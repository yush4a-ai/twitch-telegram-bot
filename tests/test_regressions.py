from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cryptography.fernet import Fernet

from bot.config import load_config
from bot.database import Database
from bot.handlers.streams import (
    TWITCH_CUSTOM_EMOJI_ID,
    _build_live_list,
    _format_viewers,
    _message_can_manage_chat,
)
from bot.oauth import OAuthCallbackServer
from bot.poller import (
    OFFLINE_GRACE_SECONDS,
    RESTART_MERGE_GRACE_SECONDS,
    StreamPoller,
    _FAILED,
)
from bot.twitch import StreamInfo


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


class ConfigTests(unittest.TestCase):
    def test_poll_interval_must_be_positive(self) -> None:
        with patch.dict(os.environ, {**REQUIRED_ENV, "POLL_INTERVAL_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "POLL_INTERVAL_SECONDS.*больше нуля"):
                load_config()


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
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
    async def test_channel_stream_generates_and_marks_final_report(self) -> None:
        db = SimpleNamespace(
            pending_stats=AsyncMock(
                return_value=[
                    (
                        1, "channel", 0.0, "Stream", "2026-01-01T00:00:00Z",
                        10, 20, 2, "stream-1", None,
                    )
                ]
            ),
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
            pending_stats=AsyncMock(
                return_value=[
                    (
                        1, "channel", 0.0, "Stream", "2026-01-01T00:00:00Z",
                        10, 20, 2, "stream-1", None,
                    )
                ]
            ),
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

    async def test_group_without_private_recipient_never_delivers_report(self) -> None:
        db = SimpleNamespace(
            pending_stats=AsyncMock(
                return_value=[
                    (-100, "channel", 0.0, "title", "2026-01-01T00:00:00Z", 10, 20, 2, "stream", None)
                ]
            ),
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


if __name__ == "__main__":
    unittest.main()
