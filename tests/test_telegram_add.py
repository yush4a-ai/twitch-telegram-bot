import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database import Database
from bot.handlers.streams import cb_menu_add, process_login_input, cb_add_found_channel, _extract_login_text
from tests.test_telegram_navigation import message


class TelegramAddTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.db = Database(str(Path(temp.name)/'add.db'))
        await self.db.connect(); self.addAsyncCleanup(self.db.close)
        self.state = FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        self.msg = message(); self.msg.text='https://www.twitch.tv/Alpha?ref=telegram'
        self.twitch = SimpleNamespace(channel_exists=AsyncMock(return_value=True),search_channels=AsyncMock(return_value=[]))

    def callback(self,data,actor=101):
        return SimpleNamespace(data=data,message=self.msg,from_user=SimpleNamespace(id=actor),answer=AsyncMock())

    async def begin(self):
        await cb_menu_add(self.callback('menu:add'),self.state)
        self.assertEqual(self.msg.edit_text.await_args.args[0],'Пришли ник или ссылку Twitch.')

    async def prepare(self):
        await self.begin()
        await process_login_input(self.msg,self.state,self.db,self.twitch)
        self.assertEqual(await self.db.list_channels(101),[])
        call=self.msg.answer.await_args
        self.assertIn('alpha',call.args[0])
        return call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data

    async def test_found_streamer_is_not_added_until_confirm_then_short_result(self):
        from bot.handlers.telegram_add import cb_confirm_add
        confirm=await self.prepare()
        await cb_confirm_add(self.callback(confirm),self.state,self.db)
        self.assertEqual(await self.db.list_channels(101),['alpha'])
        call=self.msg.edit_text.await_args
        self.assertEqual(call.args[0],'Готово. Я сообщу, когда стример начнёт эфир.')
        self.assertEqual([r[0].text for r in call.kwargs['reply_markup'].inline_keyboard],['Добавить ещё','Мои стримеры','На главную'])
        self.assertIsNone(await self.state.get_state())
        await cb_confirm_add(self.callback(confirm),self.state,self.db)
        self.assertEqual(await self.db.count_channels(101),1)

    async def test_wrong_actor_cancelled_and_expired_confirmation_never_adds(self):
        from bot.handlers.telegram_add import cb_confirm_add
        confirm=await self.prepare()
        await cb_confirm_add(self.callback(confirm,202),self.state,self.db)
        self.assertEqual(await self.db.count_channels(101),0)
        await self.state.update_data(add_expires_at=time.time()-1)
        await cb_confirm_add(self.callback(confirm),self.state,self.db)
        self.assertEqual(await self.db.count_channels(101),0)
        await self.state.clear()
        await cb_confirm_add(self.callback(confirm),self.state,self.db)
        self.assertEqual(await self.db.count_channels(101),0)

    async def test_menu_during_lookup_does_not_resurrect_draft_or_add(self):
        from bot.handlers.navigation import on_menu
        await self.begin(); self.msg.text='alpha'
        entered=asyncio.Event(); release=asyncio.Event()
        async def delayed(login): entered.set(); await release.wait(); return True
        self.twitch.channel_exists=delayed
        task=asyncio.create_task(process_login_input(self.msg,self.state,self.db,self.twitch))
        await entered.wait(); await on_menu(self.msg,self.state,self.db)
        release.set(); await task
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(),{})
        self.assertEqual(await self.db.list_channels(101),[])
        self.assertEqual(self.msg.answer_photo.await_args.kwargs['caption'], 'TwitchSignalBot\n\nСледи за стримерами или подключи свой канал.')

    async def test_confirm_checks_atomic_current_limit_50_and_200(self):
        from bot.handlers.telegram_add import cb_confirm_add
        for plus,limit in ((False,50),(True,200)):
            if plus:
                now=time.time()
                await self.db.issue_test_viewer_plus(101,'test-limit',starts_at=now-1,expires_at=now+600,issued_by=999,now=now)
            for index in range(limit-1): await self.db.add_channel(101,f'user{index:04d}')
            await self.begin();self.msg.text='alpha'
            await process_login_input(self.msg,self.state,self.db,self.twitch)
            confirm=self.msg.answer.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await self.db.add_channel(101,'lastslot')
            await cb_confirm_add(self.callback(confirm),self.state,self.db)
            self.assertEqual(await self.db.count_channels(101),limit)
            self.assertNotIn('alpha',await self.db.list_channels(101))
            self.assertIn(str(limit),self.msg.edit_text.await_args.args[0])
            for login in await self.db.list_channels(101): await self.db.remove_channel(101,login)

    async def test_duplicate_and_old_addfound_still_work_without_new_draft(self):
        await self.db.add_channel(101,'alpha'); await self.begin();self.msg.text='alpha'
        await process_login_input(self.msg,self.state,self.db,self.twitch)
        self.assertIn('уже',self.msg.answer.await_args.args[0])
        self.twitch.channel_exists.assert_not_awaited()
        await cb_add_found_channel(self.callback('addfound:101:legacy'),self.state,self.db)
        self.assertEqual(await self.db.list_channels(101),['alpha','legacy'])

    def test_twitch_url_parser_accepts_real_links_rejects_foreign_host_and_paths(self):
        for text in ('alpha','https://www.twitch.tv/Alpha?ref=x','https://m.twitch.tv/alpha/', 'twitch.tv/alpha'):
            self.assertEqual(_extract_login_text(text),'alpha')
        for text in ('https://twitch.tv.evil.test/alpha','https://evil.test/alpha','https://twitch.tv/videos/123',
                     'https://user@twitch.tv/alpha','https://twitch.tv:443/alpha','javascript:alpha'):
            self.assertIsNone(_extract_login_text(text))
