from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from bot.config import load_config
from bot.database import Database
from bot.handlers.streams import _build_live_list, _format_viewers, _message_can_manage_chat
from bot.oauth import OAuthCallbackServer
from bot.poller import StreamPoller, _FAILED


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


class PollerCleanupTests(unittest.IsolatedAsyncioTestCase):
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


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
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
