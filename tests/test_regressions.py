from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TWITCH_CLIENT_ID": "test-client",
    "TWITCH_CLIENT_SECRET": "test-secret",
}


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

    async def test_live_post_is_not_cleanup_candidate_before_stats_are_handled(self) -> None:
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

                self.assertEqual(await db.pending_offline_posts(), [])

                await db.mark_stats_sent(1, "channel")
                self.assertEqual(
                    await db.pending_offline_posts(),
                    [(1, "channel", 10, 1.0)],
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

                self.assertEqual(
                    await db.snapshot_last_stream_ends(),
                    {(1, "channel"): 1234.0},
                )

                await db.clear_message(1, "channel")
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
        self.assertEqual(RESTART_MERGE_GRACE_SECONDS, 3 * 60)
        self.assertEqual(OFFLINE_GRACE_SECONDS, 5 * 60)

    async def test_transient_delete_failure_keeps_message_for_retry(self) -> None:
        db = SimpleNamespace(
            pending_offline_posts=AsyncMock(return_value=[(1, "channel", 10, 0.0)]),
            clear_message=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=_FAILED)

        await poller._cleanup_offline_posts()

        db.clear_message.assert_not_awaited()

    async def test_successful_delete_clears_message_state(self) -> None:
        db = SimpleNamespace(
            pending_offline_posts=AsyncMock(return_value=[(1, "channel", 10, 0.0)]),
            clear_message=AsyncMock(),
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._tg_call = AsyncMock(return_value=None)

        await poller._cleanup_offline_posts()

        db.clear_message.assert_awaited_once_with(1, "channel")


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
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._send_stats = AsyncMock(return_value=True)

        await poller._send_pending_stats()

        poller._send_stats.assert_awaited_once()
        db.mark_stats_sent.assert_awaited_once_with(1, "channel")

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
        )
        poller = StreamPoller(SimpleNamespace(), db, SimpleNamespace(), 60)
        poller._send_stats = AsyncMock(return_value=False)

        await poller._send_pending_stats()

        db.mark_stats_sent.assert_not_awaited()


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
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
