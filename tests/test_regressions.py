from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# Изолируем imports тестового процесса от production delivery settings и запрещаем
# python-dotenv даже читать настоящий .env. Нужные значения тесты задают явно.
for _production_env_name in (
    "TELEGRAM_BOT_TOKEN",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "DB_PATH",
    "OWNER_CHAT_ID",
):
    os.environ.pop(_production_env_name, None)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ.update(
    {
        "TELEGRAM_BOT_TOKEN": "unit-test-token-never-used",
        "TWITCH_CLIENT_ID": "unit-test-client",
        "TWITCH_CLIENT_SECRET": "unit-test-secret",
        "DB_PATH": ":memory:",
        "OWNER_CHAT_ID": "",
    }
)

from cryptography.fernet import Fernet
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.utils.token import TokenValidationError

import main as main_module
from bot.chat_listener import ChatListener
from bot.config import ConfigError, load_config
from bot.database import Database
from bot.follow_listener import FollowEventListener
from bot.handlers.streams import (
    TWITCH_CUSTOM_EMOJI_ID,
    _build_live_list,
    _build_health_text,
    _deliver_report,
    _format_viewers,
    _message_can_manage_chat,
    _run_import_follows,
    cb_quiet_digest_response,
    cmd_health,
    cmd_report,
)
from bot.oauth import (
    OAuthCallbackServer,
    OAuthFlowError,
    OAuthTokenRevokedError,
    OAuthTokenTemporaryError,
    UserTokenResult,
    _exchange_code,
    refresh_user_token,
)
from bot.poller import (
    OFFLINE_GRACE_SECONDS,
    RESTART_MERGE_GRACE_SECONDS,
    StreamPoller,
    _FAILED,
)
from bot.report_delivery import validate_report_destination
from bot.token_store import TokenStore
from bot.twitch import (
    StreamInfo,
    TwitchAuthError,
    TwitchClient,
    TwitchPaginationError,
    TwitchRateLimitError,
    TwitchTemporaryError,
    TwitchUnauthorizedError,
    TwitchUserResponseError,
)
from main import _reconcile_telegram_channels


REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TWITCH_CLIENT_ID": "test-client",
    "TWITCH_CLIENT_SECRET": "test-secret",
}


def _stream(stream_id: str, viewers: int = 25) -> StreamInfo:
    return StreamInfo(
        user_login="channel",
        stream_id=stream_id,
        title="Test stream",
        game_name="Test game",
        viewer_count=viewers,
        started_at="2026-01-01T00:00:00Z",
    )


class _FakeHTTPResponse:
    def __init__(self, status: int, payload=None, headers=None) -> None:
        self.status = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class ConfigTests(unittest.TestCase):
    def test_poll_interval_must_be_positive(self) -> None:
        with patch.dict(os.environ, {**REQUIRED_ENV, "POLL_INTERVAL_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "POLL_INTERVAL_SECONDS.*больше нуля"):
                load_config()

    def test_invalid_owner_chat_id_is_permanent_config_error(self) -> None:
        with patch.dict(os.environ, {**REQUIRED_ENV, "OWNER_CHAT_ID": "not-an-id"}, clear=True):
            with self.assertRaisesRegex(ConfigError, "OWNER_CHAT_ID.*целым числом"):
                load_config()


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_channel_private_recipient_has_priority_over_chat_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(-100123, "channel")
                await db.set_stats_recipient(-100123, 41)
                await db.set_post_recipient(-100123, "channel", 42)

                self.assertEqual(
                    await db.resolve_post_recipient(-100123, "channel"), 42
                )
            finally:
                await db.close()

    async def test_duplicate_channel_rolls_back_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                self.assertTrue(await db.add_channel(1, "channel"))
                self.assertFalse(await db.add_channel(1, "channel"))
                self.assertFalse(db.conn.in_transaction)
            finally:
                await db.close()

    async def test_user_tokens_are_encrypted_at_rest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key = Fernet.generate_key().decode("ascii")
            db = Database(os.path.join(directory, "test.db"), token_encryption_key=key)
            await db.connect()
            try:
                await db.save_user_token("channel", "42", "access-secret", "refresh-secret", 1.0)
                cursor = await db.conn.execute(
                    "SELECT access_token, refresh_token FROM twitch_user_tokens "
                    "WHERE twitch_login = ?",
                    ("channel",),
                )
                stored_access, stored_refresh = await cursor.fetchone()
                self.assertTrue(stored_access.startswith("fernet:v1:"))
                self.assertTrue(stored_refresh.startswith("fernet:v1:"))
                self.assertNotIn("access-secret", stored_access)
                self.assertEqual(
                    await db.get_user_token("channel"),
                    ("42", "access-secret", "refresh-secret", 1.0),
                )
            finally:
                await db.close()

    async def test_existing_plaintext_tokens_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            plaintext_db = Database(path)
            await plaintext_db.connect()
            await plaintext_db.save_user_token("channel", "42", "access", "refresh", 1.0)
            await plaintext_db.close()

            encrypted_db = Database(path, token_encryption_key=Fernet.generate_key().decode("ascii"))
            await encrypted_db.connect()
            try:
                cursor = await encrypted_db.conn.execute(
                    "SELECT access_token FROM twitch_user_tokens WHERE twitch_login = 'channel'"
                )
                self.assertTrue((await cursor.fetchone())[0].startswith("fernet:v1:"))
                self.assertEqual(
                    await encrypted_db.get_user_token("channel"),
                    ("42", "access", "refresh", 1.0),
                )
            finally:
                await encrypted_db.close()

    async def test_retention_cleanup_columns_have_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                cursor = await db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND name LIKE '%_retention'"
                )
                names = {row[0] for row in await cursor.fetchall()}
                self.assertEqual(
                    names,
                    {
                        "idx_stream_samples_retention",
                        "idx_chat_activity_retention",
                        "idx_chat_unique_nicks_retention",
                        "idx_stream_chatters_retention",
                        "idx_stream_chat_meta_retention",
                        "idx_stream_history_stream_retention",
                        "idx_follow_event_counts_retention",
                        "idx_follow_event_ids_retention",
                    },
                )
            finally:
                await db.close()

    async def test_follow_events_are_counted_once_for_each_live_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.add_channel(2, "channel")
                for chat_id in (1, 2):
                    await db.set_live_state(
                        chat_id, "channel", True, "stream-1", stream_started_at="2026-01-01T00:00:00Z"
                    )
                    await db.start_follow_event_count(chat_id, "channel", "stream-1", True)

                self.assertTrue(await db.record_follow_event("channel", "event-1"))
                self.assertFalse(await db.record_follow_event("channel", "event-1"))
                self.assertEqual(
                    await db.get_follow_event_count(1, "channel", "stream-1"), (1, True)
                )
                self.assertEqual(
                    await db.get_follow_event_count(2, "channel", "stream-1"), (1, True)
                )
            finally:
                await db.close()

    async def test_follow_counter_never_becomes_reliable_again_after_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_live_state(
                    1, "channel", True, "stream-1", stream_started_at="2026-01-01T00:00:00Z"
                )
                await db.start_follow_event_count(1, "channel", "stream-1", True)
                await db.mark_live_follow_counts_unreliable("channel")
                await db.start_follow_event_count(1, "channel", "stream-1", True)

                self.assertEqual(
                    await db.get_follow_event_count(1, "channel", "stream-1"), (0, False)
                )
            finally:
                await db.close()

    async def test_live_post_cleanup_is_independent_from_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_live_state(
                    1,
                    "channel",
                    False,
                    "stream-1",
                    message_id=10,
                    title="Stream",
                    offline_since=1.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                )

                self.assertEqual(
                    await db.pending_offline_posts(),
                    [(1, "channel", 10, 1.0, False)],
                )
            finally:
                await db.close()

    async def test_last_stream_end_does_not_depend_on_report_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_live_state(
                    1,
                    "channel",
                    False,
                    "stream-1",
                    offline_since=1234.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                )

                self.assertEqual(await db.snapshot_last_stream_ends(), {})

                await db.mark_stats_sent(1, "channel")
                self.assertEqual(
                    await db.snapshot_last_stream_ends(),
                    {(1, "channel"): 1234.0},
                )
            finally:
                await db.close()

    async def test_stale_report_history_is_not_used_for_return_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.add_stream_history(
                    1, "channel", "old-stream", 100.0, 60, 10, 5, None
                )

                self.assertEqual(await db.snapshot_last_stream_ends(), {})
            finally:
                await db.close()

    async def test_finalized_session_cleanup_recovers_after_process_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_live_state(
                    1,
                    "channel",
                    False,
                    "stream-1",
                    title="Stream",
                    offline_since=1234.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                    peak_viewers=10,
                )
                await db.add_stream_history(
                    1, "channel", "stream-1", 1234.0, 60, 10, 8, None
                )
                await db.mark_stats_sent(1, "channel")

                await db.recover_finished_sessions()

                self.assertEqual(
                    await db.get_live_state(1, "channel"),
                    (False, None, None, None, None, None, None),
                )
                self.assertIsNotNone(await db.get_last_finished_stream(1, "channel"))
            finally:
                await db.close()

    async def test_group_can_never_be_report_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await db.add_channel(group_id, "channel")

                self.assertIsNone(await db.resolve_post_recipient(group_id, "channel"))

                await db.set_post_recipient(group_id, "channel", group_id)
                await db.set_stats_recipient(group_id, group_id)
                self.assertIsNone(await db.resolve_post_recipient(group_id, "channel"))

                await db.set_post_recipient(group_id, "channel", 42)
                self.assertEqual(await db.resolve_post_recipient(group_id, "channel"), 42)
            finally:
                await db.close()

    async def test_telegram_channel_keeps_its_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "News")
                await db.add_channel(channel_id, "channel")

                self.assertEqual(
                    await db.resolve_post_recipient(channel_id, "channel"), channel_id
                )
            finally:
                await db.close()

    async def test_migration_removes_old_group_report_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await db.add_channel(-100123, "channel")
            await db.set_post_recipient(-100123, "channel", -100123)
            await db.set_stats_recipient(-100123, -100123)
            await db.add_deferred_report(-100123, -100123, "channel", "stream", 1.0)
            await db.close()

            migrated = Database(path)
            await migrated.connect()
            try:
                self.assertIsNone(await migrated.get_post_recipient(-100123, "channel"))
                self.assertIsNone(await migrated.get_stats_recipient(-100123))
                self.assertFalse(await migrated.has_deferred_reports(-100123))
            finally:
                await migrated.close()

    async def test_migration_preserves_private_and_registered_channel_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            channel_id = -100456
            db = Database(path)
            await db.connect()
            await db.register_telegram_channel(channel_id, "News")
            await db.add_channel(channel_id, "channel")
            await db.set_post_recipient(channel_id, "channel", channel_id)
            await db.set_stats_recipient(channel_id, channel_id)
            await db.add_deferred_report(
                channel_id, channel_id, "channel", "stream", 1.0
            )
            await db.add_channel(-100123, "personal")
            await db.set_post_recipient(-100123, "personal", 42)
            await db.set_stats_recipient(-100123, 43)
            await db.close()

            for _ in range(2):
                migrated = Database(path)
                await migrated.connect()
                try:
                    self.assertEqual(
                        await migrated.get_post_recipient(channel_id, "channel"),
                        channel_id,
                    )
                    self.assertEqual(
                        await migrated.get_stats_recipient(channel_id), channel_id
                    )
                    self.assertTrue(await migrated.has_deferred_reports(channel_id))
                    self.assertEqual(
                        await migrated.get_post_recipient(-100123, "personal"), 42
                    )
                    self.assertEqual(
                        await migrated.get_stats_recipient(-100123), 43
                    )
                finally:
                    await migrated.close()

    async def test_deferred_reports_survive_restart_and_keep_multiple_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await db.add_deferred_report(42, -100123, "channel", "stream-1", 1.0)
            await db.add_deferred_report(42, -100123, "channel", "stream-2", 2.0)
            await db.mark_quiet_hours_digest_sent(42)
            await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                self.assertEqual(
                    await reopened.peek_deferred_reports(42),
                    [
                        (-100123, "channel", "stream-1", 1.0),
                        (-100123, "channel", "stream-2", 2.0),
                    ],
                )
                self.assertTrue(await reopened.is_quiet_hours_digest_sent(42))
            finally:
                await reopened.close()

    async def test_old_deferred_schema_is_migrated_without_losing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE deferred_reports ("
                "chat_id INTEGER NOT NULL, source_chat_id INTEGER NOT NULL, "
                "twitch_login TEXT NOT NULL, stream_id TEXT, ended_at REAL NOT NULL, "
                "PRIMARY KEY (chat_id, source_chat_id, twitch_login))"
            )
            connection.execute(
                "INSERT INTO deferred_reports VALUES (?, ?, ?, ?, ?)",
                (42, -100123, "channel", "stream-1", 1.0),
            )
            connection.commit()
            connection.close()

            db = Database(path)
            await db.connect()
            try:
                self.assertEqual(
                    await db.peek_deferred_reports(42),
                    [(-100123, "channel", "stream-1", 1.0)],
                )
                await db.add_deferred_report(
                    42, -100123, "channel", "stream-2", 2.0
                )
                self.assertEqual(len(await db.peek_deferred_reports(42)), 2)
            finally:
                await db.close()

    async def test_same_stream_is_saved_to_history_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                for _ in range(2):
                    await db.add_stream_history(
                        1, "channel", "stream-1", 1.0, 60, 10, 5, 2
                    )
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM stream_history WHERE chat_id = 1 "
                    "AND twitch_login = 'channel' AND stream_id = 'stream-1'"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
            finally:
                await db.close()


class StorageRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_raw_session(
        self,
        db: Database,
        chat_id: int,
        login: str,
        stream_id: str,
        timestamp: float,
    ) -> None:
        await db.conn.execute(
            "INSERT INTO stream_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, login, stream_id, timestamp, 10, "title", "game"),
        )
        await db.conn.execute(
            "INSERT INTO chat_activity_samples VALUES (?, ?, ?, ?, ?)",
            (chat_id, login, stream_id, timestamp, 1),
        )
        await db.conn.execute(
            "INSERT INTO chat_unique_nicks VALUES (?, ?, ?, ?, ?)",
            (chat_id, login, stream_id, "legacy-nick", timestamp),
        )
        await db.conn.execute(
            "INSERT INTO stream_chatters VALUES (?, ?, ?, ?)",
            (login, stream_id, "nick", timestamp),
        )
        await db.conn.commit()

    async def _raw_counts(
        self, db: Database, chat_id: int, login: str, stream_id: str
    ) -> tuple[int, int, int, int]:
        queries = (
            (
                "SELECT COUNT(*) FROM stream_samples WHERE chat_id = ? "
                "AND twitch_login = ? AND stream_id = ?",
                (chat_id, login, stream_id),
            ),
            (
                "SELECT COUNT(*) FROM chat_activity_samples WHERE chat_id = ? "
                "AND twitch_login = ? AND stream_id = ?",
                (chat_id, login, stream_id),
            ),
            (
                "SELECT COUNT(*) FROM chat_unique_nicks WHERE chat_id = ? "
                "AND twitch_login = ? AND stream_id = ?",
                (chat_id, login, stream_id),
            ),
            (
                "SELECT COUNT(*) FROM stream_chatters WHERE twitch_login = ? "
                "AND stream_id = ?",
                (login, stream_id),
            ),
        )
        counts = []
        for sql, params in queries:
            cursor = await db.conn.execute(sql, params)
            counts.append((await cursor.fetchone())[0])
        return tuple(counts)

    async def test_raw_retention_preserves_active_and_reconnect_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "active")
                await db.set_live_state(
                    1,
                    "active",
                    True,
                    "active-stream",
                    stream_started_at="2026-01-01T00:00:00Z",
                )
                await self._seed_raw_session(
                    db, 1, "active", "active-stream", 1.0
                )

                await db.add_channel(2, "reconnect")
                await db.set_live_state(
                    2,
                    "reconnect",
                    False,
                    "reconnect-stream",
                    offline_since=90.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                )
                await self._seed_raw_session(
                    db, 2, "reconnect", "reconnect-stream", 1.0
                )

                await db.purge_old_report_data(100.0)

                self.assertEqual(
                    await self._raw_counts(db, 1, "active", "active-stream"),
                    (1, 1, 1, 1),
                )
                self.assertEqual(
                    await self._raw_counts(db, 2, "reconnect", "reconnect-stream"),
                    (1, 1, 1, 1),
                )
            finally:
                await db.close()

    async def test_retention_is_measured_from_stream_end_for_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_stream_history(
                    1, "channel", "stream-1", 100.0, 12 * 3600, 10, 5, 1
                )
                await self._seed_raw_session(db, 1, "channel", "stream-1", 1.0)

                await db.purge_old_report_data(100.0)
                self.assertEqual(
                    await self._raw_counts(db, 1, "channel", "stream-1"),
                    (1, 1, 1, 1),
                )

                await db.purge_old_report_data(100.001)
                self.assertEqual(
                    await self._raw_counts(db, 1, "channel", "stream-1"),
                    (0, 0, 0, 0),
                )
            finally:
                await db.close()

    async def test_stream_chatter_retention_uses_stream_history_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                cursor = await db.conn.execute(
                    "EXPLAIN QUERY PLAN DELETE FROM stream_chatters AS raw "
                    "WHERE raw.first_seen_at < ? AND NOT EXISTS ("
                    "SELECT 1 FROM stream_history h "
                    "WHERE h.twitch_login = raw.twitch_login "
                    "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
                    (100.0, 100.0),
                )
                plan = " ".join(row[3] for row in await cursor.fetchall())
                self.assertIn("idx_stream_chatters_retention", plan)
                self.assertIn("idx_stream_history_stream_retention", plan)
            finally:
                await db.close()

    async def test_cancelled_retention_rolls_back_and_can_resume_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await self._seed_raw_session(db, 1, "orphan", "stream-1", 1.0)
            entered_commit = asyncio.Event()
            never = asyncio.Event()

            async def blocked_commit() -> None:
                entered_commit.set()
                await never.wait()

            try:
                with patch.object(db.conn, "commit", new=blocked_commit):
                    task = asyncio.create_task(db.purge_old_report_data(10.0))
                    await asyncio.wait_for(entered_commit.wait(), timeout=1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
            finally:
                await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                self.assertEqual(
                    await self._raw_counts(reopened, 1, "orphan", "stream-1"),
                    (1, 1, 1, 1),
                )
                await reopened.purge_old_report_data(10.0)
                self.assertEqual(
                    await self._raw_counts(reopened, 1, "orphan", "stream-1"),
                    (0, 0, 0, 0),
                )
            finally:
                await reopened.close()

    async def test_chat_removal_clears_current_state_but_preserves_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            chat_id = -100123
            try:
                await db.register_telegram_channel(chat_id, "Channel")
                await db.add_channel(chat_id, "channel")
                await db.set_stats_recipient(chat_id, 42)
                await db.set_quiet_hours(chat_id, 60, 120, 0)
                await db.set_utc_offset(chat_id, 180)
                await db.add_deferred_report(
                    chat_id, chat_id, "channel", "stream-destination", 1.0
                )
                await db.add_deferred_report(
                    42, chat_id, "channel", "stream-source", 2.0
                )
                await db.mark_quiet_hours_digest_sent(chat_id)
                await db.mark_quiet_hours_digest_sent(42)
                await db.save_stream_chat_meta(
                    chat_id, "channel", "stream-1", True, None, None, 1.0
                )
                await db.add_stream_history(
                    chat_id, "channel", "stream-1", 1.0, 60, 10, 5, 1
                )
                await db.save_vod(
                    chat_id,
                    "channel",
                    "stream-1",
                    "https://example.test/vod",
                    None,
                    "[]",
                )
                await db.create_report_delivery(
                    chat_id,
                    "channel",
                    "stream-1",
                    chat_id,
                    "brief",
                    "text",
                    None,
                    1.0,
                )

                self.assertEqual(await db.remove_all_channels(chat_id), 1)

                for table in (
                    "tracked_channels",
                    "telegram_channels",
                    "stats_recipients",
                    "quiet_hours",
                    "user_timezones",
                    "stream_chat_meta",
                    "deferred_reports",
                    "quiet_hours_digest_sent",
                ):
                    cursor = await db.conn.execute(f"SELECT COUNT(*) FROM {table}")
                    self.assertEqual((await cursor.fetchone())[0], 0, table)
                self.assertIsNotNone(
                    await db.get_finished_stream(chat_id, "channel", "stream-1")
                )
                self.assertIsNotNone(
                    await db.get_vod(chat_id, "channel", "stream-1")
                )
                self.assertIsNotNone(
                    await db.get_report_delivery_for_stream(
                        chat_id, "channel", "stream-1"
                    )
                )
            finally:
                await db.close()


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_state_accepts_callback_before_wait_starts(self) -> None:
        server = OAuthCallbackServer("https://example.test/twitch/callback", "127.0.0.1", 0)
        server.register_state("state")

        response = await server._handle_callback(
            SimpleNamespace(query={"state": "state", "code": "oauth-code"})
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(await server.wait_for_code("state", timeout=1), "oauth-code")


class GroupPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_group_member_cannot_manage_channels(self) -> None:
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            from_user=SimpleNamespace(id=10),
            sender_chat=None,
            bot=bot,
        )

        self.assertFalse(await _message_can_manage_chat(message))

    async def test_group_administrator_can_manage_channels(self) -> None:
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator"))
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            from_user=SimpleNamespace(id=10),
            sender_chat=None,
            bot=bot,
        )

        self.assertTrue(await _message_can_manage_chat(message))


class LiveListTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_list_removes_mentions_and_promo_links(self) -> None:
        db = SimpleNamespace(
            list_live_channels=AsyncMock(
                return_value=[
                    ("small", "Играем @friend", 2, "Game & Fun"),
                    ("large", "🔴 Большой эфир tg: t.me/example 🔴", 1200, "IRL"),
                ]
            )
        )

        text, keyboard = await _build_live_list(1, db)

        self.assertNotIn("@friend", text)
        self.assertNotIn("t.me", text)
        self.assertNotIn("🔴 Большой эфир", text)
        self.assertIn(
            '<blockquote><b><a href="https://twitch.tv/large">large</a></b>\n'
            "🎮 IRL  ·  👁 1 200 зрителей\nБольшой эфир</blockquote>",
            text,
        )
        self.assertIn("🤝 Вместе: <a href=\"https://www.twitch.tv/friend\">friend</a>", text)
        self.assertIn("🎮 Game &amp; Fun", text)
        self.assertLess(text.index(">large</a>"), text.index(">small</a>"))
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "▶ large")

    async def test_viewer_word_form(self) -> None:
        self.assertEqual(_format_viewers(1), "1 зритель")
        self.assertEqual(_format_viewers(22), "22 зрителя")
        self.assertEqual(_format_viewers(111), "111 зрителей")
        self.assertEqual(_format_viewers(1855), "1 855 зрителей")

    async def test_live_list_can_use_twitch_custom_emoji_with_text_fallback(self) -> None:
        db = SimpleNamespace(
            list_live_channels=AsyncMock(
                return_value=[("channel", "Стрим", 10, "IRL")]
            )
        )

        text, keyboard = await _build_live_list(1, db, custom_emoji=True)

        self.assertIn(
            f'<tg-emoji emoji-id="{TWITCH_CUSTOM_EMOJI_ID}">🎮</tg-emoji>', text
        )
        self.assertEqual(
            keyboard.inline_keyboard[0][0].icon_custom_emoji_id,
            TWITCH_CUSTOM_EMOJI_ID,
        )


class PollerCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_post_cleanup_wait_is_five_minutes(self) -> None:
        self.assertEqual(RESTART_MERGE_GRACE_SECONDS, 30 * 60)
        self.assertEqual(OFFLINE_GRACE_SECONDS, 5 * 60)

    async def test_transient_delete_failure_keeps_message_for_retry(self) -> None:
        db = SimpleNamespace(
            pending_offline_posts=AsyncMock(return_value=[(1, "channel", 10, 0.0, False)]),
            clear_live_message=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=_FAILED)

        await poller._cleanup_offline_posts()

        db.clear_live_message.assert_not_awaited()

    async def test_live_post_remains_before_five_minutes(self) -> None:
        now = time.time()
        db = SimpleNamespace(
            pending_offline_posts=AsyncMock(
                return_value=[(1, "channel", 10, now - OFFLINE_GRACE_SECONDS + 1, False)]
            ),
            clear_live_message=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=None)

        await poller._cleanup_offline_posts()

        poller._tg_call.assert_not_awaited()
        db.clear_live_message.assert_not_awaited()

    async def test_successful_delete_clears_message_state(self) -> None:
        db = SimpleNamespace(
            pending_offline_posts=AsyncMock(return_value=[(1, "channel", 10, 1000.0, False)]),
            clear_live_message=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=None)

        with patch(
            "bot.poller.time.time", return_value=1000.0 + OFFLINE_GRACE_SECONDS
        ):
            await poller._cleanup_offline_posts()

        db.clear_live_message.assert_awaited_once_with(1, "channel")
        db.clear_finished_session.assert_not_awaited()


class LogicalStreamSessionTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_offline(
        self,
        db: Database,
        *,
        offline_since: float,
        message_id: int | None = 10,
    ) -> None:
        await db.add_channel(1, "channel")
        await db.set_live_state(
            1,
            "channel",
            True,
            "session-1",
            message_id=message_id,
            title="First part",
            stream_started_at="2026-01-01T00:00:00Z",
        )
        await db.record_viewer_sample(1, "channel", 100)
        await db.set_followers_at_start(1, "channel", 500)
        await db.set_live_state(
            1,
            "channel",
            False,
            "session-1",
            message_id=message_id,
            title="First part",
            offline_since=offline_since,
            stream_started_at="2026-01-01T00:00:00Z",
            peak_viewers=100,
        )

    def _poller(self, db: Database, stream: StreamInfo | None) -> StreamPoller:
        twitch = SimpleNamespace(
            get_live_streams=AsyncMock(
                return_value={} if stream is None else {"channel": stream}
            )
        )
        bot = SimpleNamespace(delete_message=AsyncMock())
        poller = StreamPoller(bot, db, twitch, 60)
        poller._notify = AsyncMock(return_value=99)
        poller._edit = AsyncMock(return_value=True)
        poller._maybe_snapshot_followers = AsyncMock()
        return poller

    async def test_deleting_post_preserves_stream_session_and_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_offline(db, offline_since=1000.0)
                await db.clear_live_message(1, "channel")
                cursor = await db.conn.execute(
                    "SELECT last_message_id, last_stream_id, stream_started_at, "
                    "peak_viewers, viewer_sum, viewer_samples, followers_at_start, "
                    "offline_since FROM tracked_channels "
                    "WHERE chat_id = 1 AND twitch_login = 'channel'"
                )
                self.assertEqual(
                    await cursor.fetchone(),
                    (None, "session-1", "2026-01-01T00:00:00Z", 100, 100, 1, 500, 1000.0),
                )
            finally:
                await db.close()

    async def test_same_stream_reconnect_after_one_minute_edits_existing_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_offline(db, offline_since=1000.0)
                poller = self._poller(db, _stream("session-1"))

                with patch("bot.poller.time.time", return_value=1060.0):
                    await poller._check_streams()

                poller._edit.assert_awaited_once()
                poller._notify.assert_not_awaited()
                state = await db.get_live_state(1, "channel")
                self.assertTrue(state[0])
                self.assertEqual(state[1], "session-1")
            finally:
                await db.close()

    async def _assert_silent_reconnect(self, delay_minutes: int) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_offline(db, offline_since=1000.0)
                await db.clear_live_message(1, "channel")
                poller = self._poller(db, _stream("twitch-new-id", viewers=40))

                with patch(
                    "bot.poller.time.time", return_value=1000.0 + delay_minutes * 60
                ):
                    await poller._check_streams()

                poller._notify.assert_awaited_once()
                self.assertTrue(poller._notify.await_args.kwargs["silent"])
                state = await db.get_live_state(1, "channel")
                self.assertEqual(state[1], "session-1")
                self.assertEqual(state[2], 99)
                self.assertEqual(state[5], "2026-01-01T00:00:00Z")
                cursor = await db.conn.execute(
                    "SELECT viewer_sum, viewer_samples, peak_viewers, followers_at_start "
                    "FROM tracked_channels WHERE chat_id = 1 AND twitch_login = 'channel'"
                )
                self.assertEqual(await cursor.fetchone(), (140, 2, 100, 500))
            finally:
                await db.close()

    async def test_new_twitch_id_after_seven_minutes_is_same_silent_session(self) -> None:
        await self._assert_silent_reconnect(7)

    async def test_reconnect_after_twenty_minutes_is_same_silent_session(self) -> None:
        await self._assert_silent_reconnect(20)

    async def test_multiple_short_outages_do_not_duplicate_notifications_or_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_live_state(
                    1, "channel", True, "session-1", message_id=10,
                    title="First", stream_started_at="2026-01-01T00:00:00Z",
                )
                poller = self._poller(db, None)

                with patch("bot.poller.time.time", return_value=1000.0):
                    await poller._check_streams()
                poller._twitch.get_live_streams.return_value = {"channel": _stream("part-2")}
                with patch("bot.poller.time.time", return_value=1060.0):
                    await poller._check_streams()
                poller._twitch.get_live_streams.return_value = {}
                with patch("bot.poller.time.time", return_value=1100.0):
                    await poller._check_streams()
                poller._twitch.get_live_streams.return_value = {"channel": _stream("part-3")}
                with patch("bot.poller.time.time", return_value=1160.0):
                    await poller._check_streams()

                poller._notify.assert_not_awaited()
                self.assertEqual(poller._edit.await_count, 2)
                self.assertEqual((await db.get_live_state(1, "channel"))[1], "session-1")
                self.assertEqual(await db.pending_stats(), [])
            finally:
                await db.close()

    async def test_reconnect_is_isolated_across_multiple_telegram_chats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                for chat_id, message_id, viewers in ((1, 11, 10), (2, None, 20)):
                    await db.add_channel(chat_id, "channel")
                    await db.set_live_state(
                        chat_id,
                        "channel",
                        True,
                        "session-1",
                        message_id=message_id,
                        title="First",
                        stream_started_at="2026-01-01T00:00:00Z",
                    )
                    await db.record_viewer_sample(chat_id, "channel", viewers)
                    await db.start_follow_event_count(
                        chat_id, "channel", "session-1", False
                    )
                    await db.set_live_state(
                        chat_id,
                        "channel",
                        False,
                        "session-1",
                        message_id=message_id,
                        title="First",
                        offline_since=1000.0,
                        stream_started_at="2026-01-01T00:00:00Z",
                        peak_viewers=viewers,
                    )
                await db.set_notify_enabled(2, "channel", False)
                await db.clear_live_message(1, "channel")

                poller = self._poller(db, _stream("new-twitch-id", viewers=40))
                poller._follow_listener = SimpleNamespace(
                    is_configured=lambda login: True,
                    covers_stream_start=lambda login, started_at: True,
                )

                with patch("bot.poller.time.time", return_value=1420.0):
                    await poller._check_streams()

                poller._notify.assert_awaited_once()
                self.assertEqual(poller._notify.await_args.args[0], 1)
                self.assertTrue(poller._notify.await_args.kwargs["silent"])
                for chat_id, expected_sum in ((1, 50), (2, 60)):
                    state = await db.get_live_state(chat_id, "channel")
                    self.assertTrue(state[0])
                    self.assertEqual(state[1], "session-1")
                    cursor = await db.conn.execute(
                        "SELECT viewer_sum, viewer_samples FROM tracked_channels "
                        "WHERE chat_id = ? AND twitch_login = 'channel'",
                        (chat_id,),
                    )
                    self.assertEqual(await cursor.fetchone(), (expected_sum, 2))
                    self.assertEqual(
                        await db.get_follow_event_count(
                            chat_id, "channel", "session-1"
                        ),
                        (0, False),
                    )
                    self.assertIsNone(
                        await db.get_follow_event_count(
                            chat_id, "channel", "new-twitch-id"
                        )
                    )
            finally:
                await db.close()

    async def test_report_waits_thirty_minutes_then_finalizes_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_offline(db, offline_since=1000.0)
                await db.clear_live_message(1, "channel")
                listener = SimpleNamespace(
                    is_running=lambda login: True,
                    get_and_clear_activity=lambda login: [],
                    get_and_clear_chatters=lambda login: set(),
                    get_and_clear_unique_viewers=lambda login: [],
                    get_and_clear_join_reliability=lambda login: True,
                    chatters_overflowed=lambda login: False,
                    get_and_clear_top_chatters=lambda login: [],
                    get_and_clear_raid_events=lambda login: [],
                    stop=AsyncMock(),
                )
                poller = self._poller(db, None)
                poller._chat_listener = listener
                poller._send_stats = AsyncMock(return_value=True)

                with patch("bot.poller.time.time", return_value=2799.0):
                    await poller._send_pending_stats()
                poller._send_stats.assert_not_awaited()
                listener.stop.assert_not_awaited()

                with patch("bot.poller.time.time", return_value=2800.0):
                    await poller._send_pending_stats()
                    await poller._send_pending_stats()

                poller._send_stats.assert_awaited_once()
                listener.stop.assert_awaited_once_with("channel")
                state = await db.get_live_state(1, "channel")
                self.assertEqual(state, (False, None, None, None, None, None, None))
            finally:
                await db.close()

    async def test_new_stream_after_finalization_notifies_and_starts_fresh_stats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_offline(db, offline_since=1000.0)
                await db.clear_live_message(1, "channel")
                finalizer = self._poller(db, None)
                finalizer._send_stats = AsyncMock(return_value=True)
                with patch("bot.poller.time.time", return_value=2801.0):
                    await finalizer._send_pending_stats()

                poller = self._poller(db, _stream("session-2", viewers=30))
                with patch("bot.poller.time.time", return_value=2900.0):
                    await poller._check_streams()

                poller._notify.assert_awaited_once()
                self.assertFalse(poller._notify.await_args.kwargs.get("silent", False))
                self.assertEqual((await db.get_live_state(1, "channel"))[1], "session-2")
                cursor = await db.conn.execute(
                    "SELECT viewer_sum, viewer_samples FROM tracked_channels "
                    "WHERE chat_id = 1 AND twitch_login = 'channel'"
                )
                self.assertEqual(await cursor.fetchone(), (30, 1))
            finally:
                await db.close()


class TelegramChannelReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_channel_participates_in_chat_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            channel_id = -100456
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.register_telegram_channel(channel_id, "News")
                listener = SimpleNamespace(
                    is_running=lambda login: True,
                    get_and_clear_activity=lambda login: [(1.0, 3)],
                    get_and_clear_chatters=lambda login: [("alice", 1.0)],
                    get_and_clear_unique_viewers=lambda login: [],
                    get_and_clear_join_reliability=lambda login: True,
                    chatters_overflowed=lambda login: False,
                    get_and_clear_top_chatters=lambda login: [("alice", 3)],
                    get_and_clear_raid_events=lambda login: [],
                    stop=AsyncMock(),
                )
                poller = StreamPoller(
                    SimpleNamespace(),
                    db,
                    SimpleNamespace(),
                    60,
                    chat_listener=listener,
                )

                await poller._finish_chat_collection(
                    "channel", [(channel_id, "stream-1")]
                )

                self.assertEqual(
                    await db.get_chat_unique_nicks(
                        channel_id, "channel", "stream-1"
                    ),
                    [("alice", 1.0)],
                )
                self.assertEqual(
                    await db.get_chat_activity_samples(
                        channel_id, "channel", "stream-1"
                    ),
                    [(1.0, 3)],
                )
                self.assertIsNotNone(
                    await db.take_stream_chat_meta(
                        channel_id, "channel", "stream-1"
                    )
                )
            finally:
                await db.close()

    async def test_registered_channel_receives_text_html_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            channel_id = -100456
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.register_telegram_channel(channel_id, "News")
                await db.add_channel(channel_id, "channel")
                await db.set_live_state(
                    channel_id,
                    "channel",
                    False,
                    "stream-1",
                    title="Stream",
                    offline_since=1000.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                    peak_viewers=10,
                )
                bot = SimpleNamespace(
                    send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
                    send_document=AsyncMock(return_value=SimpleNamespace(message_id=2)),
                )
                twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
                poller = StreamPoller(bot, db, twitch, 60)
                poller._fetch_top_clips = AsyncMock(return_value=[])
                poller._fetch_and_save_vod = AsyncMock(return_value=None)

                with patch("bot.poller.time.time", return_value=2801.0):
                    await poller._send_pending_stats()

                self.assertEqual(bot.send_message.await_args.args[0], channel_id)
                self.assertEqual(bot.send_document.await_args.args[0], channel_id)
                self.assertIsNotNone(
                    await db.get_finished_stream(channel_id, "channel", "stream-1")
                )
                delivery = await db.get_report_delivery(
                    channel_id, "channel", "stream-1", channel_id
                )
                self.assertIsNotNone(delivery)
                self.assertTrue(delivery.complete)
                self.assertEqual(await db.pending_stats(), [])
            finally:
                await db.close()

    async def test_channel_stream_generates_and_marks_final_report(self) -> None:
        db = SimpleNamespace(
            pending_report_deliveries=AsyncMock(return_value=[]),
            pending_stats=AsyncMock(
                return_value=[
                    (
                        1, "channel", 0.0, "Stream", "2026-01-01T00:00:00Z",
                        10, 20, 2, "stream-1", None,
                    )
                ]
            ),
            get_report_delivery_for_stream=AsyncMock(return_value=None),
            resolve_post_recipient=AsyncMock(return_value=1),
            get_quiet_hours_exempt=AsyncMock(return_value=False),
            get_quiet_hours=AsyncMock(return_value=None),
            mark_stats_sent=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._send_stats = AsyncMock(return_value=True)

        await poller._send_pending_stats()

        poller._send_stats.assert_awaited_once()
        db.mark_stats_sent.assert_awaited_once_with(1, "channel")
        db.clear_finished_session.assert_awaited_once_with(1, "channel")

    async def test_failed_channel_report_stays_pending(self) -> None:
        db = SimpleNamespace(
            pending_report_deliveries=AsyncMock(return_value=[]),
            pending_stats=AsyncMock(
                return_value=[
                    (
                        1, "channel", 0.0, "Stream", "2026-01-01T00:00:00Z",
                        10, 20, 2, "stream-1", None,
                    )
                ]
            ),
            get_report_delivery_for_stream=AsyncMock(return_value=None),
            resolve_post_recipient=AsyncMock(return_value=1),
            get_quiet_hours_exempt=AsyncMock(return_value=False),
            get_quiet_hours=AsyncMock(return_value=None),
            mark_stats_sent=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._send_stats = AsyncMock(return_value=False)

        await poller._send_pending_stats()

        db.mark_stats_sent.assert_not_awaited()


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_group_session(self, db: Database, group_id: int) -> None:
        await db.add_channel(group_id, "channel")
        await db.set_live_state(
            group_id,
            "channel",
            False,
            "stream",
            title="title",
            offline_since=1000.0,
            stream_started_at="2026-01-01T00:00:00Z",
            peak_viewers=10,
        )

    async def test_group_without_private_recipient_saves_history_without_sending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_group_session(db, group_id)
                bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
                twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
                poller = StreamPoller(bot, db, twitch, 60)

                with patch("bot.poller.time.time", return_value=2801.0):
                    await poller._send_pending_stats()

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                self.assertIsNotNone(await db.get_last_finished_stream(group_id, "channel"))
                self.assertEqual(await db.pending_stats(), [])
            finally:
                await db.close()

    async def test_group_report_is_sent_to_linked_private_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_group_session(db, group_id)
                await db.set_post_recipient(group_id, "channel", 42)
                await db.set_report_format(group_id, "channel", "brief")
                bot = SimpleNamespace(
                    send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
                    send_document=AsyncMock(),
                )
                twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
                poller = StreamPoller(bot, db, twitch, 60)

                with patch("bot.poller.time.time", return_value=2801.0):
                    await poller._send_pending_stats()

                bot.send_message.assert_awaited_once()
                self.assertEqual(bot.send_message.await_args.args[0], 42)
                bot.send_document.assert_not_awaited()
            finally:
                await db.close()

    async def test_group_full_report_text_and_html_go_only_to_private_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_group_session(db, group_id)
                await db.set_post_recipient(group_id, "channel", 42)
                bot = SimpleNamespace(
                    send_message=AsyncMock(
                        return_value=SimpleNamespace(message_id=1)
                    ),
                    send_document=AsyncMock(
                        return_value=SimpleNamespace(message_id=2)
                    ),
                )
                twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
                poller = StreamPoller(bot, db, twitch, 60)
                poller._fetch_top_clips = AsyncMock(return_value=[])
                poller._fetch_and_save_vod = AsyncMock(return_value=None)

                with patch("bot.poller.time.time", return_value=2801.0):
                    await poller._send_pending_stats()

                self.assertEqual(bot.send_message.await_args.args[0], 42)
                self.assertEqual(bot.send_document.await_args.args[0], 42)
                self.assertNotEqual(bot.send_message.await_args.args[0], group_id)
                self.assertNotEqual(bot.send_document.await_args.args[0], group_id)
                delivery = await db.get_report_delivery(
                    group_id, "channel", "stream", 42
                )
                self.assertIsNotNone(delivery)
                self.assertTrue(delivery.complete)
            finally:
                await db.close()

    async def test_group_without_private_recipient_never_delivers_report(self) -> None:
        db = SimpleNamespace(
            pending_report_deliveries=AsyncMock(return_value=[]),
            pending_stats=AsyncMock(
                return_value=[
                    (-100, "channel", 0.0, "title", "2026-01-01T00:00:00Z", 10, 20, 2, "stream", None)
                ]
            ),
            get_report_delivery_for_stream=AsyncMock(return_value=None),
            is_telegram_channel=AsyncMock(return_value=False),
            resolve_post_recipient=AsyncMock(return_value=None),
            get_quiet_hours_exempt=AsyncMock(return_value=False),
            mark_stats_sent=AsyncMock(),
            clear_finished_session=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._send_stats = AsyncMock(return_value=True)

        await poller._send_pending_stats()

        self.assertFalse(poller._send_stats.await_args.kwargs["deliver"])
        db.mark_stats_sent.assert_awaited_once_with(-100, "channel")

    async def test_failed_quiet_hours_digest_is_reported_as_not_sent(self) -> None:
        db = SimpleNamespace(
            peek_deferred_reports=AsyncMock(return_value=[(1, "channel", "stream", 0.0)])
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=_FAILED)

        self.assertFalse(await poller._send_quiet_hours_digest(1))

    async def test_successful_quiet_hours_digest_is_reported_as_sent(self) -> None:
        db = SimpleNamespace(
            peek_deferred_reports=AsyncMock(return_value=[(1, "channel", "stream", 0.0)])
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=SimpleNamespace())

        self.assertTrue(await poller._send_quiet_hours_digest(1))

    async def test_permanent_digest_failure_stops_retry_but_preserves_history_path(self) -> None:
        db = SimpleNamespace(
            peek_deferred_reports=AsyncMock(
                return_value=[(1, "channel", "stream", 0.0)]
            ),
            get_and_clear_deferred_reports=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=None)

        self.assertFalse(await poller._send_quiet_hours_digest(1))
        db.get_and_clear_deferred_reports.assert_awaited_once_with(1)

    async def test_permanent_report_failure_finalizes_without_minute_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.add_channel(1, "channel")
                await db.set_report_format(1, "channel", "brief")
                await db.set_live_state(
                    1,
                    "channel",
                    False,
                    "stream-1",
                    title="Stream",
                    offline_since=1000.0,
                    stream_started_at="2026-01-01T00:00:00Z",
                    peak_viewers=10,
                )
                twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
                poller = StreamPoller(SimpleNamespace(), db, twitch, 60)
                poller._tg_call = AsyncMock(return_value=None)
                poller._fetch_top_clips = AsyncMock(return_value=[])
                poller._fetch_and_save_vod = AsyncMock(return_value=None)

                with patch("bot.poller.time.time", return_value=2801.0):
                    await poller._send_pending_stats()

                self.assertEqual(await db.pending_stats(), [])
                self.assertIsNotNone(
                    await db.get_finished_stream(1, "channel", "stream-1")
                )
            finally:
                await db.close()

    async def test_deferred_report_moves_to_new_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await db.add_channel(group_id, "channel")
                await db.set_post_recipient(group_id, "channel", 42)
                await db.add_deferred_report(
                    42, group_id, "channel", "stream-1", 1.0
                )
                await db.set_post_recipient(group_id, "channel", 43)
                poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)

                await poller._reconcile_deferred_recipient(42)

                self.assertFalse(await db.has_deferred_reports(42))
                self.assertEqual(
                    await db.peek_deferred_reports(43),
                    [(group_id, "channel", "stream-1", 1.0)],
                )
            finally:
                await db.close()


class PersistentReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_session(
        self,
        db: Database,
        *,
        source_chat_id: int = 1,
        stream_id: str = "stream-1",
        report_format: str = "full",
        recipient_chat_id: int | None = None,
        telegram_channel: bool = False,
    ) -> None:
        if telegram_channel:
            await db.register_telegram_channel(source_chat_id, "News")
        await db.add_channel(source_chat_id, "channel")
        if recipient_chat_id is not None and recipient_chat_id != source_chat_id:
            await db.set_post_recipient(
                source_chat_id, "channel", recipient_chat_id
            )
        await db.set_report_format(source_chat_id, "channel", report_format)
        await db.set_live_state(
            source_chat_id,
            "channel",
            False,
            stream_id,
            title="Stream",
            offline_since=1000.0,
            stream_started_at="2026-01-01T00:00:00Z",
            peak_viewers=10,
        )

    def _poller(self, db: Database) -> tuple[StreamPoller, SimpleNamespace]:
        bot = SimpleNamespace(
            send_message=AsyncMock(
                return_value=SimpleNamespace(message_id=1)
            ),
            send_document=AsyncMock(
                return_value=SimpleNamespace(message_id=2)
            ),
        )
        twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
        poller = StreamPoller(bot, db, twitch, 60)
        poller._fetch_top_clips = AsyncMock(return_value=[])
        poller._fetch_and_save_vod = AsyncMock(return_value=None)
        return poller, bot

    async def _run_pending(self, poller: StreamPoller) -> None:
        with patch("bot.poller.time.time", return_value=2801.0):
            await poller._send_pending_stats()

    async def test_full_success_persists_complete_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db)
                poller, bot = self._poller(db)

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertIsNotNone(delivery)
                self.assertTrue(delivery.text_sent)
                self.assertTrue(delivery.html_sent)
                self.assertTrue(delivery.complete)
                self.assertFalse(delivery.terminal_failed)
                bot.send_message.assert_awaited_once()
                bot.send_document.assert_awaited_once()
            finally:
                await db.close()

    async def test_text_temporary_failure_keeps_both_parts_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db)
                poller, _bot = self._poller(db)
                poller._tg_call = AsyncMock(return_value=_FAILED)

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertFalse(delivery.text_sent)
                self.assertFalse(delivery.html_sent)
                self.assertFalse(delivery.terminal_failed)
                self.assertEqual(poller._tg_call.await_count, 1)
                self.assertNotIn("HTML", poller._tg_call.await_args.args[1])

                poller._tg_call = AsyncMock(
                    side_effect=[SimpleNamespace(), SimpleNamespace()]
                )
                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.complete)
                self.assertEqual(poller._tg_call.await_count, 2)
            finally:
                await db.close()

    async def test_html_temporary_failure_retries_only_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db)
                poller, _bot = self._poller(db)
                poller._tg_call = AsyncMock(
                    side_effect=[SimpleNamespace(), _FAILED]
                )

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.text_sent)
                self.assertFalse(delivery.html_sent)
                self.assertFalse(delivery.terminal_failed)
                self.assertNotEqual(await db.pending_stats(), [])
                persisted_html = delivery.html_payload

                poller._tg_call = AsyncMock(return_value=SimpleNamespace())
                poller._is_recipient_in_quiet_hours = AsyncMock(
                    return_value=True
                )
                with patch(
                    "bot.poller.build_report_html",
                    side_effect=AssertionError("HTML must not be rebuilt"),
                ):
                    await self._run_pending(poller)

                self.assertEqual(poller._tg_call.await_count, 1)
                self.assertIn("HTML", poller._tg_call.await_args.args[1])
                poller._is_recipient_in_quiet_hours.assert_not_awaited()
                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.complete)
                self.assertEqual(delivery.html_payload, persisted_html)
            finally:
                await db.close()

    async def test_restart_after_html_failure_retries_only_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await self._seed_session(db)
            poller, _bot = self._poller(db)
            poller._tg_call = AsyncMock(
                side_effect=[SimpleNamespace(), _FAILED]
            )
            await self._run_pending(poller)
            await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                retry_poller, _retry_bot = self._poller(reopened)
                retry_poller._tg_call = AsyncMock(return_value=SimpleNamespace())

                await self._run_pending(retry_poller)

                self.assertEqual(retry_poller._tg_call.await_count, 1)
                self.assertIn("HTML", retry_poller._tg_call.await_args.args[1])
                delivery = await reopened.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.complete)
            finally:
                await reopened.close()

    async def test_complete_delivery_after_restart_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await self._seed_session(db)
            poller, _bot = self._poller(db)
            with patch("bot.poller.time.time", return_value=2801.0):
                self.assertTrue(
                    await poller._send_stats(
                        1,
                        "channel",
                        "stream-1",
                        "Stream",
                        "2026-01-01T00:00:00Z",
                        10,
                        20,
                        2,
                        None,
                        ended_at=1000.0,
                    )
                )
            cursor = await db.conn.execute("SELECT COUNT(*) FROM stream_history")
            self.assertEqual((await cursor.fetchone())[0], 1)
            await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                retry_poller, _retry_bot = self._poller(reopened)
                retry_poller._tg_call = AsyncMock()

                await self._run_pending(retry_poller)

                retry_poller._tg_call.assert_not_awaited()
                self.assertEqual(await reopened.pending_stats(), [])
                cursor = await reopened.conn.execute(
                    "SELECT COUNT(*) FROM stream_history"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
            finally:
                await reopened.close()

    async def test_text_pending_after_restart_retries_text_then_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await self._seed_session(db)
            await db.add_stream_history(
                1, "channel", "stream-1", 1000.0, 60, 10, 5, 1
            )
            await db.create_report_delivery(
                1,
                "channel",
                "stream-1",
                1,
                "full",
                "persisted text",
                "<html>persisted</html>",
                1000.0,
            )
            await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                retry_poller, _retry_bot = self._poller(reopened)
                retry_poller._tg_call = AsyncMock(
                    side_effect=[SimpleNamespace(), SimpleNamespace()]
                )

                await self._run_pending(retry_poller)

                self.assertEqual(retry_poller._tg_call.await_count, 2)
                self.assertNotIn(
                    "HTML", retry_poller._tg_call.await_args_list[0].args[1]
                )
                self.assertIn(
                    "HTML", retry_poller._tg_call.await_args_list[1].args[1]
                )
                delivery = await reopened.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.complete)
            finally:
                await reopened.close()

    async def test_permanent_text_failure_is_terminal_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db)
                poller, _bot = self._poller(db)
                poller._tg_call = AsyncMock(return_value=None)

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.terminal_failed)
                self.assertEqual(
                    delivery.terminal_reason, "text_permanent_failure"
                )
                self.assertIsNotNone(
                    await db.get_finished_stream(1, "channel", "stream-1")
                )
                self.assertEqual(await db.pending_stats(), [])

                poller._tg_call.reset_mock()
                await self._run_pending(poller)
                poller._tg_call.assert_not_awaited()
            finally:
                await db.close()

    async def test_terminal_delivery_after_restart_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            db = Database(path)
            await db.connect()
            await self._seed_session(db, report_format="brief")
            await db.add_stream_history(
                1, "channel", "stream-1", 1000.0, 60, 10, 5, 1
            )
            delivery = await db.create_report_delivery(
                1,
                "channel",
                "stream-1",
                1,
                "brief",
                "persisted text",
                None,
                1000.0,
            )
            await db.mark_report_delivery_terminal(
                delivery, "text_permanent_failure", 1001.0
            )
            await db.close()

            reopened = Database(path)
            await reopened.connect()
            try:
                retry_poller, _retry_bot = self._poller(reopened)
                retry_poller._tg_call = AsyncMock()

                await self._run_pending(retry_poller)

                retry_poller._tg_call.assert_not_awaited()
                self.assertEqual(await reopened.pending_stats(), [])
                cursor = await reopened.conn.execute(
                    "SELECT COUNT(*) FROM stream_history"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
                next_delivery = await reopened.create_report_delivery(
                    1,
                    "channel",
                    "stream-2",
                    1,
                    "brief",
                    "next stream",
                    None,
                    2000.0,
                )
                self.assertFalse(next_delivery.terminal_failed)
            finally:
                await reopened.close()

    async def test_permanent_html_failure_is_terminal_and_manual_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db)
                poller, _bot = self._poller(db)
                poller._tg_call = AsyncMock(
                    side_effect=[SimpleNamespace(), None]
                )

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.text_sent)
                self.assertFalse(delivery.html_sent)
                self.assertTrue(delivery.terminal_failed)
                self.assertEqual(
                    delivery.terminal_reason, "html_permanent_failure"
                )

                manual_bot = SimpleNamespace(
                    send_message=AsyncMock(), send_document=AsyncMock()
                )
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1), bot=manual_bot
                )
                with patch("bot.handlers.streams.time.time", return_value=1001.0):
                    self.assertTrue(
                        await _deliver_report(
                            message,
                            1,
                            "channel",
                            db,
                            recipient_chat_id=1,
                            stream_id="stream-1",
                        )
                    )
                manual_bot.send_message.assert_awaited_once()
                manual_bot.send_document.assert_awaited_once()
                unchanged = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(unchanged.terminal_failed)
            finally:
                await db.close()

    async def test_brief_text_success_is_complete_without_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db, report_format="brief")
                poller, bot = self._poller(db)

                await self._run_pending(poller)

                delivery = await db.get_report_delivery(
                    1, "channel", "stream-1", 1
                )
                self.assertTrue(delivery.complete)
                self.assertIsNone(delivery.html_payload)
                bot.send_message.assert_awaited_once()
                bot.send_document.assert_not_awaited()
            finally:
                await db.close()

    async def test_stream_ids_have_independent_delivery_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                for stream_id in ("stream-1", "stream-2"):
                    await db.create_report_delivery(
                        1,
                        "channel",
                        stream_id,
                        1,
                        "brief",
                        stream_id,
                        None,
                        1.0,
                    )
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM report_deliveries"
                )
                self.assertEqual((await cursor.fetchone())[0], 2)
            finally:
                await db.close()

    async def test_same_stream_in_two_source_chats_has_independent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                for source_chat_id in (-1001, -1002):
                    await db.create_report_delivery(
                        source_chat_id,
                        "channel",
                        "stream-1",
                        42,
                        "brief",
                        str(source_chat_id),
                        None,
                        1.0,
                    )
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM report_deliveries"
                )
                self.assertEqual((await cursor.fetchone())[0], 2)
                cursor = await db.conn.execute(
                    "SELECT source_chat_id, text_payload FROM report_deliveries"
                )
                self.assertEqual(
                    dict(await cursor.fetchall()),
                    {-1001: "-1001", -1002: "-1002"},
                )
            finally:
                await db.close()

    async def test_history_and_delivery_commit_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db, report_format="brief")
                await db.conn.executescript(
                    "CREATE TRIGGER fail_report_delivery "
                    "BEFORE INSERT ON report_deliveries BEGIN "
                    "SELECT RAISE(ABORT, 'simulated crash boundary'); END;"
                )
                await db.conn.commit()
                poller, bot = self._poller(db)

                with self.assertRaises(sqlite3.IntegrityError):
                    await poller._send_stats(
                        1, "channel", "stream-1", "Stream",
                        "2026-01-01T00:00:00Z", 10, 20, 2, None,
                        ended_at=1000.0,
                    )

                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM stream_history"
                )
                self.assertEqual((await cursor.fetchone())[0], 0)
                bot.send_message.assert_not_awaited()

                await db.conn.execute("DROP TRIGGER fail_report_delivery")
                await db.conn.commit()
                self.assertTrue(
                    await poller._send_stats(
                        1, "channel", "stream-1", "Stream",
                        "2026-01-01T00:00:00Z", 10, 20, 2, None,
                        ended_at=1000.0,
                    )
                )
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM stream_history"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
                delivery = await db.get_report_delivery_for_stream(
                    1, "channel", "stream-1"
                )
                self.assertTrue(delivery.complete)
                self.assertNotIn("от среднего по прошлым", delivery.text_payload)
            finally:
                await db.close()

    async def test_recipient_is_frozen_after_delivery_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_session(
                    db,
                    source_chat_id=group_id,
                    report_format="brief",
                    recipient_chat_id=41,
                )
                await db.add_stream_history(
                    group_id, "channel", "stream-1", 1000.0, 60, 10, 5, 1
                )
                first = await db.create_report_delivery(
                    group_id, "channel", "stream-1", 41, "brief", "first", None, 1.0
                )
                second = await db.create_report_delivery(
                    group_id, "channel", "stream-1", 42, "brief", "second", None, 2.0
                )
                self.assertEqual(second.recipient_chat_id, first.recipient_chat_id)
                self.assertEqual(second.text_payload, "first")
                await db.set_post_recipient(group_id, "channel", 42)
                poller, bot = self._poller(db)

                await self._run_pending(poller)

                self.assertEqual(bot.send_message.await_args.args[0], 41)
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM report_deliveries"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
            finally:
                await db.close()

    async def test_format_is_frozen_after_delivery_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_session(db, report_format="brief")
                await db.add_stream_history(
                    1, "channel", "stream-1", 1000.0, 60, 10, 5, 1
                )
                await db.create_report_delivery(
                    1, "channel", "stream-1", 1, "brief", "frozen", None, 1.0
                )
                await db.set_report_format(1, "channel", "full")
                poller, bot = self._poller(db)

                await self._run_pending(poller)

                bot.send_message.assert_awaited_once()
                bot.send_document.assert_not_awaited()
                delivery = await db.get_report_delivery_for_stream(
                    1, "channel", "stream-1"
                )
                self.assertEqual(delivery.report_format, "brief")
                self.assertTrue(delivery.complete)
            finally:
                await db.close()

    async def test_stale_channel_registration_blocks_pending_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "News")
                delivery = await db.create_report_delivery(
                    channel_id,
                    "channel",
                    "stream-1",
                    channel_id,
                    "full",
                    "text",
                    "<html>report</html>",
                    1.0,
                )
                poller, bot = self._poller(db)

                async def accept_text_then_unregister(*_args, **_kwargs):
                    await db.unregister_telegram_channel(channel_id)
                    return SimpleNamespace()

                poller._tg_call = AsyncMock(
                    side_effect=accept_text_then_unregister
                )

                self.assertTrue(
                    await poller._deliver_persisted_report(delivery)
                )

                poller._tg_call.assert_awaited_once()
                bot.send_document.assert_not_awaited()
                updated = await db.get_report_delivery_for_stream(
                    channel_id, "channel", "stream-1"
                )
                self.assertTrue(updated.text_sent)
                self.assertFalse(updated.html_sent)
                self.assertTrue(updated.terminal_failed)
                self.assertEqual(updated.terminal_reason, "destination_rejected")
            finally:
                await db.close()

    async def test_retry_after_exhaustion_keeps_text_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                delivery = await db.create_report_delivery(
                    1, "channel", "stream-1", 1, "brief", "text", None, 1.0
                )
                poller, bot = self._poller(db)
                retry_error = TelegramRetryAfter(
                    SimpleNamespace(), "retry later", retry_after=1
                )
                bot.send_message.side_effect = [retry_error, retry_error]

                with patch("bot.poller.asyncio.sleep", new=AsyncMock()) as sleep:
                    self.assertFalse(
                        await poller._deliver_persisted_report(delivery)
                    )

                self.assertEqual(bot.send_message.await_count, 2)
                sleep.assert_awaited_once_with(1)
                updated = await db.get_report_delivery_for_stream(
                    1, "channel", "stream-1"
                )
                self.assertFalse(updated.text_sent)
                self.assertFalse(updated.terminal_failed)
            finally:
                await db.close()

    async def test_network_is_temporary_and_forbidden_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                delivery = await db.create_report_delivery(
                    1, "channel", "stream-1", 1, "brief", "text", None, 1.0
                )
                poller, bot = self._poller(db)
                bot.send_message.side_effect = TelegramNetworkError(
                    SimpleNamespace(), "offline"
                )

                self.assertFalse(
                    await poller._deliver_persisted_report(delivery)
                )
                pending = await db.get_report_delivery_for_stream(
                    1, "channel", "stream-1"
                )
                self.assertFalse(pending.text_sent)
                self.assertFalse(pending.terminal_failed)

                bot.send_message.side_effect = TelegramForbiddenError(
                    SimpleNamespace(), "blocked"
                )
                self.assertTrue(
                    await poller._deliver_persisted_report(pending)
                )
                terminal = await db.get_report_delivery_for_stream(
                    1, "channel", "stream-1"
                )
                self.assertFalse(terminal.text_sent)
                self.assertTrue(terminal.terminal_failed)
                self.assertEqual(
                    terminal.terminal_reason, "text_permanent_failure"
                )
            finally:
                await db.close()

    async def test_existing_group_delivery_row_cannot_bypass_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                delivery = await db.create_report_delivery(
                    group_id,
                    "channel",
                    "stream-1",
                    group_id,
                    "full",
                    "text",
                    "<html></html>",
                    1.0,
                )
                poller, bot = self._poller(db)
                poller._tg_call = AsyncMock()

                self.assertTrue(
                    await poller._deliver_persisted_report(delivery)
                )

                poller._tg_call.assert_not_awaited()
                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                updated = await db.get_report_delivery(
                    group_id, "channel", "stream-1", group_id
                )
                self.assertTrue(updated.terminal_failed)
                self.assertEqual(updated.terminal_reason, "destination_rejected")
            finally:
                await db.close()

    async def test_registered_negative_recipient_is_blocked_outside_special_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                source_group_id = -100123
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "News")
                delivery = await db.create_report_delivery(
                    source_group_id,
                    "channel",
                    "stream-1",
                    channel_id,
                    "brief",
                    "text",
                    None,
                    1.0,
                )
                poller, bot = self._poller(db)
                poller._tg_call = AsyncMock()

                self.assertTrue(
                    await poller._deliver_persisted_report(delivery)
                )

                poller._tg_call.assert_not_awaited()
                bot.send_message.assert_not_awaited()
                updated = await db.get_report_delivery(
                    source_group_id, "channel", "stream-1", channel_id
                )
                self.assertTrue(updated.terminal_failed)
            finally:
                await db.close()

    async def test_existing_database_migrates_delivery_schema_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "test.db")
            legacy = Database(path)
            await legacy.connect()
            await legacy.add_stream_history(
                1, "channel", "stream-1", 1.0, 60, 10, 5, 1
            )
            await legacy.conn.execute("DROP TABLE report_deliveries")
            await legacy.conn.commit()
            await legacy.close()

            migrated = Database(path)
            await migrated.connect()
            try:
                self.assertIsNotNone(
                    await migrated.get_finished_stream(
                        1, "channel", "stream-1"
                    )
                )
                cursor = await migrated.conn.execute(
                    "PRAGMA table_info(report_deliveries)"
                )
                table_info = await cursor.fetchall()
                columns = {row[1] for row in table_info}
                self.assertTrue(
                    {
                        "source_chat_id",
                        "twitch_login",
                        "stream_id",
                        "recipient_chat_id",
                        "report_format",
                        "text_payload",
                        "html_payload",
                        "text_sent",
                        "html_sent",
                        "terminal_failed",
                        "terminal_reason",
                        "created_at",
                        "updated_at",
                    }.issubset(columns)
                )
                primary_key = [
                    row[1] for row in sorted(table_info, key=lambda row: row[5])
                    if row[5]
                ]
                self.assertEqual(
                    primary_key,
                    ["source_chat_id", "twitch_login", "stream_id"],
                )
            finally:
                await migrated.close()

            repeated = Database(path)
            await repeated.connect()
            await repeated.close()

    async def test_cleanup_removes_complete_and_terminal_but_keeps_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                complete = await db.create_report_delivery(
                    1, "channel", "complete", 1, "full", "text", "html", 1.0
                )
                await db.mark_report_text_sent(complete, 2.0)
                await db.mark_report_html_sent(complete, 3.0)
                terminal = await db.create_report_delivery(
                    1, "channel", "terminal", 1, "brief", "text", None, 1.0
                )
                await db.mark_report_delivery_terminal(
                    terminal, "permanent", 2.0
                )
                await db.create_report_delivery(
                    1, "channel", "pending", 1, "full", "text", "html", 1.0
                )

                plan_cursor = await db.conn.execute(
                    "EXPLAIN QUERY PLAN DELETE FROM report_deliveries "
                    "WHERE updated_at < ? AND (terminal_failed = 1 OR "
                    "(text_sent = 1 AND (report_format = 'brief' OR html_sent = 1)))",
                    (10.0,),
                )
                plan = " ".join(row[3] for row in await plan_cursor.fetchall())
                self.assertIn("idx_report_deliveries_cleanup", plan)

                await db.purge_old_report_data(10.0)

                self.assertIsNone(
                    await db.get_report_delivery(1, "channel", "complete", 1)
                )
                self.assertIsNone(
                    await db.get_report_delivery(1, "channel", "terminal", 1)
                )
                self.assertIsNotNone(
                    await db.get_report_delivery(1, "channel", "pending", 1)
                )
                cursor = await db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' "
                    "AND name = 'idx_report_deliveries_cleanup'"
                )
                self.assertIsNotNone(await cursor.fetchone())
            finally:
                await db.close()


class ManualReportTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_history(self, db: Database, group_id: int) -> None:
        await db.add_channel(group_id, "channel")
        await db.add_stream_history(
            group_id,
            "channel",
            "stream-1",
            time.time(),
            3600,
            100,
            50,
            10,
            started_at="2026-01-01T00:00:00Z",
            title="Stream",
            new_followers_text="10",
        )

    async def test_manual_report_from_group_goes_only_to_requester_private_chat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            group_id = -100123
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_history(db, group_id)
                await db.mark_known_private_user(42)
                bot = SimpleNamespace(
                    send_message=AsyncMock(), send_document=AsyncMock()
                )
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=group_id),
                    from_user=SimpleNamespace(id=42),
                    bot=bot,
                    answer=AsyncMock(),
                )

                await cmd_report(message, SimpleNamespace(args="channel"), db)

                self.assertEqual(bot.send_message.await_args.args[0], 42)
                self.assertEqual(bot.send_document.await_args.args[0], 42)
                self.assertNotEqual(bot.send_message.await_args.args[0], group_id)
                message.answer.assert_not_awaited()
            finally:
                await db.close()

    async def test_manual_report_without_private_link_sends_only_service_hint(self) -> None:
        db = SimpleNamespace(is_known_private_user=AsyncMock(return_value=False))
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            from_user=SimpleNamespace(id=42),
            bot=bot,
            answer=AsyncMock(),
        )

        await cmd_report(message, SimpleNamespace(args="channel"), db)

        message.answer.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        bot.send_document.assert_not_awaited()

    async def test_brief_and_full_reports_use_same_private_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            group_id = -100123
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await self._seed_history(db, group_id)
                bot = SimpleNamespace(
                    send_message=AsyncMock(), send_document=AsyncMock()
                )
                message = SimpleNamespace(chat=SimpleNamespace(id=group_id), bot=bot)

                await db.set_report_format(group_id, "channel", "brief")
                self.assertTrue(
                    await _deliver_report(
                        message,
                        group_id,
                        "channel",
                        db,
                        recipient_chat_id=42,
                    )
                )
                self.assertEqual(bot.send_message.await_args.args[0], 42)
                bot.send_document.assert_not_awaited()

                bot.send_message.reset_mock()
                await db.set_report_format(group_id, "channel", "full")
                self.assertTrue(
                    await _deliver_report(
                        message,
                        group_id,
                        "channel",
                        db,
                        recipient_chat_id=42,
                    )
                )
                self.assertEqual(bot.send_message.await_args.args[0], 42)
                self.assertEqual(bot.send_document.await_args.args[0], 42)
            finally:
                await db.close()

    async def test_report_cannot_be_sent_to_unregistered_group(self) -> None:
        db = SimpleNamespace(is_telegram_channel=AsyncMock(return_value=False))
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=-100123), bot=bot)

        self.assertFalse(
            await _deliver_report(
                message, -100123, "channel", db, recipient_chat_id=-100123
            )
        )
        bot.send_message.assert_not_awaited()
        bot.send_document.assert_not_awaited()


class FinalReportGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _seed_offline_group(self, db: Database, group_id: int) -> None:
        await db.add_channel(group_id, "channel")
        await db.set_live_state(
            group_id,
            "channel",
            False,
            "stream-1",
            title="Stream",
            offline_since=1000.0,
            stream_started_at="2026-01-01T00:00:00Z",
            peak_viewers=10,
        )

    async def _run_automatic_report(self, db: Database) -> SimpleNamespace:
        bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())
        twitch = SimpleNamespace(get_user_id=AsyncMock(return_value=None))
        poller = StreamPoller(bot, db, twitch, 60)
        poller._fetch_top_clips = AsyncMock(return_value=[])
        poller._fetch_and_save_vod = AsyncMock(return_value=None)
        with patch("bot.poller.time.time", return_value=2801.0):
            await poller._send_pending_stats()
        return bot

    async def test_negative_per_channel_recipient_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_offline_group(db, group_id)
                await db.set_post_recipient(group_id, "channel", -100999)

                bot = await self._run_automatic_report(db)

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                self.assertEqual(await db.pending_stats(), [])
            finally:
                await db.close()

    async def test_negative_chat_wide_recipient_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_offline_group(db, group_id)
                await db.set_stats_recipient(group_id, -100999)

                bot = await self._run_automatic_report(db)

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                self.assertEqual(await db.pending_stats(), [])
            finally:
                await db.close()

    async def test_corrupt_negative_destination_from_upstream_hits_final_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await self._seed_offline_group(db, group_id)
                db.resolve_post_recipient = AsyncMock(return_value=-100999)

                bot = await self._run_automatic_report(db)

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                self.assertEqual(await db.pending_stats(), [])
                self.assertIsNotNone(
                    await db.get_finished_stream(group_id, "channel", "stream-1")
                )
            finally:
                await db.close()

    async def test_deferred_negative_group_destination_is_removed_without_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await db.add_channel(group_id, "channel")
                await db.add_deferred_report(
                    group_id, group_id, "channel", "stream-1", 1.0
                )
                bot = SimpleNamespace(send_message=AsyncMock())
                poller = StreamPoller(bot, db, SimpleNamespace(), 60)

                await poller._check_quiet_hours_end()

                bot.send_message.assert_not_awaited()
                self.assertFalse(await db.has_deferred_reports(group_id))
            finally:
                await db.close()

    async def test_deferred_negative_group_destination_moves_to_private_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                private_id = 42
                await db.add_channel(group_id, "channel")
                await db.set_post_recipient(group_id, "channel", private_id)
                await db.add_deferred_report(
                    group_id, group_id, "channel", "stream-1", 1.0
                )
                bot = SimpleNamespace(
                    send_message=AsyncMock(
                        return_value=SimpleNamespace(message_id=1)
                    )
                )
                poller = StreamPoller(bot, db, SimpleNamespace(), 60)

                # Первый цикл переносит legacy/stale строку из группы в актуальную
                # личку; второй обрабатывает новый recipient из свежего snapshot.
                await poller._check_quiet_hours_end()
                await poller._check_quiet_hours_end()

                self.assertFalse(await db.has_deferred_reports(group_id))
                self.assertTrue(await db.has_deferred_reports(private_id))
                bot.send_message.assert_awaited_once()
                self.assertEqual(bot.send_message.await_args.args[0], private_id)
                self.assertNotEqual(bot.send_message.await_args.args[0], group_id)
            finally:
                await db.close()

    async def test_quiet_digest_callback_cannot_send_report_into_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await db.add_channel(group_id, "channel")
                await db.add_deferred_report(
                    group_id, group_id, "channel", "stream-1", 1.0
                )
                db.resolve_post_recipient = AsyncMock(return_value=group_id)
                bot = SimpleNamespace(
                    send_message=AsyncMock(), send_document=AsyncMock()
                )
                callback = SimpleNamespace(
                    data=f"quietdigest:show:{group_id}",
                    message=SimpleNamespace(
                        chat=SimpleNamespace(id=group_id),
                        bot=bot,
                        edit_reply_markup=AsyncMock(),
                    ),
                    answer=AsyncMock(),
                )

                await cb_quiet_digest_response(callback, db)

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
                self.assertFalse(await db.has_deferred_reports(group_id))
            finally:
                await db.close()

    async def test_registered_channel_is_allowed_only_for_special_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "News")

                self.assertTrue(
                    await validate_report_destination(
                        db,
                        channel_id,
                        source_chat_id=channel_id,
                        allow_telegram_channel=True,
                        operation="test",
                    )
                )
                self.assertFalse(
                    await validate_report_destination(
                        db,
                        channel_id,
                        source_chat_id=channel_id,
                        allow_telegram_channel=False,
                        operation="test",
                    )
                )
            finally:
                await db.close()

    async def test_unregistered_negative_id_is_blocked_in_special_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                self.assertFalse(
                    await validate_report_destination(
                        db,
                        -100456,
                        source_chat_id=-100456,
                        allow_telegram_channel=True,
                        operation="test",
                    )
                )
            finally:
                await db.close()

    async def test_brief_and_full_cannot_bypass_destination_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                group_id = -100123
                await db.add_channel(group_id, "channel")
                bot = SimpleNamespace(
                    send_message=AsyncMock(), send_document=AsyncMock()
                )
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=group_id), bot=bot
                )

                for report_format in ("brief", "full"):
                    await db.set_report_format(
                        group_id, "channel", report_format
                    )
                    self.assertFalse(
                        await _deliver_report(
                            message,
                            group_id,
                            "channel",
                            db,
                            recipient_chat_id=group_id,
                        )
                    )

                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
            finally:
                await db.close()


class RestartLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._directory.name, "restart.db")
        self.db = Database(self._db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._directory.cleanup()

    async def _reopen(self) -> None:
        await self.db.close()
        self.db = Database(self._db_path)
        await self.db.connect()

    async def _seed_live(
        self,
        *,
        chat_id: int = 1,
        stream_id: str = "session-1",
        message_id: int | None = 10,
        last_seen_live_at: float = 1000.0,
        viewers: int = 100,
    ) -> None:
        await self.db.add_channel(chat_id, "channel")
        await self.db.set_live_state(
            chat_id,
            "channel",
            True,
            stream_id,
            message_id=message_id,
            title="Original title",
            stream_started_at="2026-01-01T00:00:00Z",
            last_seen_live_at=last_seen_live_at,
        )
        await self.db.record_viewer_sample(chat_id, "channel", viewers)
        await self.db.set_followers_at_start(chat_id, "channel", 500)

    def _poller(self, stream: StreamInfo | None) -> tuple[StreamPoller, SimpleNamespace]:
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
            send_document=AsyncMock(return_value=SimpleNamespace(message_id=100)),
            delete_message=AsyncMock(),
        )
        twitch = SimpleNamespace(
            get_live_streams=AsyncMock(
                return_value={} if stream is None else {"channel": stream}
            )
        )
        poller = StreamPoller(bot, self.db, twitch, 60)
        poller._notify = AsyncMock(return_value=99)
        poller._edit = AsyncMock(return_value=True)
        poller._maybe_snapshot_followers = AsyncMock()
        return poller, bot

    async def _seed_offline(self, offline_since: float = 1000.0) -> None:
        await self._seed_live()
        await self.db.set_live_state(
            1,
            "channel",
            False,
            "session-1",
            message_id=None,
            title="Original title",
            offline_since=offline_since,
            stream_started_at="2026-01-01T00:00:00Z",
            peak_viewers=100,
        )

    async def _seed_delivery(self, state: str):
        await self._seed_offline()
        await self.db.mark_known_private_user(1)
        delivery = await self.db.create_report_delivery(
            1,
            "channel",
            "session-1",
            1,
            "full",
            "text payload",
            "<html>payload</html>",
            1000.0,
        )
        if state in {"html_pending", "complete"}:
            await self.db.mark_report_text_sent(delivery, 1001.0)
        if state == "complete":
            await self.db.mark_report_html_sent(delivery, 1002.0)
        if state == "terminal":
            await self.db.mark_report_delivery_terminal(delivery, "blocked", 1002.0)
        return delivery

    async def test_live_stream_state_survives_database_reopen(self) -> None:
        await self._seed_live()
        await self.db.record_viewer_sample(1, "channel", 150)
        await self._reopen()

        cursor = await self.db.conn.execute(
            "SELECT is_live, last_stream_id, last_message_id, last_title, offline_since, "
            "stream_started_at, last_seen_live_at, peak_viewers, viewer_sum, "
            "viewer_samples, followers_at_start, stats_sent FROM tracked_channels"
        )
        self.assertEqual(
            await cursor.fetchone(),
            (
                1,
                "session-1",
                10,
                "Original title",
                None,
                "2026-01-01T00:00:00Z",
                1000.0,
                150,
                250,
                2,
                500,
                0,
            ),
        )

    async def test_existing_database_backfills_last_seen_from_stream_samples(self) -> None:
        await self._seed_live(last_seen_live_at=1000.0)
        await self.db.add_stream_sample(
            1, "channel", "session-1", 1050.0, 110, "Title", "Game"
        )
        await self.db.conn.execute(
            "UPDATE tracked_channels SET last_seen_live_at = NULL"
        )
        await self.db.conn.commit()

        await self._reopen()

        cursor = await self.db.conn.execute(
            "SELECT last_seen_live_at FROM tracked_channels"
        )
        self.assertEqual(await cursor.fetchone(), (1050.0,))

    async def test_restart_same_stream_id_edits_post_and_continues_counters(self) -> None:
        await self._seed_live()
        await self._reopen()
        poller, _bot = self._poller(_stream("session-1", viewers=40))

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        poller._edit.assert_awaited_once()
        poller._notify.assert_not_awaited()
        cursor = await self.db.conn.execute(
            "SELECT last_stream_id, stream_started_at, viewer_sum, viewer_samples "
            "FROM tracked_channels"
        )
        self.assertEqual(
            await cursor.fetchone(),
            ("session-1", "2026-01-01T00:00:00Z", 140, 2),
        )

    async def test_restart_new_twitch_id_inside_window_keeps_logical_session(self) -> None:
        await self._seed_live(message_id=None)
        await self._reopen()
        replacement = _stream("twitch-reconnect", viewers=40)
        replacement.started_at = "2026-01-01T00:10:00Z"
        poller, _bot = self._poller(replacement)

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        self.assertTrue(poller._notify.await_args.kwargs["silent"])
        cursor = await self.db.conn.execute(
            "SELECT last_stream_id, stream_started_at, viewer_sum, viewer_samples "
            "FROM tracked_channels"
        )
        self.assertEqual(
            await cursor.fetchone(),
            ("session-1", "2026-01-01T00:00:00Z", 140, 2),
        )

    async def test_restart_new_twitch_id_at_boundary_finalizes_old_before_new(self) -> None:
        await self._seed_live(message_id=None)
        await self.db.mark_known_private_user(1)
        await self.db.set_report_format(1, "channel", "brief")
        await self._reopen()
        poller, bot = self._poller(_stream("session-2", viewers=40))
        poller._fetch_top_clips = AsyncMock(return_value=[])
        poller._fetch_and_save_vod = AsyncMock(return_value=None)

        with patch("bot.poller.time.time", return_value=2800.0):
            await poller._check_streams()

        poller._notify.assert_not_awaited()
        state = await self.db.get_live_state(1, "channel")
        self.assertFalse(state[0])
        self.assertEqual(state[1], "session-1")

        with patch("bot.poller.time.time", return_value=2800.0):
            await poller._send_pending_stats()
        history = await self.db.get_finished_stream(1, "channel", "session-1")
        self.assertIsNotNone(history)
        delivery = await self.db.get_report_delivery_for_stream(
            1, "channel", "session-1"
        )
        self.assertIsNotNone(delivery)
        self.assertTrue(delivery.complete)
        bot.send_message.assert_awaited_once()
        self.assertEqual(
            await self.db.get_live_state(1, "channel"),
            (False, None, None, None, None, None, None),
        )

        with patch("bot.poller.time.time", return_value=2801.0):
            await poller._check_streams()

        self.assertFalse(poller._notify.await_args.kwargs.get("silent", False))
        cursor = await self.db.conn.execute(
            "SELECT last_stream_id, viewer_sum, viewer_samples FROM tracked_channels"
        )
        self.assertEqual(await cursor.fetchone(), ("session-2", 40, 1))

    async def test_negative_last_seen_delta_never_overwrites_old_session(self) -> None:
        await self._seed_live(message_id=None, last_seen_live_at=1000.0)
        await self._reopen()
        poller, _bot = self._poller(_stream("session-2", viewers=40))

        with patch("bot.poller.time.time", return_value=999.0):
            await poller._check_streams()

        poller._notify.assert_not_awaited()
        cursor = await self.db.conn.execute(
            "SELECT is_live, last_stream_id, offline_since, viewer_sum, viewer_samples "
            "FROM tracked_channels"
        )
        self.assertEqual(await cursor.fetchone(), (0, "session-1", 999.0, 100, 1))

    async def test_ready_source_preserves_shared_chat_buffer_for_later_source(self) -> None:
        await self._seed_live(chat_id=1, message_id=None, viewers=10)
        await self._seed_live(chat_id=2, message_id=None, viewers=20)
        for chat_id, offline_since in ((1, 1000.0), (2, 1000.1)):
            await self.db.set_live_state(
                chat_id,
                "channel",
                False,
                "session-1",
                message_id=None,
                title="Original title",
                offline_since=offline_since,
                stream_started_at="2026-01-01T00:00:00Z",
            )
        listener = SimpleNamespace(
            is_running=lambda login: True,
            get_and_clear_activity=lambda login: [(1000.0, 3)],
            get_and_clear_chatters=lambda login: {("viewer", 1000.0)},
            get_and_clear_unique_viewers=lambda login: [],
            get_and_clear_join_reliability=lambda login: True,
            chatters_overflowed=lambda login: False,
            get_and_clear_top_chatters=lambda login: [("viewer", 3)],
            get_and_clear_raid_events=lambda login: [],
            stop=AsyncMock(),
        )
        poller, _bot = self._poller(None)
        poller._chat_listener = listener
        poller._send_stats = AsyncMock(return_value=True)

        with patch("bot.poller.time.time", return_value=2800.0):
            await poller._send_pending_stats()

        listener.stop.assert_awaited_once_with("channel")
        self.assertEqual(
            await self.db.get_stream_chat_meta(2, "channel", "session-1"),
            (True, '[["viewer", 3]]', None),
        )
        pending = await self.db.pending_stats()
        self.assertEqual([(row[0], row[1]) for row in pending], [(2, "channel")])

    async def test_followers_at_start_is_not_resnapshotted_after_restart(self) -> None:
        await self._seed_live()
        await self._reopen()
        poller, _bot = self._poller(_stream("session-1"))
        poller._maybe_snapshot_followers = AsyncMock()

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        args = poller._maybe_snapshot_followers.await_args.args
        self.assertEqual(args[:3], (1, "channel", 500))
        self.assertEqual(args[3], {})
        self.assertEqual(await self.db.get_followers_at_start(1, "channel"), 500)

    async def test_follow_reliability_stays_false_through_restart_reconnect(self) -> None:
        await self._seed_live(message_id=None)
        await self.db.start_follow_event_count(1, "channel", "session-1", True)
        await self._reopen()
        await self.db.invalidate_live_follow_counts_after_restart()
        poller, _bot = self._poller(_stream("twitch-reconnect"))
        poller._follow_listener = SimpleNamespace(
            is_configured=lambda login: True,
            covers_stream_start=lambda login, started_at: True,
        )

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        self.assertEqual(
            await self.db.get_follow_event_count(1, "channel", "session-1"),
            (0, False),
        )
        self.assertIsNone(
            await self.db.get_follow_event_count(1, "channel", "twitch-reconnect")
        )

    async def test_new_stream_after_finalization_can_be_follow_reliable(self) -> None:
        await self._seed_live(message_id=None)
        await self.db.start_follow_event_count(1, "channel", "session-1", False)
        await self.db.set_live_state(
            1,
            "channel",
            False,
            "session-1",
            offline_since=1000.0,
            stream_started_at="2026-01-01T00:00:00Z",
        )
        await self.db.mark_stats_sent(1, "channel")
        await self.db.clear_finished_session(1, "channel")
        poller, _bot = self._poller(_stream("session-2"))
        poller._follow_listener = SimpleNamespace(
            is_configured=lambda login: True,
            covers_stream_start=lambda login, started_at: True,
        )

        with patch("bot.poller.time.time", return_value=3000.0):
            await poller._check_streams()

        self.assertEqual(
            await self.db.get_follow_event_count(1, "channel", "session-2"),
            (0, True),
        )

    async def test_missing_live_post_after_restart_is_replaced_quietly(self) -> None:
        await self._seed_live(message_id=None)
        await self._reopen()
        poller, _bot = self._poller(_stream("session-1"))

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        poller._notify.assert_awaited_once()
        self.assertTrue(poller._notify.await_args.kwargs["silent"])
        self.assertEqual((await self.db.get_live_state(1, "channel"))[2], 99)

    async def test_deleted_live_post_after_restart_is_replaced_quietly(self) -> None:
        await self._seed_live(message_id=10)
        await self._reopen()
        poller, bot = self._poller(_stream("session-1"))
        poller._edit.return_value = False

        with patch("bot.poller.time.time", return_value=1100.0):
            await poller._check_streams()

        bot.delete_message.assert_awaited_once_with(1, 10)
        self.assertTrue(poller._notify.await_args.kwargs["silent"])
        self.assertEqual((await self.db.get_live_state(1, "channel"))[1], "session-1")

    async def test_offline_reconnect_timeout_is_not_reset_by_database_reopen(self) -> None:
        await self._seed_offline(offline_since=1000.0)
        await self._reopen()
        poller, _bot = self._poller(None)
        poller._send_stats = AsyncMock(return_value=True)

        with patch("bot.poller.time.time", return_value=2799.0):
            await poller._send_pending_stats()
        poller._send_stats.assert_not_awaited()

        with patch("bot.poller.time.time", return_value=2800.0):
            await poller._send_pending_stats()
        poller._send_stats.assert_awaited_once()

    async def test_restart_after_reconnect_window_finalizes_promptly(self) -> None:
        await self._seed_offline(offline_since=1000.0)
        await self._reopen()
        poller, _bot = self._poller(None)
        poller._send_stats = AsyncMock(return_value=True)

        with patch("bot.poller.time.time", return_value=3000.0):
            await poller._send_pending_stats()

        poller._send_stats.assert_awaited_once()
        self.assertEqual(
            await self.db.get_live_state(1, "channel"),
            (False, None, None, None, None, None, None),
        )

    async def test_restart_during_offline_window_returns_quietly_and_finalizes_once(self) -> None:
        await self._seed_offline(offline_since=1000.0)
        await self._reopen()
        await self.db.invalidate_live_follow_counts_after_restart()
        await self.db.invalidate_live_chat_stats_after_restart()
        poller, _bot = self._poller(_stream("twitch-reconnect", viewers=40))

        with patch("bot.poller.time.time", return_value=1720.0):
            await poller._check_streams()

        self.assertTrue(poller._notify.await_args.kwargs["silent"])
        cursor = await self.db.conn.execute(
            "SELECT last_stream_id, stream_started_at, viewer_sum, viewer_samples "
            "FROM tracked_channels"
        )
        self.assertEqual(
            await cursor.fetchone(),
            ("session-1", "2026-01-01T00:00:00Z", 140, 2),
        )

        poller._twitch.get_live_streams.return_value = {}
        with patch("bot.poller.time.time", return_value=1800.0):
            await poller._check_streams()
        with patch("bot.poller.time.time", return_value=2100.0):
            await poller._cleanup_offline_posts()

        poller._send_stats = AsyncMock(return_value=True)
        with patch("bot.poller.time.time", return_value=3599.0):
            await poller._send_pending_stats()
        poller._send_stats.assert_not_awaited()
        with patch("bot.poller.time.time", return_value=3600.0):
            await poller._send_pending_stats()
            await poller._send_pending_stats()

        poller._send_stats.assert_awaited_once()
        self.assertEqual(
            await self.db.get_live_state(1, "channel"),
            (False, None, None, None, None, None, None),
        )

    async def test_complete_delivery_restart_sends_nothing_and_cleans_session(self) -> None:
        await self._seed_delivery("complete")
        await self._reopen()
        poller, bot = self._poller(None)

        with patch("bot.poller.time.time", return_value=3000.0):
            await poller._send_pending_stats()

        bot.send_message.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        self.assertEqual(
            await self.db.get_live_state(1, "channel"),
            (False, None, None, None, None, None, None),
        )

    async def test_terminal_delivery_restart_sends_nothing_and_cleans_session(self) -> None:
        await self._seed_delivery("terminal")
        await self._reopen()
        poller, bot = self._poller(None)

        with patch("bot.poller.time.time", return_value=3000.0):
            await poller._send_pending_stats()

        bot.send_message.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        self.assertEqual(
            await self.db.get_live_state(1, "channel"),
            (False, None, None, None, None, None, None),
        )

    async def test_html_pending_restart_retries_only_html_with_guard(self) -> None:
        await self._seed_delivery("html_pending")
        await self._reopen()
        delivery = await self.db.get_report_delivery_for_stream(
            1, "channel", "session-1"
        )
        self.assertIsNotNone(delivery)
        poller, bot = self._poller(None)

        delivered = await poller._deliver_persisted_report(delivery)

        self.assertTrue(delivered)
        bot.send_message.assert_not_awaited()
        bot.send_document.assert_awaited_once()
        completed = await self.db.get_report_delivery_for_stream(
            1, "channel", "session-1"
        )
        self.assertTrue(completed.complete)

    async def test_multiple_chats_resume_one_listener_and_keep_independent_rows(self) -> None:
        await self._seed_live(chat_id=1, message_id=11, viewers=10)
        await self._seed_live(chat_id=2, message_id=22, viewers=20)
        await self._reopen()
        listener = SimpleNamespace(start=Mock())
        poller, _bot = self._poller(None)
        poller._chat_listener = listener

        await poller._resume_chat_listeners()

        listener.start.assert_called_once_with("channel")
        cursor = await self.db.conn.execute(
            "SELECT chat_id, last_message_id, viewer_sum FROM tracked_channels "
            "ORDER BY chat_id"
        )
        self.assertEqual(await cursor.fetchall(), [(1, 11, 10), (2, 22, 20)])

    async def test_chat_stats_are_marked_incomplete_after_restart(self) -> None:
        await self._seed_live()
        await self._reopen()
        await self.db.invalidate_live_chat_stats_after_restart()
        await self.db.save_stream_chat_meta(
            1,
            "channel",
            "session-1",
            True,
            '[["viewer", 3]]',
            None,
            1100.0,
        )

        meta = await self.db.get_stream_chat_meta(1, "channel", "session-1")
        self.assertEqual(meta, (False, '[["viewer", 3]]', None))

    async def test_offline_session_chat_stats_are_marked_incomplete_on_restart(self) -> None:
        await self._seed_offline()
        await self._reopen()
        await self.db.invalidate_live_chat_stats_after_restart()

        self.assertEqual(
            await self.db.get_stream_chat_meta(1, "channel", "session-1"),
            (False, None, None),
        )

    async def test_token_refresh_startup_race_is_serialized_per_login(self) -> None:
        await self.db.save_user_token(
            "channel", "broadcaster", "expired-access", "refresh-once", 0.0
        )
        store = TokenStore(
            self.db,
            "client-id",
            "client-secret",
            SimpleNamespace(),
        )

        async def refresh_once(*_args):
            await asyncio.sleep(0)
            return "fresh-access", "rotated-refresh", time.time() + 3600

        with patch("bot.token_store.refresh_user_token", new=AsyncMock(side_effect=refresh_once)) as refresh:
            first, second = await asyncio.gather(
                store.get_valid_token("channel"),
                store.get_valid_token("channel"),
            )

        self.assertEqual(first, ("broadcaster", "fresh-access"))
        self.assertEqual(second, first)
        refresh.assert_awaited_once()


