from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.config import load_config
from bot.database import Database
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
                    },
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
