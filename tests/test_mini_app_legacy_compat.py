"""Production journey A3/A4 against a fresh local database and fake Telegram."""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import MenuButtonCommands, MenuButtonWebApp

from bot.database import Database
from bot.notification_queue import NotificationJob
from bot.notification_worker import NotificationOutcome, NotificationRetryAfter
from bot.poller import StreamPoller


class QuietHoursCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "compat.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.add_channel(101, "alpha")
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=700)))
        self.poller = StreamPoller(self.bot, self.db, SimpleNamespace(), 60)
        self.poller._build_private_live_text = AsyncMock(return_value="Live")
        self.poller._build_keyboard = AsyncMock(return_value=None)
        self.poller._viewer_allows_private_alert = AsyncMock(return_value=True)
        now = datetime.now(timezone.utc)
        minute = now.hour * 60 + now.minute
        await self.db.set_quiet_hours(101, (minute - 1) % 1440, (minute + 2) % 1440, 0)

    async def test_private_raid_obeys_quiet_hours_and_explicit_exemption(self):
        await self.db.add_channel(-100101, "alpha")
        await self.poller._notify_raid("alpha", "raider", 42)
        self.assertEqual([call.args[0] for call in self.bot.send_message.await_args_list], [-100101])
        await self.db.set_quiet_hours_exempt(101, "alpha", True)
        await self.poller._notify_raid("alpha", "raider", 42)
        self.assertEqual([call.args[0] for call in self.bot.send_message.await_args_list],
                         [-100101, 101, -100101])

    async def test_private_live_direct_and_queue_wait_for_quiet_end(self):
        self.assertIsNone(await self.poller._notify(101, "alpha", "Live", 5, "Game"))
        self.bot.send_message.assert_not_awaited()
        await self.db.set_live_state(
            101, "alpha", True, "live-1", None, "Live", broadcaster_id="1001",
        )
        self.db.list_live_channels = AsyncMock(return_value=[("alpha", "Live", 5, "Game")])
        job = NotificationJob(
            1, "go_live", 101, "alpha", "live-1", 0, time.time(), 1,
            time.time() + 180,
        )
        with self.assertRaises(NotificationRetryAfter):
            await self.poller.send_queued_job(job)
        self.bot.send_message.assert_not_awaited()
        await self.db.clear_quiet_hours(101)
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.bot.send_message.assert_awaited_once()

    async def test_private_live_exemption_and_group_live_remain_available(self):
        await self.db.set_quiet_hours_exempt(101, "alpha", True)
        self.assertEqual(await self.poller._notify(101, "alpha", "Live", 5, "Game"), 700)
        await self.db.add_channel(-100101, "alpha")
        self.poller._build_live_text = AsyncMock(return_value="Group live")
        self.assertEqual(await self.poller._notify(-100101, "alpha", "Live", 5, "Game"), 700)
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_raid_rechecks_quiet_hours_after_telegram_retry_wait(self):
        await self.db.clear_quiet_hours(101)

        async def delayed_retry(send, _description):
            now = datetime.now(timezone.utc)
            minute = now.hour * 60 + now.minute
            await self.db.set_quiet_hours(101, (minute - 1) % 1440, (minute + 2) % 1440, 0)
            return await send()

        self.poller._tg_call = delayed_retry
        await self.poller._notify_raid("alpha", "raider", 42)
        self.bot.send_message.assert_not_awaited()

    async def test_raid_rechecks_toggle_after_telegram_retry_wait(self):
        await self.db.clear_quiet_hours(101)

        async def delayed_retry(send, _description):
            await self.db.set_raid_detection_enabled(101, "alpha", False)
            return await send()

        self.poller._tg_call = delayed_retry
        await self.poller._notify_raid("alpha", "raider", 42)
        self.bot.send_message.assert_not_awaited()

    async def test_live_rechecks_quiet_hours_after_template_resolution(self):
        await self.db.clear_quiet_hours(101)

        async def resolving_template(_chat_id, _login, content):
            now = datetime.now(timezone.utc)
            minute = now.hour * 60 + now.minute
            await self.db.set_quiet_hours(101, (minute - 1) % 1440, (minute + 2) % 1440, 0)
            return content

        self.poller._with_streamer_template = resolving_template
        self.assertIsNone(await self.poller._notify(101, "alpha", "Live", 5, "Game"))
        self.bot.send_message.assert_not_awaited()

    async def test_raid_and_rename_skip_inactive_private_channel_but_keep_group(self):
        await self.db.clear_quiet_hours(101)
        for index in range(50):
            await self.db.add_channel(101, f"track{index:02}")
        await self.db.add_channel(101, "zeta")
        self.assertFalse(await self.db.is_personal_channel_active(101, "zeta"))
        await self.db.add_channel(-100101, "zeta")
        await self.poller._notify_raid("zeta", "raider", 42)
        await self.poller._notify_channel_renamed("zeta", "Old", "New")
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertTrue(all(call.args[0] == -100101 for call in self.bot.send_message.await_args_list))

    async def test_rename_obeys_private_quiet_hours(self):
        await self.poller._notify_channel_renamed("alpha", "Old", "New")
        self.bot.send_message.assert_not_awaited()