class TestIsolationTests(unittest.TestCase):
    def test_production_dotenv_loading_is_disabled(self) -> None:
        self.assertEqual(os.environ.get("PYTHON_DOTENV_DISABLED"), "1")
        self.assertEqual(os.environ.get("DB_PATH"), ":memory:")

    def test_test_suite_never_constructs_real_aiogram_bot(self) -> None:
        tests_root = Path(__file__).resolve().parent
        for test_path in tests_root.glob("test*.py"):
            source = test_path.read_text(encoding="utf-8")
            self.assertNotIn(
                "Bot" + "(",
                source,
                f"{test_path.name} must use a fake/mock bot, not aiogram.Bot",
            )

    def test_startup_orders_reliability_and_listener_recovery_before_polling(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        main_source = (project_root / "main.py").read_text(encoding="utf-8")
        poller_source = (project_root / "bot" / "poller.py").read_text(encoding="utf-8")

        ordered_main_fragments = (
            "await db.connect()",
            "await db.invalidate_live_follow_counts_after_restart()",
            "await db.invalidate_live_chat_stats_after_restart()",
            "lambda: _reconcile_telegram_channels(bot, db)",
            "follow_listener_task = asyncio.create_task(follow_listener.run())",
            "await follow_listener.wait_initial_ready()",
            "poller_task = asyncio.create_task(poller.run())",
            "dp.start_polling(bot, close_bot_session=False)",
        )
        positions = [main_source.index(fragment) for fragment in ordered_main_fragments]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(
            poller_source.index("await self._resume_chat_listeners()"),
            poller_source.index("await self._check_once()"),
        )


class TelegramChannelRegistryRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_reconciliation_removes_stale_channel_and_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "Old channel")
                await db.add_channel(channel_id, "channel")
                bot = SimpleNamespace(
                    id=99,
                    get_chat=AsyncMock(
                        return_value=SimpleNamespace(type="supergroup")
                    ),
                    get_chat_member=AsyncMock(
                        return_value=SimpleNamespace(status="administrator")
                    ),
                )

                await _reconcile_telegram_channels(bot, db)

                self.assertFalse(await db.is_telegram_channel(channel_id))
                self.assertEqual(await db.list_channels(channel_id), [])
                self.assertFalse(
                    await validate_report_destination(
                        db,
                        channel_id,
                        source_chat_id=channel_id,
                        allow_telegram_channel=True,
                        operation="test",
                    )
                )
            finally:
                await db.close()


class UserTokenFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()

    @staticmethod
    def _session(*, get=None, post=None):
        return SimpleNamespace(
            get=Mock(side_effect=get or []),
            post=Mock(side_effect=post or []),
        )

    async def _store(self, access="old-access", refresh="old-refresh", expires=None):
        await self.db.save_user_token(
            "channel",
            "broadcaster",
            access,
            refresh,
            time.time() + 3600 if expires is None else expires,
        )
        return TokenStore(self.db, "client", "secret", SimpleNamespace())

    async def test_user_follower_endpoint_success(self) -> None:
        session = self._session(get=[_FakeHTTPResponse(200, {"total": 42})])
        client = TwitchClient("client", "secret", session)

        self.assertEqual(await client.get_followers_count("broadcaster", "access"), 42)
        self.assertEqual(session.get.call_count, 1)

    async def test_user_endpoint_429_is_temporary_and_long_wait_is_deferred(self) -> None:
        session = self._session(
            get=[_FakeHTTPResponse(429, headers={"Retry-After": "30"})]
        )
        client = TwitchClient("client", "secret", session)

        with patch("bot.twitch.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(TwitchRateLimitError):
                await client.get_followers_count("broadcaster", "access")

        sleep.assert_not_awaited()
        self.assertEqual(session.get.call_count, 1)

    async def test_user_endpoint_500_retries_once_then_succeeds(self) -> None:
        session = self._session(
            get=[
                _FakeHTTPResponse(500),
                _FakeHTTPResponse(200, {"total": 9}),
            ]
        )
        client = TwitchClient("client", "secret", session)

        with patch("bot.twitch.asyncio.sleep", new=AsyncMock()) as sleep:
            self.assertEqual(
                await client.get_followers_count("broadcaster", "access"), 9
            )

        sleep.assert_awaited_once()
        self.assertEqual(session.get.call_count, 2)

    async def test_user_endpoint_timeout_is_temporary_and_bounded(self) -> None:
        session = self._session(
            get=[asyncio.TimeoutError(), asyncio.TimeoutError()]
        )
        client = TwitchClient("client", "secret", session)

        with patch("bot.twitch.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(TwitchTemporaryError):
                await client.get_followers_count("broadcaster", "access")

        sleep.assert_awaited_once()
        self.assertEqual(session.get.call_count, 2)

    async def test_user_endpoint_403_is_not_misclassified_as_unauthorized(self) -> None:
        session = self._session(get=[_FakeHTTPResponse(403)])
        client = TwitchClient("client", "secret", session)

        with self.assertRaises(TwitchUserResponseError) as caught:
            await client.get_followers_count("broadcaster", "access")

        self.assertEqual(caught.exception.status, 403)

    async def test_401_forces_one_refresh_then_retries_with_new_access(self) -> None:
        store = await self._store()
        session = self._session(
            get=[
                _FakeHTTPResponse(401),
                _FakeHTTPResponse(200, {"total": 17}),
            ]
        )
        client = TwitchClient("client", "secret", session)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(return_value=("new-access", "new-refresh", time.time() + 3600)),
        ) as refresh:
            result = await store.execute_with_token(
                "channel",
                lambda broadcaster_id, access_token: client.get_followers_count(
                    broadcaster_id, access_token
                ),
            )

        self.assertEqual(result, 17)
        refresh.assert_awaited_once()
        self.assertEqual(
            await self.db.get_user_token("channel"),
            ("broadcaster", "new-access", "new-refresh", refresh.return_value[2]),
        )
        authorizations = [
            call.kwargs["headers"]["Authorization"] for call in session.get.call_args_list
        ]
        self.assertEqual(authorizations, ["Bearer old-access", "Bearer new-access"])

    async def test_second_401_after_refresh_is_terminal_without_loop(self) -> None:
        store = await self._store()
        session = self._session(get=[_FakeHTTPResponse(401), _FakeHTTPResponse(401)])
        client = TwitchClient("client", "secret", session)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(return_value=("new-access", "new-refresh", time.time() + 3600)),
        ) as refresh:
            with self.assertRaises(TwitchAuthError):
                await store.execute_with_token(
                    "channel",
                    lambda broadcaster_id, access_token: client.get_followers_count(
                        broadcaster_id, access_token
                    ),
                )

        refresh.assert_awaited_once()
        self.assertEqual(session.get.call_count, 2)
        operation = AsyncMock(return_value=1)
        with self.assertRaises(TwitchAuthError):
            await store.execute_with_token("channel", operation)
        operation.assert_not_awaited()
        refresh.assert_awaited_once()

    async def test_concurrent_401_operations_share_one_refresh(self) -> None:
        store = await self._store()
        both_rejected = asyncio.Event()
        old_calls = 0

        async def operation(_broadcaster_id, access_token):
            nonlocal old_calls
            if access_token == "old-access":
                old_calls += 1
                if old_calls == 2:
                    both_rejected.set()
                await both_rejected.wait()
                raise TwitchUnauthorizedError("401")
            return access_token

        async def rotate(*_args):
            await asyncio.sleep(0)
            return "new-access", "new-refresh", time.time() + 3600

        with patch(
            "bot.token_store.refresh_user_token", new=AsyncMock(side_effect=rotate)
        ) as refresh:
            first, second = await asyncio.gather(
                store.execute_with_token("channel", operation),
                store.execute_with_token("channel", operation),
            )

        self.assertEqual((first, second), ("new-access", "new-access"))
        refresh.assert_awaited_once()

    async def test_rotated_refresh_token_is_used_by_next_refresh(self) -> None:
        store = await self._store(expires=0.0)
        refresh_results = [
            ("access-one", "refresh-one", 0.0),
            ("access-two", "refresh-two", time.time() + 3600),
        ]

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(side_effect=refresh_results),
        ) as refresh:
            await store.get_valid_token("channel")
            await store.get_valid_token("channel")

        self.assertEqual(refresh.await_count, 2)
        self.assertEqual(refresh.await_args_list[0].args[2], "old-refresh")
        self.assertEqual(refresh.await_args_list[1].args[2], "refresh-one")

    async def test_temporary_refresh_failure_preserves_stored_token(self) -> None:
        store = await self._store(expires=0.0)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(side_effect=OAuthTokenTemporaryError("temporary")),
        ):
            with self.assertRaises(OAuthTokenTemporaryError):
                await store.get_valid_token("channel")

        self.assertEqual(
            await self.db.get_user_token("channel"),
            ("broadcaster", "old-access", "old-refresh", 0.0),
        )

    async def test_invalid_grant_is_terminal_until_token_record_changes(self) -> None:
        store = await self._store(expires=0.0)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(side_effect=OAuthTokenRevokedError("invalid_grant")),
        ) as refresh:
            with self.assertRaises(OAuthTokenRevokedError):
                await store.get_valid_token("channel")
            self.assertIsNone(await store.get_valid_token("channel"))

        refresh.assert_awaited_once()
        await self.db.save_user_token(
            "channel", "broadcaster", "reauth-access", "reauth-refresh", time.time() + 3600
        )
        self.assertEqual(
            await store.get_valid_token("channel"),
            ("broadcaster", "reauth-access"),
        )

    async def test_refresh_invalid_grant_is_classified_without_secret_in_error(self) -> None:
        session = self._session(
            post=[_FakeHTTPResponse(400, {"error": "invalid_grant"})]
        )

        with self.assertRaises(OAuthTokenRevokedError) as caught:
            await refresh_user_token("client", "secret", "refresh-secret", session)

        self.assertNotIn("refresh-secret", str(caught.exception))

    async def test_malformed_refresh_response_is_temporary(self) -> None:
        session = self._session(
            post=[_FakeHTTPResponse(200, {"access_token": "access", "expires_in": 3600})]
        )

        with self.assertRaises(OAuthTokenTemporaryError):
            await refresh_user_token("client", "secret", "refresh", session)

    async def test_new_user_token_validation_retries_temporary_5xx(self) -> None:
        session = self._session(
            post=[
                _FakeHTTPResponse(
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                    },
                )
            ],
            get=[
                _FakeHTTPResponse(500),
                _FakeHTTPResponse(
                    200, {"data": [{"login": "Channel", "id": "42"}]}
                ),
            ],
        )

        with patch("bot.twitch.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await _exchange_code(
                "client", "secret", "code", "https://example.test/callback", session
            )

        self.assertEqual((result.login, result.broadcaster_id), ("channel", "42"))
        sleep.assert_awaited_once()

    async def test_new_user_token_validation_401_is_safe_auth_error(self) -> None:
        session = self._session(
            post=[
                _FakeHTTPResponse(
                    200,
                    {
                        "access_token": "new-access-secret",
                        "refresh_token": "new-refresh-secret",
                        "expires_in": 3600,
                    },
                )
            ],
            get=[_FakeHTTPResponse(401)],
        )

        with self.assertRaises(OAuthFlowError) as caught:
            await _exchange_code(
                "client", "client-secret", "code-secret",
                "https://example.test/callback", session,
            )

        error = str(caught.exception)
        self.assertNotIn("new-access-secret", error)
        self.assertNotIn("new-refresh-secret", error)
        self.assertNotIn("client-secret", error)
        self.assertNotIn("code-secret", error)

    async def test_refresh_500_retries_once_then_persists_rotated_pair(self) -> None:
        session = self._session(
            post=[
                _FakeHTTPResponse(500),
                _FakeHTTPResponse(
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                    },
                ),
            ]
        )

        with patch("bot.oauth.asyncio.sleep", new=AsyncMock()) as sleep:
            access, refresh, expires_at = await refresh_user_token(
                "client", "secret", "old-refresh", session
            )

        self.assertEqual((access, refresh), ("new-access", "new-refresh"))
        self.assertGreater(expires_at, time.time())
        sleep.assert_awaited_once()
        self.assertEqual(session.post.call_count, 2)

    async def test_refresh_429_with_long_wait_is_temporary_without_sleep(self) -> None:
        session = self._session(
            post=[_FakeHTTPResponse(429, headers={"Retry-After": "30"})]
        )

        with patch("bot.oauth.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(OAuthTokenTemporaryError):
                await refresh_user_token("client", "secret", "refresh", session)

        sleep.assert_not_awaited()
        self.assertEqual(session.post.call_count, 1)

    async def test_refresh_timeout_is_temporary_and_bounded(self) -> None:
        session = self._session(
            post=[asyncio.TimeoutError(), asyncio.TimeoutError()]
        )

        with patch("bot.oauth.asyncio.sleep", new=AsyncMock()) as sleep:
            with self.assertRaises(OAuthTokenTemporaryError):
                await refresh_user_token("client", "secret", "refresh", session)

        sleep.assert_awaited_once()
        self.assertEqual(session.post.call_count, 2)

    async def test_rotated_token_is_not_returned_when_database_save_fails(self) -> None:
        store = await self._store(expires=0.0)

        with (
            patch(
                "bot.token_store.refresh_user_token",
                new=AsyncMock(
                    return_value=("new-access", "new-refresh", time.time() + 3600)
                ),
            ) as refresh,
            patch.object(
                self.db,
                "save_user_token",
                new=AsyncMock(side_effect=OSError("database unavailable")),
            ),
        ):
            with self.assertRaisesRegex(OSError, "database unavailable"):
                await store.get_valid_token("channel")

        self.assertEqual(
            await self.db.get_user_token("channel"),
            ("broadcaster", "old-access", "old-refresh", 0.0),
        )
        self.assertEqual(
            await store.get_valid_token("channel"),
            ("broadcaster", "new-access"),
        )
        refresh.assert_awaited_once()
        persisted = await self.db.get_user_token("channel")
        self.assertEqual(persisted[1:3], ("new-access", "new-refresh"))

    async def test_follower_snapshot_failure_is_not_zero_and_retries_next_cycle(self) -> None:
        db = SimpleNamespace(set_followers_at_start=AsyncMock())
        token_store = SimpleNamespace(
            execute_with_token=AsyncMock(
                side_effect=[TwitchRateLimitError(30), 25]
            )
        )
        poller = StreamPoller(
            SimpleNamespace(), db, SimpleNamespace(), 60, token_store=token_store
        )
        first_cycle: dict[str, int | None] = {}

        await poller._maybe_snapshot_followers(1, "channel", None, first_cycle)
        await poller._maybe_snapshot_followers(2, "channel", None, first_cycle)

        db.set_followers_at_start.assert_not_awaited()
        self.assertEqual(token_store.execute_with_token.await_count, 1)

        await poller._maybe_snapshot_followers(1, "channel", None, {})

        db.set_followers_at_start.assert_awaited_once_with(1, "channel", 25)
        self.assertEqual(token_store.execute_with_token.await_count, 2)

    async def test_followed_pagination_429_midway_never_returns_partial_result(self) -> None:
        session = self._session(
            get=[
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [{"broadcaster_login": "one"}],
                        "pagination": {"cursor": "next"},
                    },
                ),
                _FakeHTTPResponse(429, headers={"Retry-After": "30"}),
            ]
        )
        client = TwitchClient("client", "secret", session)

        with self.assertRaises(TwitchRateLimitError):
            await client.get_followed_channels("user", "access")

        self.assertEqual(session.get.call_count, 2)

    async def test_followed_pagination_401_refreshes_once_and_restarts_safely(self) -> None:
        store = await self._store()
        session = self._session(
            get=[
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [{"broadcaster_login": "one"}],
                        "pagination": {"cursor": "next"},
                    },
                ),
                _FakeHTTPResponse(401),
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [{"broadcaster_login": "one"}],
                        "pagination": {"cursor": "next"},
                    },
                ),
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [{"broadcaster_login": "two"}],
                        "pagination": {},
                    },
                ),
            ]
        )
        client = TwitchClient("client", "secret", session)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(return_value=("new-access", "new-refresh", time.time() + 3600)),
        ) as refresh:
            follows = await store.execute_with_token(
                "channel",
                lambda broadcaster_id, access_token: client.get_followed_channels(
                    broadcaster_id, access_token
                ),
            )

        self.assertEqual(follows, ["one", "two"])
        refresh.assert_awaited_once()
        authorizations = [
            call.kwargs["headers"]["Authorization"] for call in session.get.call_args_list
        ]
        self.assertEqual(
            authorizations,
            [
                "Bearer old-access",
                "Bearer old-access",
                "Bearer new-access",
                "Bearer new-access",
            ],
        )

    async def test_repeated_pagination_cursor_is_bounded_error(self) -> None:
        page = {
            "data": [{"broadcaster_login": "one"}],
            "pagination": {"cursor": "same"},
        }
        session = self._session(
            get=[_FakeHTTPResponse(200, page), _FakeHTTPResponse(200, page)]
        )
        client = TwitchClient("client", "secret", session)

        with self.assertRaises(TwitchPaginationError):
            await client.get_followed_channels("user", "access")

        self.assertEqual(session.get.call_count, 2)

    async def test_followed_pagination_deduplicates_channels(self) -> None:
        session = self._session(
            get=[
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [{"broadcaster_login": "One"}],
                        "pagination": {"cursor": "next"},
                    },
                ),
                _FakeHTTPResponse(
                    200,
                    {
                        "data": [
                            {"broadcaster_login": "one"},
                            {"broadcaster_login": "two"},
                        ],
                        "pagination": {},
                    },
                ),
            ]
        )
        client = TwitchClient("client", "secret", session)

        self.assertEqual(
            await client.get_followed_channels("user", "access"),
            ["one", "two"],
        )

    async def test_malformed_followed_page_is_not_false_complete(self) -> None:
        session = self._session(
            get=[_FakeHTTPResponse(200, {"data": "not-a-list", "pagination": {}})]
        )
        client = TwitchClient("client", "secret", session)

        with self.assertRaises(TwitchTemporaryError):
            await client.get_followed_channels("user", "access")

    async def test_eventsub_subscription_401_uses_coordinated_refresh(self) -> None:
        store = await self._store()
        session = self._session(
            post=[_FakeHTTPResponse(401), _FakeHTTPResponse(202)]
        )
        listener = FollowEventListener(self.db, store, "client", session)

        with patch(
            "bot.token_store.refresh_user_token",
            new=AsyncMock(return_value=("new-access", "new-refresh", time.time() + 3600)),
        ) as refresh:
            await store.execute_with_token(
                "channel",
                lambda broadcaster_id, access_token: listener._subscribe(
                    "session", broadcaster_id, access_token
                ),
            )

        refresh.assert_awaited_once()
        self.assertEqual(session.post.call_count, 2)

    async def test_import_temporary_error_does_not_publish_partial_selection(self) -> None:
        message = SimpleNamespace(
            chat=SimpleNamespace(type="private", id=123),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock(), update_data=AsyncMock())
        config = SimpleNamespace(
            twitch_client_id="client",
            twitch_client_secret="secret",
        )
        result = UserTokenResult(
            login="channel",
            broadcaster_id="broadcaster",
            access_token="access",
            refresh_token="refresh",
            expires_at=time.time() + 3600,
        )

        with (
            patch(
                "bot.handlers.streams.run_authorization_flow",
                new=AsyncMock(return_value=result),
            ),
            patch(
                "bot.handlers.streams.TwitchClient.get_followed_channels",
                new=AsyncMock(side_effect=TwitchRateLimitError(30)),
            ),
        ):
            await _run_import_follows(
                message,
                state,
                self.db,
                config,
                SimpleNamespace(),
            )

        state.update_data.assert_not_awaited()
        self.assertIn("Ничего не импортировано", message.answer.await_args.args[0])

    async def test_user_token_logs_never_contain_secrets(self) -> None:
        session = self._session(
            get=[_FakeHTTPResponse(500), _FakeHTTPResponse(500)]
        )
        client = TwitchClient("client", "secret", session)

        with (
            patch("bot.twitch.asyncio.sleep", new=AsyncMock()),
            self.assertLogs("bot.twitch", level="WARNING") as captured,
        ):
            with self.assertRaises(TwitchTemporaryError):
                await client.get_followers_count(
                    "broadcaster-secret", "access-secret"
                )

        logs = "\n".join(captured.output)
        self.assertNotIn("access-secret", logs)
        self.assertNotIn("refresh-secret", logs)
        self.assertNotIn("Authorization", logs)


class ProductionHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_twitch_stage_failure_does_not_starve_independent_cycle_work(self) -> None:
        db = SimpleNamespace(
            recover_finished_sessions=AsyncMock(),
            purge_old_report_data=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._check_streams = AsyncMock(side_effect=RuntimeError("Twitch down"))
        poller._send_pending_stats = AsyncMock()
        poller._cleanup_offline_posts = AsyncMock()
        poller._check_channel_bans = AsyncMock()
        poller._check_channel_renames = AsyncMock()
        poller._check_quiet_hours_end = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "опрос Twitch-стримов"):
            await poller._check_once()

        poller._send_pending_stats.assert_awaited_once()
        poller._cleanup_offline_posts.assert_awaited_once()
        poller._check_quiet_hours_end.assert_awaited_once()
        db.purge_old_report_data.assert_awaited_once()

    async def test_hot_pending_queries_use_partial_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                queries = {
                    "idx_tracked_channels_pending_posts": (
                        "SELECT chat_id FROM tracked_channels WHERE is_live = 0 "
                        "AND offline_since IS NOT NULL AND last_message_id IS NOT NULL"
                    ),
                    "idx_tracked_channels_pending_stats": (
                        "SELECT chat_id FROM tracked_channels WHERE is_live = 0 "
                        "AND offline_since IS NOT NULL AND stats_sent = 0 "
                        "AND stream_started_at IS NOT NULL ORDER BY offline_since ASC"
                    ),
                    "idx_report_deliveries_pending": (
                        "SELECT source_chat_id FROM report_deliveries "
                        "WHERE terminal_failed = 0 AND (text_sent = 0 OR "
                        "(report_format = 'full' AND html_sent = 0)) "
                        "ORDER BY updated_at ASC LIMIT 5"
                    ),
                }
                for expected_index, query in queries.items():
                    cursor = await db.conn.execute(f"EXPLAIN QUERY PLAN {query}")
                    plan = " ".join(str(row) for row in await cursor.fetchall())
                    self.assertIn(expected_index, plan)
            finally:
                await db.close()

    async def test_cancelled_write_is_rolled_back_before_next_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            entered_commit = asyncio.Event()
            never = asyncio.Event()

            async def blocked_commit() -> None:
                entered_commit.set()
                await never.wait()

            try:
                with patch.object(db.conn, "commit", new=blocked_commit):
                    task = asyncio.create_task(db.add_channel(1, "cancelled"))
                    await asyncio.wait_for(entered_commit.wait(), timeout=1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

                # Если cancellation не сделал rollback, этот commit зафиксировал бы
                # INSERT отменённой операции вместе со своей записью.
                await db.add_channel(1, "survivor")
                self.assertEqual(await db.list_channels(1), ["survivor"])
            finally:
                await db.close()

    async def test_connect_failure_closes_partially_opened_sqlite_connection(self) -> None:
        connection = SimpleNamespace(
            execute=AsyncMock(side_effect=OSError("read-only volume")),
            close=AsyncMock(),
        )
        db = Database("ignored.db")
        with patch("bot.database.aiosqlite.connect", new=AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(OSError, "read-only"):
                await db.connect()
        connection.close.assert_awaited_once()
        self.assertIsNone(db._conn)

    async def test_pending_delivery_retry_rotates_queue_instead_of_starving_newer_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                first_batch = []
                for index in range(6):
                    first_batch.append(
                        await db.create_report_delivery(
                            1,
                            f"channel{index}",
                            f"stream{index}",
                            1,
                            "brief",
                            "text",
                            None,
                            float(index + 1),
                        )
                    )
                selected = await db.pending_report_deliveries(5)
                self.assertEqual([row.twitch_login for row in selected], [f"channel{i}" for i in range(5)])
                for offset, delivery in enumerate(selected):
                    await db.defer_report_delivery_retry(delivery, 100.0 + offset)

                next_row = await db.pending_report_deliveries(1)
                self.assertEqual(next_row[0].twitch_login, "channel5")
            finally:
                await db.close()

    async def test_long_retry_after_is_deferred_without_blocking_poll_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                delivery = await db.create_report_delivery(
                    1, "channel", "stream", 1, "brief", "text", None, 1.0
                )
                retry_error = TelegramRetryAfter(
                    SimpleNamespace(), "retry later", retry_after=30
                )
                bot = SimpleNamespace(send_message=AsyncMock(side_effect=retry_error))
                poller = StreamPoller(bot, db, SimpleNamespace(), 60)
                with patch("bot.poller.asyncio.sleep", new=AsyncMock()) as sleep:
                    self.assertFalse(await poller._deliver_persisted_report(delivery))
                sleep.assert_not_awaited()
                bot.send_message.assert_awaited_once()
                updated = await db.get_report_delivery_for_stream(1, "channel", "stream")
                self.assertGreater(updated.updated_at, 1.0)
            finally:
                await db.close()

    async def test_follower_snapshot_is_fetched_once_per_login_for_multiple_chats(self) -> None:
        db = SimpleNamespace(set_followers_at_start=AsyncMock())
        twitch = SimpleNamespace(get_followers_count=AsyncMock(return_value=42))

        async def execute(_login, operation):
            return await operation("broadcaster", "access")

        token_store = SimpleNamespace(execute_with_token=AsyncMock(side_effect=execute))
        poller = StreamPoller(
            SimpleNamespace(), db, twitch, 60, token_store=token_store
        )
        cache: dict[str, int | None] = {}

        await poller._maybe_snapshot_followers(1, "channel", None, cache)
        await poller._maybe_snapshot_followers(2, "channel", None, cache)

        token_store.execute_with_token.assert_awaited_once()
        self.assertEqual(token_store.execute_with_token.await_args.args[0], "channel")
        twitch.get_followers_count.assert_awaited_once_with("broadcaster", "access")
        self.assertEqual(db.set_followers_at_start.await_count, 2)

    async def test_poller_shutdown_cancels_raid_tasks(self) -> None:
        started = asyncio.Event()

        async def background() -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(background())
        await started.wait()
        chat_listener = SimpleNamespace(
            set_raid_callback=Mock(),
            stop_all=AsyncMock(),
        )
        poller = StreamPoller(
            SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 60,
            chat_listener=chat_listener,
        )
        poller._background_tasks.add(task)

        await asyncio.wait_for(poller.shutdown(), timeout=1)

        self.assertTrue(task.cancelled())
        chat_listener.set_raid_callback.assert_called_once_with(None)
        chat_listener.stop_all.assert_awaited_once()

    async def test_eventsub_missing_keepalive_forces_reconnect(self) -> None:
        welcome = {
            "metadata": {"message_type": "session_welcome"},
            "payload": {
                "session": {
                    "id": "session",
                    "keepalive_timeout_seconds": 1,
                }
            },
        }
        ws = SimpleNamespace(
            receive_json=AsyncMock(return_value=welcome),
            receive=AsyncMock(side_effect=asyncio.TimeoutError()),
            close=AsyncMock(),
        )
        session = SimpleNamespace(ws_connect=AsyncMock(return_value=ws))
        listener = FollowEventListener(
            SimpleNamespace(), SimpleNamespace(), "client", session
        )
        listener._subscribe = AsyncMock()
        timeouts: list[float] = []

        async def observed_wait_for(awaitable, timeout):
            timeouts.append(timeout)
            return await awaitable

        with patch("bot.follow_listener.asyncio.wait_for", new=observed_wait_for):
            with self.assertRaises(asyncio.TimeoutError):
                await listener._connection("channel", "broadcaster", "access")

        self.assertEqual(timeouts, [10, 11.0])
        ws.close.assert_awaited_once()

    async def test_oauth_pending_states_are_bounded_and_cleared_on_stop(self) -> None:
        server = OAuthCallbackServer("https://example.test/callback", "127.0.0.1", 0)
        with patch("bot.oauth.MAX_PENDING_AUTHORIZATIONS", 2):
            server.register_state("one")
            server.register_state("two")
            with self.assertRaisesRegex(OAuthFlowError, "Слишком много"):
                server.register_state("three")
        await server.stop()
        self.assertEqual(server._pending, {})

    async def test_twelve_hour_chat_stream_keeps_join_nicks_bounded(self) -> None:
        listener = ChatListener(SimpleNamespace())
        now = [1_700_000_000.0]
        with (
            patch("bot.chat_listener.MAX_TRACKED_CHATTERS", 100),
            patch("bot.chat_listener.time.time", side_effect=lambda: now[0]),
        ):
            for minute in range(12 * 60):
                now[0] = 1_700_000_000.0 + minute * 60
                listener._record_message("channel", "speaker")
                listener._record_join("channel", f"viewer{minute}")

        self.assertEqual(len(listener._buckets["channel"]), 12 * 60)
        self.assertEqual(listener._message_counts["channel"]["speaker"], 12 * 60)
        self.assertEqual(len(listener._unique_nicks["channel"]), 100)

    async def test_twelve_hour_poller_session_keeps_exact_aggregate_state(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            await db.add_channel(1, "channel")
            await db.set_notify_enabled(1, "channel", False)
            twitch = SimpleNamespace(
                get_live_streams=AsyncMock(
                    return_value={"channel": _stream("twelve-hour", viewers=25)}
                )
            )
            poller = StreamPoller(SimpleNamespace(), db, twitch, 60)
            now = [1_700_000_000.0]
            with patch("bot.poller.time.time", side_effect=lambda: now[0]):
                for minute in range(12 * 60):
                    now[0] = 1_700_000_000.0 + minute * 60
                    await poller._check_streams()

            cursor = await db.conn.execute(
                "SELECT viewer_sum, viewer_samples, peak_viewers "
                "FROM tracked_channels WHERE chat_id = 1 AND twitch_login = 'channel'"
            )
            self.assertEqual(await cursor.fetchone(), (18_000, 720, 25))
            cursor = await db.conn.execute("SELECT COUNT(*) FROM stream_samples")
            self.assertEqual((await cursor.fetchone())[0], 720)
            self.assertEqual(twitch.get_live_streams.await_count, 720)
        finally:
            await db.close()

    async def test_one_hundred_logins_use_one_twitch_streams_batch(self) -> None:
        client = TwitchClient("client", "secret", SimpleNamespace())
        client._request = AsyncMock(return_value={"data": []})
        logins = [f"channel{i}" for i in range(100)]

        self.assertEqual(await client.get_live_streams(logins), {})

        client._request.assert_awaited_once()
        self.assertEqual(len(client._request.await_args.args[1]), 100)


class StartupHardeningTests(unittest.TestCase):
    def test_permanent_configuration_error_does_not_restart_forever(self) -> None:
        with (
            patch.object(
                main_module,
                "main",
                new=AsyncMock(side_effect=ConfigError("bad env")),
            ),
            patch.object(main_module.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(SystemExit, "2"):
                main_module.run_forever()
        sleep.assert_not_called()

    def test_invalid_telegram_token_does_not_restart_forever(self) -> None:
        with (
            patch.object(
                main_module,
                "main",
                new=AsyncMock(side_effect=TokenValidationError("bad token")),
            ),
            patch.object(main_module.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(SystemExit, "2"):
                main_module.run_forever()
        sleep.assert_not_called()


class AsyncStartupHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_failure_before_runtime_block_closes_db_and_bot(self) -> None:
        config = SimpleNamespace(
            db_path=":memory:",
            token_encryption_key=None,
            telegram_bot_token="unit-test-token-never-used",
            twitch_client_id="client",
            twitch_client_secret="secret",
            poll_interval_seconds=60,
            owner_chat_id=None,
            oauth_public_base_url="https://example.test",
            oauth_host="127.0.0.1",
            oauth_port=0,
            auto_track=(),
        )
        db = SimpleNamespace(
            connect=AsyncMock(),
            invalidate_live_follow_counts_after_restart=AsyncMock(),
            invalidate_live_chat_stats_after_restart=AsyncMock(),
            all_telegram_channels=AsyncMock(return_value=[]),
            all_distinct_group_chat_ids=AsyncMock(return_value=[]),
            close=AsyncMock(),
        )
        bot = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))

        async def execute(factory, _description):
            return await factory()

        with (
            patch.object(main_module, "load_config", return_value=config),
            patch.object(main_module, "Database", return_value=db),
            patch.object(main_module, "Bot", return_value=bot),
            patch.object(main_module, "_with_startup_retry", side_effect=execute),
            patch.object(
                main_module,
                "_reconcile_telegram_channels",
                new=AsyncMock(side_effect=RuntimeError("startup failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                await main_module.main()

        db.close.assert_awaited_once()
        bot.session.close.assert_awaited_once()

    async def test_poller_crash_is_supervised_and_cleanup_failures_are_isolated(self) -> None:
        config = SimpleNamespace(
            db_path=":memory:", token_encryption_key=None,
            telegram_bot_token="unit-test-token-never-used",
            twitch_client_id="client", twitch_client_secret="secret",
            poll_interval_seconds=60, owner_chat_id=None,
            oauth_public_base_url="https://example.test",
            oauth_host="127.0.0.1", oauth_port=0, auto_track=(),
        )
        db = SimpleNamespace(
            connect=AsyncMock(),
            invalidate_live_follow_counts_after_restart=AsyncMock(),
            invalidate_live_chat_stats_after_restart=AsyncMock(),
            all_telegram_channels=AsyncMock(return_value=[]),
            all_distinct_group_chat_ids=AsyncMock(return_value=[]),
            close=AsyncMock(),
        )
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )

        async def wait_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        class FakeDispatcher(dict):
            def __init__(self):
                super().__init__()
                self.start_polling = AsyncMock(side_effect=wait_forever)

        class FakeSession:
            exited = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                self.exited = True

        session = FakeSession()
        follow_listener = SimpleNamespace(
            run=AsyncMock(side_effect=wait_forever),
            wait_initial_ready=AsyncMock(),
            stop=AsyncMock(),
        )
        poller = SimpleNamespace(
            run=AsyncMock(side_effect=RuntimeError("poller crash")),
            stop=Mock(),
            shutdown=AsyncMock(side_effect=RuntimeError("cleanup failed")),
        )
        oauth_server = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())

        with (
            patch.object(main_module, "load_config", return_value=config),
            patch.object(main_module, "Database", return_value=db),
            patch.object(main_module, "Bot", return_value=bot),
            patch.object(main_module, "Dispatcher", side_effect=FakeDispatcher),
            patch.object(main_module, "setup_middlewares"),
            patch.object(main_module, "register_all_handlers"),
            patch.object(main_module.aiohttp, "ClientSession", return_value=session),
            patch.object(main_module, "TwitchClient", return_value=SimpleNamespace()),
            patch.object(main_module, "TokenStore", return_value=SimpleNamespace()),
            patch.object(main_module, "ChatListener", return_value=SimpleNamespace()),
            patch.object(main_module, "FollowEventListener", return_value=follow_listener),
            patch.object(main_module, "StreamPoller", return_value=poller),
            patch.object(main_module, "OAuthCallbackServer", return_value=oauth_server),
        ):
            with self.assertRaisesRegex(RuntimeError, "poller crash"):
                await main_module.main()

        poller.stop.assert_called_once()
        poller.shutdown.assert_awaited_once()
        follow_listener.stop.assert_awaited_once()
        oauth_server.stop.assert_awaited_once()
        db.close.assert_awaited_once()
        bot.session.close.assert_awaited_once()
        self.assertTrue(session.exited)


class HealthObservabilityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _healthy_snapshots():
        poller = {
            "running": True,
            "last_cycle_started_at": 995.0,
            "last_successful_cycle_at": 996.0,
            "last_successful_cycle_age_seconds": 4.0,
            "last_cycle_duration_seconds": 0.25,
            "last_cycle_error": None,
            "active_chat_listeners": 2,
            "background_tasks": 0,
            "stopping": False,
            "uptime_seconds": 100.0,
            "stale_after_seconds": 180,
        }
        eventsub = {
            "running": True,
            "stopping": False,
            "configured_logins": 2,
            "ready_logins": 2,
            "stalest_message_age_seconds": 10.0,
            "last_error": None,
            "last_error_age_seconds": None,
        }
        oauth = {"runner_started": True, "pending_states": 0}
        token = {"auth_blocked_logins": 0}
        database = {
            "pending_deliveries": 0,
            "oldest_pending_age_seconds": None,
            "deferred_reports": 0,
            "oldest_deferred_age_seconds": None,
            "stored_user_tokens": 2,
            "db_file_bytes": 4096,
            "wal_file_bytes": 0,
            "page_count": 10,
            "freelist_count": 1,
            "page_size": 4096,
        }
        return poller, eventsub, oauth, token, database

    async def test_health_shows_poller_last_success_and_error(self) -> None:
        poller = StreamPoller(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 60)
        poller._running = True
        poller._last_successful_cycle_at = 900.0
        poller._last_cycle_duration_seconds = 1.25
        poller._last_cycle_error = "RuntimeError"

        snapshot = poller.health_snapshot(1000.0)

        self.assertEqual(snapshot["last_successful_cycle_age_seconds"], 100.0)
        self.assertEqual(snapshot["last_cycle_error"], "RuntimeError")
        _poller, eventsub, oauth, token, database = self._healthy_snapshots()
        text = _build_health_text(snapshot, eventsub, oauth, token, database)
        self.assertIn("success 100 сек. назад", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("DEGRADED", text)

    async def test_pending_outbox_count_excludes_completed_deliveries(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            await db.create_report_delivery(1, "one", "pending", 1, "brief", "x", None, 100.0)
            complete = await db.create_report_delivery(
                2, "two", "complete", 2, "brief", "x", None, 200.0
            )
            await db.mark_report_text_sent(complete, 201.0)

            snapshot = await db.health_snapshot(1000.0)

            self.assertEqual(snapshot["pending_deliveries"], 1)
        finally:
            await db.close()

    async def test_oldest_pending_age_uses_delivery_creation_time(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            delivery = await db.create_report_delivery(
                1, "one", "pending", 1, "brief", "x", None, 100.0
            )
            await db.defer_report_delivery_retry(delivery, 900.0)

            snapshot = await db.health_snapshot(1000.0)

            self.assertEqual(snapshot["oldest_pending_age_seconds"], 900.0)
        finally:
            await db.close()

    async def test_deferred_count_and_oldest_age(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            await db.add_deferred_report(1, 1, "one", "stream-1", 100.0)
            await db.add_deferred_report(1, 2, "two", "stream-2", 250.0)

            snapshot = await db.health_snapshot(1000.0)

            self.assertEqual(snapshot["deferred_reports"], 2)
            self.assertEqual(snapshot["oldest_deferred_age_seconds"], 900.0)
        finally:
            await db.close()

    async def test_db_and_wal_bytes_fallback_for_memory_database(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            snapshot = await db.health_snapshot(1000.0)
            self.assertEqual(snapshot["db_file_bytes"], 0)
            self.assertEqual(snapshot["wal_file_bytes"], 0)
        finally:
            await db.close()

    async def test_missing_wal_and_filesystem_error_do_not_break_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                def file_size(path: str) -> int:
                    if path.endswith("-wal"):
                        raise FileNotFoundError(path)
                    raise OSError("mount unavailable")

                with patch("bot.database.os.path.getsize", side_effect=file_size):
                    snapshot = await db.health_snapshot(1000.0)
                self.assertIsNone(snapshot["db_file_bytes"])
                self.assertEqual(snapshot["wal_file_bytes"], 0)
            finally:
                await db.close()

    async def test_health_reports_page_and_freelist_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.conn.execute("CREATE TABLE health_freelist_probe (payload BLOB)")
                await db.conn.executemany(
                    "INSERT INTO health_freelist_probe VALUES (?)",
                    [(b"x" * 4096,) for _ in range(300)],
                )
                await db.conn.commit()
                await db.conn.execute("DELETE FROM health_freelist_probe")
                await db.conn.commit()

                snapshot = await db.health_snapshot()

                self.assertGreater(snapshot["page_count"], 0)
                self.assertGreater(snapshot["freelist_count"], 0)
                self.assertGreater(snapshot["page_size"], 0)
            finally:
                await db.close()

    async def test_eventsub_health_is_aggregate_and_tracks_stalest_message(self) -> None:
        listener = FollowEventListener(
            SimpleNamespace(), SimpleNamespace(), "client", SimpleNamespace()
        )
        listener._running = True
        listener._tasks = {"alpha": Mock(), "beta": Mock()}
        listener._ready = {"alpha": True, "beta": False}
        listener._last_message_at = {"alpha": 900.0, "beta": 990.0}
        listener._last_error = "ConnectionError"
        listener._last_error_at = 950.0

        snapshot = listener.health_snapshot(1000.0)

        self.assertEqual(snapshot["configured_logins"], 2)
        self.assertEqual(snapshot["ready_logins"], 1)
        self.assertEqual(snapshot["stalest_message_age_seconds"], 100.0)
        self.assertEqual(snapshot["last_error_age_seconds"], 50.0)
        self.assertNotIn("alpha", str(snapshot))
        self.assertNotIn("beta", str(snapshot))

    async def test_oauth_health_counts_states_without_exposing_them(self) -> None:
        server = OAuthCallbackServer("https://example.test/callback", "127.0.0.1", 0)
        server._runner = object()
        server.register_state("state-secret-one")
        server.register_state("state-secret-two")
        try:
            snapshot = server.health_snapshot()
            self.assertEqual(snapshot, {"runner_started": True, "pending_states": 2})
            self.assertNotIn("state-secret", str(snapshot))
        finally:
            server.discard_state("state-secret-one")
            server.discard_state("state-secret-two")
            server._runner = None

    async def test_token_health_counts_blocked_logins_without_exposing_tokens(self) -> None:
        store = TokenStore(
            SimpleNamespace(), "client", "client-secret", SimpleNamespace()
        )
        store._terminal_refresh_tokens = {
            "one": "refresh-secret-one",
            "two": "refresh-secret-two",
        }

        snapshot = store.health_snapshot()

        self.assertEqual(snapshot, {"auth_blocked_logins": 2})
        self.assertNotIn("refresh-secret", str(snapshot))

    async def test_health_is_owner_only_even_when_owner_is_missing(self) -> None:
        message = SimpleNamespace(
            chat=SimpleNamespace(id=7),
            answer=AsyncMock(),
        )
        never = Mock(side_effect=AssertionError("health dependency must not run"))
        poller = SimpleNamespace(health_snapshot=never)
        listener = SimpleNamespace(health_snapshot=never)
        oauth = SimpleNamespace(health_snapshot=never)
        token = SimpleNamespace(health_snapshot=never)
        db = SimpleNamespace(health_snapshot=AsyncMock(side_effect=AssertionError))

        await cmd_health(
            message,
            SimpleNamespace(owner_chat_id=None),
            db,
            poller,
            listener,
            oauth,
            token,
        )
        await cmd_health(
            message,
            SimpleNamespace(owner_chat_id=8),
            db,
            poller,
            listener,
            oauth,
            token,
        )

        message.answer.assert_not_awaited()
        never.assert_not_called()
        db.health_snapshot.assert_not_awaited()

    async def test_health_command_is_read_only(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            await db.add_deferred_report(7, 7, "channel", "stream", 100.0)
            await db.create_report_delivery(
                7, "channel", "stream", 7, "brief", "payload", None, 100.0
            )
            before = db.conn.total_changes
            poller, eventsub, oauth, token, _database = self._healthy_snapshots()
            message = SimpleNamespace(
                chat=SimpleNamespace(id=7),
                answer=AsyncMock(),
            )

            await cmd_health(
                message,
                SimpleNamespace(owner_chat_id=7),
                db,
                SimpleNamespace(health_snapshot=Mock(return_value=poller)),
                SimpleNamespace(health_snapshot=Mock(return_value=eventsub)),
                SimpleNamespace(health_snapshot=Mock(return_value=oauth)),
                SimpleNamespace(health_snapshot=Mock(return_value=token)),
            )

            self.assertEqual(db.conn.total_changes, before)
            self.assertEqual(len(await db.pending_report_deliveries()), 1)
            self.assertTrue(await db.has_deferred_reports(7))
            message.answer.assert_awaited_once()
        finally:
            await db.close()

    async def test_large_health_snapshot_uses_aggregate_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                await db.conn.executemany(
                    "INSERT INTO tracked_channels (chat_id, twitch_login) VALUES (?, ?)",
                    [(index + 1, f"channel{index}") for index in range(100)],
                )
                deliveries = []
                for index in range(1000):
                    deliveries.append(
                        (
                            index + 1,
                            f"channel{index}",
                            f"stream{index}",
                            index + 1,
                            "brief",
                            "payload",
                            None,
                            int(index >= 500),
                            0,
                            0,
                            None,
                            1000.0 + index,
                            1000.0 + index,
                        )
                    )
                await db.conn.executemany(
                    "INSERT INTO report_deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    deliveries,
                )
                await db.conn.executemany(
                    "INSERT INTO deferred_reports VALUES (?, ?, ?, ?, ?)",
                    [
                        (7, index + 1, f"channel{index}", f"stream{index}", 1000.0 + index)
                        for index in range(1000)
                    ],
                )
                await db.conn.execute("CREATE TABLE health_freelist_probe (payload BLOB)")
                await db.conn.executemany(
                    "INSERT INTO health_freelist_probe VALUES (?)",
                    [(b"x" * 4096,) for _ in range(300)],
                )
                await db.conn.commit()
                await db.conn.execute("DELETE FROM health_freelist_probe")
                await db.conn.commit()

                statements: list[str] = []
                await db.conn.set_trace_callback(statements.append)
                started = time.perf_counter()
                snapshot = await db.health_snapshot(5000.0)
                elapsed = time.perf_counter() - started
                await db.conn.set_trace_callback(None)

                self.assertEqual(snapshot["pending_deliveries"], 500)
                self.assertEqual(snapshot["deferred_reports"], 1000)
                self.assertGreater(snapshot["freelist_count"], 0)
                self.assertGreaterEqual(elapsed, 0.0)
                self.assertFalse(any("stream_history" in sql for sql in statements))

                plans = {
                    "idx_report_deliveries_pending_created": (
                        "SELECT COUNT(*), MIN(created_at) FROM report_deliveries "
                        "WHERE terminal_failed = 0 AND (text_sent = 0 OR "
                        "(report_format = 'full' AND html_sent = 0))"
                    ),
                    "idx_deferred_reports_health": (
                        "SELECT COUNT(*), MIN(ended_at) FROM deferred_reports"
                    ),
                }
                for expected_index, query in plans.items():
                    cursor = await db.conn.execute(f"EXPLAIN QUERY PLAN {query}")
                    plan = " ".join(str(row) for row in await cursor.fetchall())
                    self.assertIn(expected_index, plan)
            finally:
                await db.close()

    async def test_health_text_contains_no_secrets_paths_or_state_values(self) -> None:
        poller, eventsub, oauth, token, database = self._healthy_snapshots()
        poller["last_cycle_error"] = "RuntimeError: access-secret C:\\private\\bot.db"
        eventsub["last_error"] = "refresh-secret"

        text = _build_health_text(poller, eventsub, oauth, token, database)

        self.assertNotIn("access-secret", text)
        self.assertNotIn("refresh-secret", text)
        self.assertNotIn("C:\\private", text)
        self.assertNotIn("state", text.lower())
        self.assertLess(len(text), 4096)

    async def test_degraded_threshold_is_exactly_three_poll_intervals(self) -> None:
        poller, eventsub, oauth, token, database = self._healthy_snapshots()
        poller["stale_after_seconds"] = 180
        database["pending_deliveries"] = 1
        database["oldest_pending_age_seconds"] = 180.0
        self.assertIn(
            "Состояние: OK",
            _build_health_text(poller, eventsub, oauth, token, database),
        )

        database["oldest_pending_age_seconds"] = 181.0
        self.assertIn(
            "Состояние: DEGRADED",
            _build_health_text(poller, eventsub, oauth, token, database),
        )

    async def test_unavailable_storage_metrics_degrade_health_without_crashing(self) -> None:
        poller, eventsub, oauth, token, database = self._healthy_snapshots()
        database["db_file_bytes"] = None

        text = _build_health_text(poller, eventsub, oauth, token, database)

        self.assertIn("Состояние: DEGRADED", text)
        self.assertIn("DB: <b>н/д</b>", text)

    async def test_repeated_identical_poller_failure_logs_once(self) -> None:
        poller = StreamPoller(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), 0.001)
        attempts = 0

        async def fail_twice() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                poller.stop()
            raise RuntimeError("same outage")

        poller._check_once = fail_twice
        with self.assertLogs("bot.poller", level="ERROR") as captured:
            await poller.run()

        errors = [line for line in captured.output if "Ошибка в цикле опроса Twitch" in line]
        self.assertEqual(attempts, 2)
        self.assertEqual(len(errors), 1)

    async def test_terminal_outbox_log_excludes_payload_and_recipient(self) -> None:
        db = Database(":memory:")
        await db.connect()
        try:
            delivery = await db.create_report_delivery(
                1,
                "channel",
                "stream",
                987654321,
                "brief",
                "access-secret payload",
                None,
                100.0,
            )
            with self.assertLogs("bot.database", level="WARNING") as captured:
                await db.mark_report_delivery_terminal(delivery, "destination_rejected", 101.0)

            logs = "\n".join(captured.output)
            self.assertIn("terminal", logs)
            self.assertIn("destination_rejected", logs)
            self.assertNotIn("access-secret", logs)
            self.assertNotIn("987654321", logs)
        finally:
            await db.close()


class TelegramChannelRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_reconciliation_keeps_valid_admin_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "News")
                await db.add_channel(channel_id, "channel")
                bot = SimpleNamespace(
                    id=99,
                    get_chat=AsyncMock(return_value=SimpleNamespace(type="channel")),
                    get_chat_member=AsyncMock(
                        return_value=SimpleNamespace(status="administrator")
                    ),
                )

                await _reconcile_telegram_channels(bot, db)

                self.assertTrue(await db.is_telegram_channel(channel_id))
                self.assertEqual(await db.list_channels(channel_id), ["channel"])
            finally:
                await db.close()

    async def test_stale_channel_pending_html_becomes_terminal_after_tracking_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "test.db"))
            await db.connect()
            try:
                channel_id = -100456
                await db.register_telegram_channel(channel_id, "Old channel")
                await db.add_channel(channel_id, "channel")
                delivery = await db.create_report_delivery(
                    channel_id,
                    "channel",
                    "stream-1",
                    channel_id,
                    "full",
                    "persisted text",
                    "<html>pending</html>",
                    1000.0,
                )
                await db.mark_report_text_sent(delivery, 1001.0)
                bot = SimpleNamespace(
                    id=99,
                    get_chat=AsyncMock(
                        return_value=SimpleNamespace(type="supergroup")
                    ),
                    get_chat_member=AsyncMock(
                        return_value=SimpleNamespace(status="administrator")
                    ),
                    send_message=AsyncMock(),
                    send_document=AsyncMock(),
                )

                await _reconcile_telegram_channels(bot, db)
                self.assertEqual(await db.list_channels(channel_id), [])

                poller = StreamPoller(bot, db, SimpleNamespace(), 60)
                await poller._send_pending_stats()

                updated = await db.get_report_delivery_for_stream(
                    channel_id, "channel", "stream-1"
                )
                self.assertIsNotNone(updated)
                self.assertTrue(updated.text_sent)
                self.assertFalse(updated.html_sent)
                self.assertTrue(updated.terminal_failed)
                self.assertEqual(updated.terminal_reason, "destination_rejected")
                bot.send_message.assert_not_awaited()
                bot.send_document.assert_not_awaited()
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
