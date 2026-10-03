"""Guided native flows use the real FSM/SQLite; only external Telegram is fake."""
import inspect
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from bot.database import Database
from bot.handlers import streams
from tests.test_telegram_navigation import message,CONFIG
from tests.test_channel_permissions_redesign import fake_bot


class GuidedFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');await self.db.connect();self.addAsyncCleanup(self.db.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        self.msg=message();self.msg.bot=fake_bot()

    def cb(self,data,actor=101):
        return SimpleNamespace(data=data,message=self.msg,from_user=SimpleNamespace(id=actor),answer=AsyncMock(),bot=self.msg.bot)

    async def preset(self):
        await self.db.set_utc_offset(101,180)
        cb=self.cb('qhpreset:23:8')
        kwargs={'state':self.state} if 'state' in inspect.signature(streams.cb_quiet_hours_preset).parameters else {}
        await streams.cb_quiet_hours_preset(cb,self.db,**kwargs)
        self.assertIsNone(await self.db.get_quiet_hours(101),'Preview must not apply quiet hours')
        call=self.msg.edit_text.await_args
        self.assertIn('23:00',call.args[0]);self.assertIn('08:00',call.args[0]);self.assertIn('UTC+3',call.args[0])
        return call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data

    async def test_quiet_preset_requires_owned_confirmation_and_is_one_use(self):
        data=await self.preset()
        await streams.cb_quiet_hours_confirm(self.cb(data,202),self.state,self.db)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        row=await self.db.get_quiet_hours(101)
        self.assertEqual(row[:3],(1200,300,180))
        changes=self.db.conn.total_changes
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        self.assertEqual(self.db.conn.total_changes,changes)
        self.assertFalse(await self.db.has_viewer_plus(101))

    async def test_quiet_cancel_expiry_and_timezone_change_never_apply(self):
        from bot.handlers.navigation import cb_more
        data=await self.preset()
        await cb_more(self.cb('menu:more'),CONFIG,self.state,self.db)
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        data=await self.preset();draft=(await self.state.get_data())['quiet_draft']
        await self.state.update_data(quiet_draft={**draft,'expires':time.time()-1})
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        data=await self.preset();await self.db.set_utc_offset(101,120)
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        self.assertIsNone(await self.db.get_quiet_hours(101))

    async def test_custom_quiet_interval_previews_local_time_before_writing(self):
        await self.db.set_utc_offset(101,180)
        await self.state.set_state(streams.QuietHoursSetup.waiting_for_custom_time)
        self.msg.text='22:30-07:15'
        await streams.process_custom_quiet_hours(self.msg,self.state,self.db)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        call=self.msg.answer.await_args
        self.assertIn('22:30',call.args[0]);self.assertIn('07:15',call.args[0])
        data=call.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
        await streams.cb_quiet_hours_confirm(self.cb(data),self.state,self.db)
        self.assertEqual((await self.db.get_quiet_hours(101))[:3],(1170,255,180))

    async def test_timezone_quick_choice_keeps_free_local_clock_and_rejects_old_choice(self):
        await streams.cb_menu_quiet_hours(self.cb('menu:quiet_hours'),self.state,self.db)
        call=self.msg.edit_text.await_args
        actions={b.callback_data for row in call.kwargs['reply_markup'].inline_keyboard for b in row}
        self.assertIn('qh:offset:180',actions)
        await streams.cb_quiet_hours_offset(self.cb('qh:offset:180'),self.state,self.db)
        self.assertEqual(await self.db.get_utc_offset(101),180)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        await streams.cb_quiet_hours_offset(self.cb('qh:offset:120'),self.state,self.db)
        self.assertEqual(await self.db.get_utc_offset(101),180)

    async def test_channel_guide_preserves_owned_selector_and_cancels_cleanly(self):
        from bot.handlers import telegram_streamer as handlers
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await handlers.cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        self.assertTrue(self.msg.answer_photo.called,'Channel entry needs approved guide card')
        row=(await self.state.get_data())['telegram_community_intent']
        await handlers.cb_channel_help(self.cb('streamer:channelhelp'),self.state,self.db)
        self.assertEqual((await self.state.get_data())['telegram_community_intent'],row)
        self.assertIn('Публикация сообщений',self.msg.edit_text.await_args.args[0])
        await handlers.cb_channel_resume(self.cb('streamer:channelresume'),self.state,self.db)
        self.assertEqual((await self.state.get_data())['telegram_community_intent'],row)
        await handlers.cb_streamer(self.cb('menu:streamer'),self.db,CONFIG,self.state)
        self.assertEqual((await self.db.get_community_intent(row))[6],'cancelled')
        self.assertEqual(await self.db.list_streamer_communities(101),[])

    async def test_streamer_readiness_checks_current_rights_and_publication_switch(self):
        from bot.handlers import telegram_streamer as handlers
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await self.db.add_streamer_community(101,-1001,'Длинное название моего Telegram-канала','channel')
        await self.db.add_channel(-1001,'alpha')
        await self.db.set_notify_enabled(-1001,'alpha',False)
        await handlers.cb_streamer(self.cb('menu:streamer'),self.db,CONFIG)
        call=self.msg.edit_text.await_args
        self.assertIn('Длинное название',call.args[0])
        actions={b.callback_data for row in call.kwargs['reply_markup'].inline_keyboard for b in row}
        self.assertIn('streamer:readiness',actions)
        await handlers.cb_streamer_readiness(self.cb('streamer:readiness'),self.db)
        self.assertIn('Публикации выключены',self.msg.edit_text.await_args.args[0])
        await self.db.set_notify_enabled(-1001,'alpha',True)
        self.msg.bot=fake_bot(post=False)
        await handlers.cb_streamer_readiness(self.cb('streamer:readiness'),self.db)
        self.assertIn('Нет права публикации',self.msg.edit_text.await_args.args[0])
        self.assertNotIn('Готов к публикации',self.msg.edit_text.await_args.args[0])

    async def test_readiness_transition_cancels_unfinished_channel_selection(self):
        from bot.handlers import telegram_streamer as handlers
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await handlers.cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        intent=(await self.state.get_data())['telegram_community_intent']
        kwargs={'state':self.state} if 'state' in inspect.signature(handlers.cb_streamer_readiness).parameters else {}
        await handlers.cb_streamer_readiness(self.cb('streamer:readiness'),self.db,**kwargs)
        self.assertEqual((await self.db.get_community_intent(intent))[6],'cancelled')

    async def test_foreign_import_confirmation_preserves_owner_draft_and_list(self):
        await self.state.update_data(import_logins=['alpha'])
        await streams.cb_import_follows_add(self.cb('importfollows:add',202),self.state,self.db)
        self.assertEqual(await self.db.list_channels(101),[])
        self.assertEqual((await self.state.get_data())['import_logins'],['alpha'])

    async def test_add_success_offers_add_again_and_explicit_cancel_before_save(self):
        from bot.handlers.telegram_add import cb_confirm_add,process_confirmed_input
        await streams.cb_menu_add(self.cb('menu:add'),self.state,self.db)
        self.msg.text='alpha'
        twitch=SimpleNamespace(channel_exists=AsyncMock(return_value=True))
        await process_confirmed_input(self.msg,self.state,self.db,twitch)
        call=self.msg.answer.await_args
        buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any(b.text=='Отменить' for b in buttons))
        confirm=buttons[0].callback_data
        await cb_confirm_add(self.cb(confirm),self.state,self.db)
        buttons=[b for row in self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any(b.text=='Добавить ещё' and b.callback_data=='menu:add' for b in buttons))
        self.assertEqual(await self.db.list_channels(101),['alpha'])
