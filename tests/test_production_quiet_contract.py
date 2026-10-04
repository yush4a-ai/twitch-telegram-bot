import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from bot.handlers import telegram_help, streams
from tests.test_mini_app_legacy_compat import QuietHoursCompatibilityTests


class QuietCopyTests(unittest.TestCase):
    def test_help_tells_the_truth_about_personal_raids(self):
        text = telegram_help.TOPICS['quiet'][1]
        self.assertIn('рейдах', text)
        self.assertNotIn('Рейды не входят', text)
        self.assertIn('настройкам канала', text)



class QuietGuideTests(unittest.IsolatedAsyncioTestCase):
    async def test_quiet_guide_does_not_promise_raid_exemption(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=101), answer=AsyncMock())
        state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
        await streams._preview_quiet_hours(message, state, 101, 0, 60, 0)
        text = message.answer.await_args.args[0]
        self.assertIn('рейдах', text)
        self.assertNotIn('Рейды не входят', text)


class QuietOutsideTests(QuietHoursCompatibilityTests):
    async def test_free_live_and_raid_outside_quiet(self):
        await self.db.clear_quiet_hours(101)
        self.assertFalse(await self.db.has_viewer_plus(101))
        self.assertEqual(await self.poller._notify(101, 'alpha', 'Live', 5, 'Game'), 700)
        await self.poller._notify_raid('alpha', 'raider', 42)
        self.assertEqual(self.bot.send_message.await_count, 2)
