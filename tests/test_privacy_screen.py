"""Экран «Мои данные»: выгрузка, удаление и защита от случайного удаления."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database import Database
from bot.handlers import telegram_help


class PrivacyScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(':memory:')
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.remember_profile(
            101, username='tester', display_name='Тест', language_code='ru', now=1000.0,
        )
        await self.db.add_channel(101, 'alpha')
        self.msg = SimpleNamespace(
            edit_text=AsyncMock(), answer_document=AsyncMock(), chat=SimpleNamespace(id=101),
        )

    def cb(self, data, user_id=101):
        return SimpleNamespace(
            data=data, message=self.msg,
            from_user=SimpleNamespace(id=user_id, first_name='Тест'), answer=AsyncMock(),
        )

    async def test_screen_shows_stored_data_and_actions(self):
        await telegram_help.cb_help_data(self.cb('help:data'), db=self.db)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn('Мои данные', text)
        self.assertIn('101', text)
        actions = [b.callback_data for row in self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertEqual(actions, ['privacy:export', 'privacy:delete', 'menu:help'])
        self.assertIn('требует закон', text)

    async def test_export_sends_person_file(self):
        await telegram_help.cb_privacy_export(self.cb('privacy:export'), db=self.db)
        document = self.msg.answer_document.await_args.args[0]
        payload = json.loads(document.data.decode('utf-8'))
        self.assertEqual(payload['telegram_id'], 101)
        self.assertEqual([row['twitch_login'] for row in payload['channels']], ['alpha'])
        self.assertTrue(document.filename.endswith('.json'))

    async def test_delete_needs_explicit_confirmation(self):
        await telegram_help.cb_privacy_delete(self.cb('privacy:delete'))
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn('Вернуть их нельзя', text)
        actions = [b.callback_data for row in self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertEqual(actions, ['privacy:delete:confirm', 'help:data'])
        # До подтверждения данные остаются на месте.
        self.assertEqual(await self.db.list_channels(101), ['alpha'])

    async def test_confirm_deletes_only_that_person(self):
        await self.db.add_channel(202, 'other')
        await telegram_help.cb_privacy_delete_confirm(self.cb('privacy:delete:confirm'), db=self.db)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn('Данные удалены', text)
        self.assertEqual(await self.db.list_channels(101), [])
        self.assertEqual(await self.db.list_channels(202), ['other'])
        cursor = await self.db.conn.execute('SELECT COUNT(*) FROM telegram_user_profiles WHERE user_id=101')
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_help_screen_offers_personal_data_screen(self):
        _text, keyboard = telegram_help.help_screen(None)
        actions = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertIn('help:data', actions)
