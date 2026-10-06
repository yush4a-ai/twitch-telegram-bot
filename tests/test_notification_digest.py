"""Сводки уведомлений: несколько вышедших каналов одним сообщением.

Владелец тестирует сводки на staging, поэтому здесь же проверяется, что в
production флаг включить нельзя: цена ошибки — молчаливая смена поведения у
живых людей.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.config import ConfigError, load_config
from bot.database import Database
from bot.poller import StreamPoller

RAILWAY_ENV = {
    "TELEGRAM_BOT_TOKEN": "123:token",
    "TWITCH_CLIENT_ID": "client",
    "TWITCH_CLIENT_SECRET": "secret",
    "TOKEN_ENCRYPTION_KEY": "test-key",
    "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
    "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
    "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
    "RAILWAY_VOLUME_MOUNT_PATH": "/data",
    "RAILWAY_PUBLIC_DOMAIN": "test.example.up.railway.app",
    "DB_PATH": "/data/bot.db",
}


class DigestConfigTests(unittest.TestCase):
    def test_digest_is_allowed_only_on_pinned_staging(self):
        flag = {"NOTIFICATION_DIGEST_ENABLED": "1"}
        with patch.dict(
            os.environ, {**RAILWAY_ENV, **flag, "RAILWAY_ENVIRONMENT_NAME": "staging"},
            clear=True,
        ):
            self.assertTrue(load_config().notification_digest_enabled)
        with patch.dict(
            os.environ, {**RAILWAY_ENV, **flag, "RAILWAY_ENVIRONMENT_NAME": "production"},
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                load_config()

    def test_digest_is_off_by_default(self):
        with patch.dict(os.environ, dict(RAILWAY_ENV), clear=True):
            config = load_config()
        self.assertFalse(config.notification_digest_enabled)
        self.assertEqual(config.notification_digest_window_seconds, 300)
        self.assertEqual(config.notification_digest_max_lines, 5)

    def test_window_and_lines_are_bounded(self):
        with patch.dict(
            os.environ,
            {
                **RAILWAY_ENV,
                "NOTIFICATION_DIGEST_ENABLED": "1",
                "NOTIFICATION_DIGEST_WINDOW_SECONDS": "1",
                "NOTIFICATION_DIGEST_MAX_LINES": "999",
                "RAILWAY_ENVIRONMENT_NAME": "staging",
            },
            clear=True,
        ):
            config = load_config()
        self.assertEqual(config.notification_digest_window_seconds, 30)
        self.assertEqual(config.notification_digest_max_lines, 10)


class DigestStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_same_stream_is_not_queued_twice(self):
        first = await self.db.queue_notification_digest(
            101, "alpha", "stream-1", "Title", "Game", 10, now=1000.0
        )
        again = await self.db.queue_notification_digest(
            101, "alpha", "stream-1", "Title", "Game", 12, now=1005.0
        )

        self.assertTrue(first)
        self.assertFalse(again)
        self.assertEqual(await self.db.notification_digest_pending_count(101), 1)

    async def test_entries_wait_for_the_window_then_leave_the_queue(self):
        await self.db.queue_notification_digest(101, "alpha", "s1", "A", None, 1, now=1000.0)
        await self.db.queue_notification_digest(101, "beta", "s2", "B", "Game", 2, now=1001.0)

        self.assertEqual(
            await self.db.list_due_notification_digests(now=1200.0, window_seconds=300), []
        )
        self.assertEqual(
            await self.db.list_due_notification_digests(now=1301.0, window_seconds=300), [101]
        )

        entries = await self.db.notification_digest_entries(101, limit=10)
        await self.db.mark_notification_digest_sent([row[0] for row in entries], now=1301.0)

        self.assertEqual(await self.db.notification_digest_pending_count(101), 0)
        self.assertEqual(
            await self.db.list_due_notification_digests(now=1400.0, window_seconds=300), []
        )

    async def test_opt_out_drops_what_was_already_collected(self):
        await self.db.queue_notification_digest(101, "alpha", "s1", "A", None, 1, now=1000.0)

        await self.db.drop_notification_digest_chat(101)

        self.assertEqual(await self.db.notification_digest_pending_count(101), 0)


class DigestMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.poller = StreamPoller(
            SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1))),
            self.db,
            SimpleNamespace(),
            60,
            notification_digest_enabled=True,
            notification_digest_max_lines=2,
        )

    async def asyncTearDown(self):
        await self.db.close()

    def entries(self, count: int):
        return [
            (index, f"channel{index}", "Title", "Game", index * 10)
            for index in range(1, count + 1)
        ]

    def test_message_lists_the_channels_and_hides_the_rest(self):
        text, keyboard = self.poller._build_digest_message(self.entries(4), total=4)

        self.assertIn("Сейчас в эфире: 4", text)
        self.assertIn("channel1", text)
        self.assertIn("channel2", text)
        self.assertNotIn("channel3", text)
        self.assertIn("и ещё 2", text)
        self.assertEqual(len(keyboard.inline_keyboard), 2)
        self.assertEqual(
            keyboard.inline_keyboard[0][0].url, "https://www.twitch.tv/channel1"
        )

    def test_single_channel_message_has_no_remainder_line(self):
        text, keyboard = self.poller._build_digest_message(self.entries(1), total=1)

        self.assertIn("Сейчас в эфире: 1", text)
        self.assertNotIn("и ещё", text)
        self.assertEqual(len(keyboard.inline_keyboard), 1)

    async def test_digest_is_sent_once_and_leaves_the_queue(self):
        await self.db.queue_notification_digest(101, "alpha", "s1", "A", "Game", 5, now=1000.0)

        await self.poller._flush_notification_digests(now=1400.0)

        self.poller._bot.send_message.assert_awaited_once()
        text = self.poller._bot.send_message.await_args.args[1]
        self.assertIn("alpha", text)
        self.assertEqual(await self.db.notification_digest_pending_count(101), 0)

    async def test_blocked_chat_stops_accumulating(self):
        self.poller._bot.send_message = AsyncMock(
            side_effect=RuntimeError("blocked")
        )
        await self.db.queue_notification_digest(101, "alpha", "s1", "A", None, 1, now=1000.0)

        with patch.object(self.poller, "_tg_call", AsyncMock(return_value=None)):
            await self.poller._flush_notification_digests(now=1400.0)

        self.assertEqual(await self.db.notification_digest_pending_count(101), 0)


class DigestEligibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    def poller(self, *, enabled: bool):
        return StreamPoller(
            SimpleNamespace(), self.db, SimpleNamespace(), 60,
            notification_digest_enabled=enabled,
        )

    async def test_group_chats_and_disabled_flag_never_use_the_digest(self):
        self.assertFalse(await self.poller(enabled=True)._digest_eligible(-1001, "alpha"))
        self.assertFalse(await self.poller(enabled=False)._digest_eligible(101, "alpha"))

    async def test_private_chat_without_live_video_goes_to_the_digest(self):
        self.assertTrue(await self.poller(enabled=True)._digest_eligible(101, "alpha"))


if __name__ == "__main__":
    unittest.main()
