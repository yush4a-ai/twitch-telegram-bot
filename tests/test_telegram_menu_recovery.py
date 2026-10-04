"""Real Telegram handlers, with only Bot API delivery replaced by local transport."""
import asyncio
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, ChatShared, InaccessibleMessage, Message, ReplyKeyboardMarkup, User

from bot.database import Database
from bot.handlers.auth import on_streamer_community_shared, _run_auth_flow
from bot.oauth import OAuthFlowError
from bot.handlers.streams import cmd_start, cb_menu_live
from bot.handlers.telegram_streamer import show_channel_selector
from bot.handlers.navigation import on_menu
from bot.middlewares import ErrorGuardMiddleware
from bot import telegram_home
from bot.telegram_ui import cancel_ui
from tests.test_telegram_home import MenuClient, MenuTransport
from tests.test_telegram_navigation import CONFIG


class RecoveryTransport(MenuTransport):
    async def make_request(self,bot,method,timeout=None):
        if method.__api_method__=='answerCallbackQuery':
            self.calls.append(method); return True
        return await super().make_request(bot,method,timeout)


class MenuRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        telegram_home._menus.clear()
        self.db=Database(':memory:'); await self.db.connect(); self.addAsyncCleanup(self.db.close)
        self.transport=RecoveryTransport(); self.bot=MenuClient(777,self.transport)
        self.addAsyncCleanup(self.transport.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=777,chat_id=101,user_id=101))
        self.message=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=101,type='private'),
                             from_user=User(id=101,is_bot=False,first_name='Test'),text='Меню').as_(self.bot)
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())

    def keyboards(self):
        return [m.reply_markup for m in self.transport.calls
                if isinstance(getattr(m,'reply_markup',None),ReplyKeyboardMarkup)]

    def assert_menu(self, keyboard):
        self.assertEqual([[b.text for b in row] for row in keyboard.keyboard],[['Меню']])
        self.assertTrue(keyboard.is_persistent); self.assertTrue(keyboard.resize_keyboard)
        self.assertFalse(keyboard.one_time_keyboard)

    async def pending(self):
        await self.db.create_community_intent('intent-local-0001',101,42,'channel',now=time.time())
        return await self.db.get_community_intent('intent-local-0001')

    async def test_failed_cancel_delivery_is_recovered_by_next_home(self):
        await cmd_start(self.message,self.state,self.db,CONFIG)
        await show_channel_selector(self.message,self.state,await self.pending(),self.db)
        original=self.transport.make_request
        async def fail_restore(bot,method,timeout=None):
            if isinstance(getattr(method,'reply_markup',None),ReplyKeyboardMarkup):
                raise RuntimeError('local delivery failure')
            return await original(bot,method,timeout)
        with patch.object(self.transport,'make_request',new=fail_restore):
            with self.assertRaises(RuntimeError):
                await cancel_ui(self.state,actor_id=101,db=self.db,message=self.message)
        self.assertEqual(await self.state.get_data(),{})
        before=len(self.keyboards())
        await cmd_start(self.message,self.state,self.db,CONFIG)
        self.assertEqual(len(self.keyboards()),before+1)
        self.assert_menu(self.keyboards()[-1])

    async def test_database_cancel_failure_restores_menu_without_false_success(self):
        row=await self.pending()
        await show_channel_selector(self.message,self.state,row,self.db)
        async def fail_cancel(*args): raise RuntimeError('local database failure')
        with patch.object(self.db,'cancel_community_intent',new=fail_cancel):
            with self.assertRaisesRegex(RuntimeError,'local database failure'):
                await cancel_ui(self.state,actor_id=101,db=self.db,message=self.message)
        self.assert_menu(self.keyboards()[-1])
        self.assertEqual((await self.db.get_community_intent(row[0]))[6],'pending')
        self.assertNotEqual(self.transport.calls[-1].text,'Выбор канала отменён.')

    async def test_cancel_during_guide_does_not_send_a_late_selector(self):
        await cmd_start(self.message,self.state,self.db,CONFIG)
        entered=asyncio.Event(); release=asyncio.Event(); original=self.transport.make_request
        async def hold_guide(bot,method,timeout=None):
            if method.__api_method__=='sendPhoto':
                entered.set(); await release.wait()
            return await original(bot,method,timeout)
        with patch.object(self.transport,'make_request',new=hold_guide):
            selector=asyncio.create_task(show_channel_selector(self.message,self.state,await self.pending(),self.db))
            await asyncio.wait_for(entered.wait(),2)
            await cancel_ui(self.state,actor_id=101,db=self.db,message=self.message)
            release.set(); await selector
        self.assertEqual(await self.state.get_data(),{})
        self.assert_menu(self.keyboards()[-1])

    async def test_cancel_during_selector_delivery_finishes_with_ordinary_menu(self):
        await cmd_start(self.message,self.state,self.db,CONFIG)
        entered=asyncio.Event(); release=asyncio.Event(); original=self.transport.make_request
        cancelled=asyncio.Event(); original_cancel=self.db.cancel_community_intent
        async def cancel_intent(*args):
            result=await original_cancel(*args); cancelled.set(); return result
        async def hold_selector(bot,method,timeout=None):
            keyboard=getattr(method,'reply_markup',None)
            if isinstance(keyboard,ReplyKeyboardMarkup) and keyboard.keyboard[0][0].request_chat:
                entered.set(); await release.wait()
            return await original(bot,method,timeout)
        with patch.object(self.transport,'make_request',new=hold_selector), \
             patch.object(self.db,'cancel_community_intent',new=cancel_intent):
            selector=asyncio.create_task(show_channel_selector(self.message,self.state,await self.pending(),self.db))
            await asyncio.wait_for(entered.wait(),2)
            cancel=asyncio.create_task(cancel_ui(self.state,actor_id=101,db=self.db,message=self.message))
            await asyncio.wait_for(cancelled.wait(),2)
            self.assertEqual(await self.state.get_data(),{})
            await asyncio.sleep(0)
            try:
                self.assertFalse(cancel.done(),'restore must wait for in-flight selector delivery')
            finally:
                release.set(); await asyncio.gather(selector,cancel)
        self.assert_menu(self.keyboards()[-1])

    async def test_menu_handler_cancels_before_waiting_for_inflight_selector_delivery(self):
        await cmd_start(self.message,self.state,self.db,CONFIG)
        entered=asyncio.Event(); release=asyncio.Event(); cancelled=asyncio.Event()
        original=self.transport.make_request; original_cancel=self.db.cancel_community_intent
        async def hold_selector(bot,method,timeout=None):
            keyboard=getattr(method,'reply_markup',None)
            if isinstance(keyboard,ReplyKeyboardMarkup) and keyboard.keyboard[0][0].request_chat:
                entered.set(); await release.wait()
            return await original(bot,method,timeout)
        async def cancel_intent(*args):
            result=await original_cancel(*args); cancelled.set(); return result
        async def menu_handler(event,data): await on_menu(event,self.state,self.db,CONFIG)
        with patch.object(self.transport,'make_request',new=hold_selector), \
             patch.object(self.db,'cancel_community_intent',new=cancel_intent):
            row=await self.pending()
            selector=asyncio.create_task(show_channel_selector(self.message,self.state,row,self.db))
            await asyncio.wait_for(entered.wait(),2)
            menu=asyncio.create_task(ErrorGuardMiddleware()(menu_handler,self.message,{'state':self.state}))
            try:
                await asyncio.wait_for(cancelled.wait(),2)
                self.assertEqual(await self.state.get_data(),{})
                self.assertEqual((await self.db.get_community_intent(row[0]))[6],'cancelled')
            finally:
                release.set(); await asyncio.gather(selector,menu)
        self.assert_menu(self.keyboards()[-1])

    async def test_inline_edits_do_not_extend_keyboard_delivery_lifetime(self):
        clock=[100.0]
        with patch('bot.telegram_home.time',SimpleNamespace(monotonic=lambda:clock[0],time=time.time)):
            await cmd_start(self.message,self.state,self.db,CONFIG)
            photo=list(self.transport.messages.values())[-1]
            clock[0]+=23*3600
            await telegram_home.edit_menu(photo,'Help')
            before=len(self.keyboards()); clock[0]+=2*3600
            await telegram_home.ensure_menu_keyboard(self.message)
        self.assertEqual(len(self.keyboards()),before+1)
        self.assert_menu(self.keyboards()[-1])

    async def test_cold_non_home_interaction_and_reset_recover_once_without_warm_spam(self):
        async def help_handler(event,data):
            await event.answer('Help')
        guard=ErrorGuardMiddleware()
        for _ in range(2):
            telegram_home._menus.clear(); before=len(self.keyboards())
            await guard(help_handler,self.message,{'state':self.state})
            self.assertEqual(len(self.keyboards()),before+1)
            self.assert_menu(self.keyboards()[-1])
            await guard(help_handler,self.message,{'state':self.state})
            self.assertEqual(len(self.keyboards()),before+1)

    async def test_existing_live_callback_returns_own_live_streams_and_recovers_after_reset(self):
        await self.db.add_channel(101,'alpha')
        await self.db.add_channel(202,'other_private')
        await self.db.conn.execute('UPDATE tracked_channels SET is_live=1')
        await self.db.conn.commit()
        await cmd_start(self.message,self.state,self.db,CONFIG)
        photo=list(self.transport.messages.values())[-1]
        telegram_home._menus.clear(); before=len(self.keyboards())
        callback=CallbackQuery(id='local-live',from_user=self.message.from_user,chat_instance='local',
                               message=photo,data='menu:live').as_(self.bot)
        async def live_handler(event,data): await cb_menu_live(event,self.db)
        await ErrorGuardMiddleware()(live_handler,callback,{'state':self.state})
        caption=next(m.caption for m in reversed(self.transport.calls) if m.__api_method__=='editMessageCaption')
        self.assertIn('alpha',caption); self.assertNotIn('other_private',caption)
        self.assertIn('В эфире — 1',caption)
        self.assertEqual(len(self.keyboards()),before+1); self.assert_menu(self.keyboards()[-1])
        await ErrorGuardMiddleware()(live_handler,callback,{'state':self.state})
        self.assertEqual(len(self.keyboards()),before+1)

    async def test_cold_oauth_has_menu_while_waiting_for_external_authorization(self):
        entered=asyncio.Event(); release=asyncio.Event()
        async def authorization(*args,on_url_ready):
            await on_url_ready('https://id.twitch.tv/oauth2/authorize?state=local-oauth')
            entered.set(); await release.wait()
            raise OAuthFlowError('local expiry')
        async def handler(event,data):
            await _run_auth_flow(event,self.db,SimpleNamespace(twitch_client_id='local',twitch_client_secret='local'),
                                 SimpleNamespace(),state=self.state)
        with patch('bot.handlers.auth.run_authorization_flow',new=authorization):
            task=asyncio.create_task(ErrorGuardMiddleware()(handler,self.message,{'state':self.state}))
            await asyncio.wait_for(entered.wait(),2)
            try:
                self.assertEqual(len(self.keyboards()),1,'Menu must arrive before OAuth completes')
                self.assert_menu(self.keyboards()[-1])
            finally:
                release.set(); await task
        self.assertEqual(len(self.keyboards()),1)

    async def test_error_after_fsm_clear_restores_selector(self):
        await show_channel_selector(self.message,self.state,await self.pending(),self.db)
        async def failed_handler(event,data):
            await self.state.clear()
            raise RuntimeError('local handler failure')
        with self.assertLogs('bot.middlewares',level='ERROR'):
            await ErrorGuardMiddleware()(failed_handler,self.message,{'state':self.state})
        self.assert_menu(self.keyboards()[-1])

    async def test_connected_shared_replay_recovers_failed_confirmation_without_reconnecting(self):
        await show_channel_selector(self.message,self.state,await self.pending(),self.db)
        await self.db.conn.execute("UPDATE streamer_community_intents SET status='connected' WHERE intent_id='intent-local-0001'")
        await self.db.conn.commit()
        shared=self.message.model_copy(update={'chat_shared':ChatShared(request_id=42,chat_id=-1001)}).as_(self.bot)
        before=len(self.keyboards())
        await on_streamer_community_shared(shared,self.db,self.state)
        self.assertEqual(len(self.keyboards()),before+1)
        self.assert_menu(self.keyboards()[-1]); self.assertEqual(await self.state.get_data(),{})
        await on_streamer_community_shared(shared,self.db,self.state)
        self.assertEqual(len(self.keyboards()),before+1)

    async def test_stale_shared_request_does_not_replace_another_active_selector(self):
        old=await self.pending()
        await self.db.cancel_community_intent(old[0],101)
        await self.db.create_community_intent('intent-local-0002',101,43,'channel',now=time.time())
        row=await self.db.get_community_intent('intent-local-0002')
        await show_channel_selector(self.message,self.state,row,self.db)
        shared=self.message.model_copy(update={'chat_shared':ChatShared(request_id=42,chat_id=-1001)}).as_(self.bot)
        before=len(self.keyboards())
        await on_streamer_community_shared(shared,self.db,self.state)
        self.assertEqual(len(self.keyboards()),before)
        self.assertEqual(self.keyboards()[-1].keyboard[0][0].request_chat.request_id,43)
        self.assertEqual((await self.state.get_data())['telegram_community_intent'],row[0])

    async def test_late_shared_row_read_cannot_clear_a_new_selector_generation(self):
        for status in ('connected','cancelled'):
            with self.subTest(status=status):
                await self.state.clear()
                await self.db.conn.execute('DELETE FROM streamer_community_intents')
                await self.db.conn.commit()
                old=await self.pending()
                await show_channel_selector(self.message,self.state,old,self.db)
                await self.db.conn.execute('UPDATE streamer_community_intents SET status=? WHERE intent_id=?',(status,old[0]))
                await self.db.conn.commit()
                await self.db.create_community_intent('intent-local-0002',101,43,'channel',now=time.time())
                row=await self.db.get_community_intent('intent-local-0002')
                shared=self.message.model_copy(update={'chat_shared':ChatShared(request_id=42,chat_id=-1001)}).as_(self.bot)
                entered=asyncio.Event(); release=asyncio.Event(); original=self.db.get_community_intent
                async def hold_old(key):
                    result=await original(key)
                    if key==old[0]: entered.set(); await release.wait()
                    return result
                with patch.object(self.db,'get_community_intent',new=hold_old):
                    delayed=asyncio.create_task(on_streamer_community_shared(shared,self.db,self.state))
                    await asyncio.wait_for(entered.wait(),2)
                    await show_channel_selector(self.message,self.state,row,self.db)
                    release.set(); await delayed
                self.assertEqual((await self.state.get_data()).get('telegram_community_intent'),row[0])
                self.assertEqual(self.keyboards()[-1].keyboard[0][0].request_chat.request_id,43)
                self.assertEqual((await original(row[0]))[6],'pending')

    async def test_cold_callback_from_deleted_own_message_still_recovers_menu(self):
        callback=CallbackQuery(id='local-query',from_user=self.message.from_user,chat_instance='local',
            message=InaccessibleMessage(message_id=2,chat=self.message.chat).as_(self.bot),data='menu:help').as_(self.bot)
        async def noop(event,data): pass
        await ErrorGuardMiddleware()(noop,callback,{'state':self.state})
        self.assertEqual(len(self.keyboards()),1); self.assert_menu(self.keyboards()[-1])

    async def test_active_selector_is_preserved_and_successful_cancel_has_no_extra_recovery(self):
        await show_channel_selector(self.message,self.state,await self.pending(),self.db)
        before=len(self.keyboards()); selector=self.keyboards()[-1]
        self.assertEqual([[b.text for b in row] for row in selector.keyboard],
                         [['Выбрать Telegram-канал'],['Меню']])
        async def noop(event,data): pass
        await ErrorGuardMiddleware()(noop,self.message,{'state':self.state})
        self.assertEqual(len(self.keyboards()),before)
        await cancel_ui(self.state,actor_id=101,db=self.db,message=self.message)
        await ErrorGuardMiddleware()(noop,self.message,{'state':self.state})
        self.assertEqual(len(self.keyboards()),before+1); self.assert_menu(self.keyboards()[-1])

    async def test_recovery_never_sends_private_keyboard_to_groups_or_another_actor(self):
        async def noop(event,data): pass
        for chat,actor,kind in ((-1001,101,'supergroup'),(101,202,'private')):
            message=self.message.model_copy(update={'chat':Chat(id=chat,type=kind),
                'from_user':User(id=actor,is_bot=False,first_name='Other')}).as_(self.bot)
            await ErrorGuardMiddleware()(noop,message,{'state':self.state})
        self.assertEqual(self.keyboards(),[])
