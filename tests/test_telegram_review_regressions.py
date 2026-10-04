"""Reviewer reproductions: discarded intents, cold deep entry and truthful grants."""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup

from bot.database import Database
from bot.handlers import streams,auth
from bot.handlers.navigation import on_menu
from bot.handlers.telegram_streamer import cb_streamer_channel
from bot.handlers.telegram_plus import cb_plus
from tests.test_telegram_home_copy import Caption
from bot.viewer_trial import ViewerTrialService
from tests.test_telegram_navigation import message,CONFIG
from tests.test_channel_permissions_redesign import fake_bot
from tests import test_telegram_home as home_fixtures


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');await self.db.connect();self.addAsyncCleanup(self.db.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        self.msg=message();self.msg.bot=fake_bot()
        self.oauth=SimpleNamespace(cancel_streamer_connect_intent=AsyncMock(),discard_state=unittest.mock.Mock())

    def cb(self,data):
        return SimpleNamespace(message=self.msg,from_user=SimpleNamespace(id=101),data=data,answer=AsyncMock(),bot=self.msg.bot)

    async def test_switch_channel_to_add_or_quiet_then_menu_rejects_late_shared(self):
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        for handler,data in ((streams.cb_menu_add,'menu:add'),(streams.cb_menu_quiet_hours,'menu:quiet_hours'),
                             (streams.cb_quiet_hours_custom,'qh:custom'),(streams.cb_add_found_channel,'addfound:101:beta'),
                             (streams.cb_import_follows_add,'importfollows:add')):
            with self.subTest(route=data):
                await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
                row=await self.db.get_community_intent((await self.state.get_data())['telegram_community_intent'])
                await HandlerObject(handler).call(self.cb(data),state=self.state,db=self.db,oauth_server=self.oauth)
                await on_menu(self.msg,self.state,self.db,CONFIG,self.oauth)
                self.msg.chat_shared=SimpleNamespace(request_id=row[2],chat_id=-1001)
                await auth.on_streamer_community_shared(self.msg,self.db,self.state)
                self.assertEqual(await self.db.list_streamer_communities(101),[])
                self.assertEqual((await self.db.get_community_intent(row[0]))[6],'cancelled')

    async def test_switch_new_oauth_to_add_discards_both_owned_oauth_generations(self):
        await self.state.set_data({'telegram_oauth_intent':'own-connect','legacy_oauth_state':'old-generation'})
        await HandlerObject(streams.cb_menu_add).call(self.cb('menu:add'),state=self.state,db=self.db,oauth_server=self.oauth)
        await on_menu(self.msg,self.state,self.db,CONFIG,self.oauth)
        self.oauth.cancel_streamer_connect_intent.assert_awaited_once_with(101,'own-connect')
        self.oauth.discard_state.assert_called_once_with('old-generation')

    async def test_legacy_auth_switch_cancels_pending_channel_before_waiting(self):
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        row=await self.db.get_community_intent((await self.state.get_data())['telegram_community_intent'])
        config=SimpleNamespace(twitch_client_id='local',twitch_client_secret='local')
        with patch('bot.handlers.auth.run_authorization_flow',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await auth._run_auth_flow(self.msg,self.db,config,self.oauth,state=self.state)
        await on_menu(self.msg,self.state,self.db,CONFIG,self.oauth)
        self.msg.chat_shared=SimpleNamespace(request_id=row[2],chat_id=-1001)
        await auth.on_streamer_community_shared(self.msg,self.db,self.state)
        self.assertEqual(await self.db.list_streamer_communities(101),[])
        self.assertEqual((await self.db.get_community_intent(row[0]))[6],'cancelled')

    async def test_menu_during_channel_row_read_cannot_resurrect_selector(self):
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        entered=asyncio.Event();release=asyncio.Event();original=self.db.get_community_intent
        async def delayed(intent):
            result=await original(intent);entered.set();await release.wait();return result
        with patch.object(self.db,'get_community_intent',new=delayed):
            task=asyncio.create_task(cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db))
            await entered.wait();await on_menu(self.msg,self.state,self.db,CONFIG,self.oauth);release.set();await task
        self.assertEqual(await self.state.get_data(),{})
        rows=await (await self.db.conn.execute('SELECT status FROM streamer_community_intents')).fetchall()
        self.assertEqual(rows,[('cancelled',)])
        self.assertTrue(all(not any(b.request_chat for row in call.kwargs['reply_markup'].keyboard for b in row)
                            for call in self.msg.answer.await_args_list if isinstance(call.kwargs.get('reply_markup'),ReplyKeyboardMarkup)))

    async def test_deep_track_link_and_cold_callback_initialize_persistent_menu(self):
        from bot import telegram_home
        transport=home_fixtures.MenuTransport();client=home_fixtures.MenuClient(777,transport)
        incoming=home_fixtures.SmartHomeTests.incoming(SimpleNamespace(bot=client))
        for payload in ('track_alpha','link_-1001'):
            telegram_home._menus.clear();transport.calls.clear()
            with patch('bot.handlers.streams._chat_member_status',new=AsyncMock(return_value='administrator')):
                await streams.cmd_start_link(incoming,SimpleNamespace(args=payload),self.state,self.db,
                                             SimpleNamespace(channel_exists=AsyncMock(return_value=True)),CONFIG)
            self.assertTrue(any(isinstance(m.reply_markup,ReplyKeyboardMarkup) and m.reply_markup.is_persistent
                                and [[b.text for b in row] for row in m.reply_markup.keyboard]==[['Меню']]
                                for m in transport.calls))
        await streams.cmd_start(incoming,self.state,self.db,CONFIG)
        previous=list(transport.messages.values())[-1]
        telegram_home._menus.clear();transport.calls.clear()
        cb=SimpleNamespace(message=previous,from_user=SimpleNamespace(id=101),answer=AsyncMock())
        await streams.cb_menu_home(cb,self.state,CONFIG,self.db)
        self.assertTrue(any(isinstance(m.reply_markup,ReplyKeyboardMarkup) for m in transport.calls))

    async def test_menu_while_reopening_channel_deep_link_keeps_it_cancelled(self):
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        intent=(await self.state.get_data())['telegram_community_intent']
        self.msg.answer.reset_mock()
        entered=asyncio.Event();release=asyncio.Event();original=self.db.get_community_intent
        async def delayed(key):
            result=await original(key);entered.set();await release.wait();return result
        with patch.object(self.db,'get_community_intent',new=delayed):
            task=asyncio.create_task(streams.cmd_start_link(self.msg,SimpleNamespace(args='tscommunity_'+intent),
                                                          self.state,self.db,SimpleNamespace(),CONFIG,self.oauth))
            await entered.wait();await on_menu(self.msg,self.state,self.db,CONFIG,self.oauth);release.set();await task
        self.assertEqual(await self.state.get_data(),{})
        self.assertEqual((await original(intent))[6],'cancelled')
        self.assertTrue(all(not any(b.request_chat for row in call.kwargs['reply_markup'].keyboard for b in row)
                            for call in self.msg.answer.await_args_list if isinstance(call.kwargs.get('reply_markup'),ReplyKeyboardMarkup)))

    async def test_overlapping_trial_and_manual_test_keep_actual_label_and_expiry(self):
        now=time.time();await ViewerTrialService(self.db).start(101,now=now)
        expiry=now+20*86400
        await self.db.issue_test_viewer_plus(101,'manual20',starts_at=now-1,expires_at=expiry,issued_by=999,now=now)
        await cb_plus(self.cb('menu:plus'),self.state,self.db)
        text=self.msg.edit_text.await_args.args[0]
        rendered=''.join(Caption(text).plain)
        self.assertIn('Статус: Тестовый доступ',rendered)
        self.assertNotIn('Статус: Ознакомительный доступ',rendered)
        self.assertIn('Тестовый доступ',Caption(text).bold)
        from datetime import datetime,timezone,timedelta
        self.assertIn(datetime.fromtimestamp(expiry,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M'),text)

    async def test_free_streamer_offer_does_not_claim_access_already_granted(self):
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_plus(self.cb('menu:plus'),self.state,self.db)
        text=self.msg.edit_text.await_args.args[0]
        self.assertIn('В Стример Plus включены все возможности Зритель Plus.',text)
        self.assertNotIn('уже включён для твоего',text)
        self.assertFalse(await self.db.has_viewer_plus(101))

    async def test_short_mock_does_not_mislabel_long_paid_grant(self):
        now=time.time()
        for product in ('viewer_plus','streamer_plus'):
            with self.subTest(product=product):
                if product=='streamer_plus':
                    await self.db.link_streamer_identity(101,'11','alpha',verified_at=now)
                async def grant(key,expiry):
                    if product=='viewer_plus':
                        return await self.db.issue_test_viewer_plus(101,key,starts_at=now-1,expires_at=expiry,issued_by=999,now=now)
                    return await self.db.issue_test_streamer_plus('11',key,starts_at=now-1,expires_at=expiry,
                                                                 issued_by=999,now=now,beneficiary_telegram_user_id=101)
                short=await grant(product+'-mock',now+3600)
                long=await grant(product+'-paid',now+20*86400)
                # Reproduce old persisted mock source=paid, alongside a separate
                # real-provider-shaped grant. No real payment or provider call.
                await self.db.conn.execute("UPDATE entitlement_grants SET source='paid' WHERE grant_id IN (?,?)",(short,long))
                order=await self.db.create_billing_order(('a' if product=='viewer_plus' else 'b')*32,
                                                        product+'-order',101,3600,now=now,plan=product)
                await self.db.conn.execute("UPDATE billing_orders SET grant_id=?,status='paid' WHERE order_id=?",(short,order.order_id))
                await self.db.conn.commit()
                await cb_plus(self.cb('menu:plus'),self.state,self.db)
                text=self.msg.edit_text.await_args.args[0]
                rendered=''.join(Caption(text).plain)
                self.assertIn('Статус: Активна',rendered)
                self.assertNotIn('Статус: Тестовый доступ',rendered)
                self.assertIn('Активна',Caption(text).bold)

    def test_help_points_to_actual_home_action(self):
        from bot.handlers.telegram_help import help_screen
        text,_=help_screen(CONFIG)
        self.assertIn('«➕ Добавить оповещения»',text)
        self.assertNotIn('«Добавить стримера»',text)
