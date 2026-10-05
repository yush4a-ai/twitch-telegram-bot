"""Public explanations and report dates; real storage, fake external transport."""
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from bot.database import Database
from bot.handlers import streams, telegram_help, telegram_plus
from bot.plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE
from bot.poller import StreamPoller
from tests.test_telegram_navigation import message, CONFIG


class HelpReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(':memory:')
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.mark_known_private_user(101)
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=999, chat_id=101, user_id=101))
        self.msg = message()
        self.msg.bot = SimpleNamespace(send_message=AsyncMock(), send_document=AsyncMock())

    def cb(self, data):
        return SimpleNamespace(data=data, message=self.msg, from_user=self.msg.from_user, answer=AsyncMock())

    async def test_help_has_guides_without_requiring_a_subscription(self):
        text, keyboard = telegram_help.help_screen(CONFIG)
        actions = {b.callback_data for row in keyboard.inline_keyboard for b in row}
        for topic in ('about', 'import', 'quiet', 'reports'):
            self.assertIn('help:topic:' + topic, actions)
        self.assertIn('ник или ссылку Twitch', text)
        expected = {'about': 'фото', 'import': 'подтверждения', 'quiet': 'Рейды', 'reports': 'HTML'}
        for topic, detail in expected.items():
            await telegram_help.cb_help_topic(self.cb('help:topic:' + topic))
            self.assertIn(detail, self.msg.edit_text.await_args.args[0])
            self.assertEqual(self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[-1][0].callback_data, 'menu:help')
        self.msg.edit_text.reset_mock()
        await telegram_help.cb_help_topic(self.cb('help:topic:unknown'))
        self.msg.edit_text.assert_not_awaited()

    async def test_tariff_explains_personal_slots_and_payment_off_before_checkout(self):
        await telegram_plus.cb_plus(self.cb('menu:plus'), self.state, self.db, CONFIG)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn('включая тех, кто сейчас не в эфире', text)
        self.assertIn('В Free: фото', text)
        self.assertIn(PAYMENT_UNAVAILABLE_MESSAGE, text)
        self.assertFalse(await self.db.has_viewer_plus(101))
        await telegram_plus.cb_buy(self.cb('plus:buy:viewer_plus'), self.state, self.db)
        self.assertIn(PAYMENT_UNAVAILABLE_MESSAGE, self.msg.edit_text.await_args.args[0])

    async def seed(self):
        await self.db.add_channel(101, 'channel')
        end = datetime(2026, 10, 2, 11, tzinfo=timezone.utc).timestamp()
        await self.db.add_stream_history(101, 'channel', 'stream-1', end, 3600, 100, 50, 10,
            started_at='2026-10-02T10:00:00Z', title='Длинное название <эфира>',
            new_followers_text='10', join_reliable=False)
        return end

    def assert_period(self, text):
        self.assertIn('Начало: 02.10.2026, 10:00 UTC', text)
        self.assertIn('Завершение: 02.10.2026, 11:00 UTC', text)

    async def test_manual_free_report_has_stream_dates_and_retains_html(self):
        end = await self.seed()
        await self.db.set_report_format(101, 'channel', 'full')
        with patch('bot.handlers.streams.time.time', return_value=end + 3600):
            self.assertTrue(await streams._deliver_report(self.msg, 101, 'channel', self.db))
        text = self.msg.bot.send_message.await_args.args[1]
        self.assert_period(text)
        self.assertIn('Данные чата: неполные', text)
        self.assertIn('&lt;эфира&gt;', text)
        file = self.msg.bot.send_document.await_args.args[1]
        self.assertTrue(file.filename.endswith('.html'))
        self.assertIn('UTC', file.data.decode('utf-8'))
        self.assertFalse(await self.db.has_viewer_plus(101))

    async def test_missing_start_is_unknown_instead_of_delivery_date(self):
        end = await self.seed()
        await self.db.conn.execute('UPDATE stream_history SET started_at=NULL')
        await self.db.conn.commit()
        await streams._deliver_report(self.msg, 101, 'channel', self.db)
        self.assertIn('Начало: не сохранено', self.msg.bot.send_message.await_args.args[1])

    async def test_automatic_report_persists_the_same_stream_period(self):
        end = await self.seed()
        await self.db.set_auto_report_enabled(101, 'channel', True)
        poller = StreamPoller(self.msg.bot, self.db, SimpleNamespace(), 60)
        poller._compute_new_followers = AsyncMock(return_value=None)
        poller._fetch_top_clips = AsyncMock(return_value=[])
        poller._fetch_and_save_vod = AsyncMock(return_value=None)
        await poller._send_stats(101, 'channel', 'stream-2', 'Название',
            '2026-10-02T10:00:00Z', 100, 50, 1, None, ended_at=end)
        self.assert_period(self.msg.bot.send_message.await_args.args[1])
        row = await self.db.get_report_delivery_for_stream(101, 'channel', 'stream-2')
        self.assert_period(row.text_payload)