class MenuButtonCompatibilityTests(unittest.TestCase):
    def test_restart_button_uses_app_only_for_pinned_testbot(self):
        from main import _menu_button_for_config

        config = SimpleNamespace(
            mini_app_enabled=True, pinned_staging=True,
            admin_telegram_bot_username="SignalStreamsBot",
            oauth_public_base_url="https://staging.example.test",
        )
        button = _menu_button_for_config(config)
        self.assertIsInstance(button, MenuButtonWebApp)
        self.assertEqual(button.text, "Приложение")
        self.assertEqual(button.web_app.url, "https://staging.example.test/app")
        self.assertIsInstance(_menu_button_for_config(SimpleNamespace(**{**vars(config), "pinned_staging": False})), MenuButtonCommands)
        self.assertIsInstance(_menu_button_for_config(SimpleNamespace(**{**vars(config), "admin_telegram_bot_username": "other"})), MenuButtonCommands)
        self.assertIsInstance(_menu_button_for_config(SimpleNamespace(**{**vars(config), "mini_app_enabled": False})), MenuButtonCommands)
        self.assertIsInstance(_menu_button_for_config(SimpleNamespace(**{**vars(config), "oauth_public_base_url": "http://staging.example.test"})), MenuButtonCommands)

    def test_production_admission_uses_app_button_with_public_url(self):
        from main import _menu_button_for_config

        config = SimpleNamespace(
            mini_app_enabled=True,
            production_admitted=True,
            production_contract=object(),
            admin_telegram_bot_username="TwitchSignalBot",
            oauth_public_base_url="https://worker-production-cee5.up.railway.app",
        )
        button = _menu_button_for_config(config)
        self.assertIsInstance(button, MenuButtonWebApp)
        self.assertEqual(button.text, "Приложение")
        self.assertEqual(
            button.web_app.url,
            "https://worker-production-cee5.up.railway.app/app",
        )
        for mutation in (
            {"production_contract": None},
            {"production_admitted": False},
            {"mini_app_enabled": False},
        ):
            with self.subTest(mutation=mutation):
                self.assertIsInstance(
                    _menu_button_for_config(SimpleNamespace(**{**vars(config), **mutation})),
                    MenuButtonCommands,
                )


class StagingIdentityCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_bot_identity_must_match_before_staging_menu_writes(self):
        from bot.config import ConfigError
        from main import _verify_staging_bot_identity

        config = SimpleNamespace(mini_app_enabled=True, pinned_staging=True,
                                 admin_telegram_bot_username="SignalStreamsBot")
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="AnotherBot")))
        with self.assertRaises(ConfigError):
            await _verify_staging_bot_identity(bot, config)
        bot.get_me.assert_awaited_once()

    async def test_nonstaging_identity_guard_does_not_call_telegram(self):
        from main import _verify_staging_bot_identity

        config = SimpleNamespace(mini_app_enabled=True, pinned_staging=False,
                                 admin_telegram_bot_username="SignalStreamsBot")
        bot = SimpleNamespace(get_me=AsyncMock())
        await _verify_staging_bot_identity(bot, config)
        bot.get_me.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
