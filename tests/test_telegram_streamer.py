import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database import Database
from bot.oauth import UserTokenResult
from bot.handlers.auth import cmd_streamer_connect, on_streamer_community_shared
from bot.handlers.navigation import on_menu
from tests.test_telegram_navigation import message, CONFIG
from tests.test_channel_permissions_redesign import fake_bot


class TelegramStreamerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');await self.db.connect();self.addAsyncCleanup(self.db.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        self.msg=message();self.msg.bot=fake_bot()

    def cb(self,data,actor=101):
        return SimpleNamespace(data=data,message=self.msg,from_user=SimpleNamespace(id=actor),answer=AsyncMock(),bot=self.msg.bot)

    async def test_unverified_and_verified_journey_uses_server_identity(self):
        from bot.handlers.telegram_streamer import cb_streamer
        await cb_streamer(self.cb('menu:streamer'),self.db,CONFIG)
        call=self.msg.edit_text.await_args
        self.assertEqual(call.args[0],'Подключи Twitch, чтобы бот мог создавать публикации о твоих эфирах.')
        self.assertEqual([r[0].text for r in call.kwargs['reply_markup'].inline_keyboard],['Подключить Twitch','Что получит стример?','Назад'])
        await self.db.save_user_token('alpha','11','access','refresh',time.time()+300)
        await cb_streamer(self.cb('menu:streamer'),self.db,CONFIG)
        self.assertNotIn('подключён:',self.msg.edit_text.await_args.args[0])
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer(self.cb('menu:streamer'),self.db,CONFIG)
        call=self.msg.edit_text.await_args
        self.assertEqual(call.args[0],'Twitch подключён: alpha\n\nСледующий шаг: выбери Telegram-канал, затем настрой публикации.')
        self.assertEqual([r[0].text for r in call.kwargs['reply_markup'].inline_keyboard],['Telegram-канал','Настройки публикаций','Проверить готовность','Тариф для стримера','Назад'])
        self.assertEqual(call.kwargs['reply_markup'].inline_keyboard[1][0].callback_data,'streamer:posts')

    async def test_connect_intent_cancelled_by_menu_and_late_creation_is_cancelled(self):
        from bot.handlers.telegram_streamer import cb_streamer_connect
        server=SimpleNamespace(create_streamer_connect_intent=AsyncMock(return_value=('owned-intent','https://id.twitch.tv/oauth2/authorize?state=opaque',time.time()+600)),
                               cancel_streamer_connect_intent=AsyncMock())
        await cb_streamer_connect(self.cb('streamer:connect'),self.state,self.db,server)
        self.assertEqual((await self.state.get_data())['telegram_oauth_intent'],'owned-intent')
        self.assertIsNone(await self.db.get_streamer_identity(101))
        await on_menu(self.msg,self.state,self.db,CONFIG,server)
        server.cancel_streamer_connect_intent.assert_awaited_with(101,'owned-intent')
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed(actor): entered.set();await release.wait();return ('late-intent','https://id.twitch.tv/oauth2/authorize',time.time()+600)
        server.create_streamer_connect_intent=delayed
        task=asyncio.create_task(cb_streamer_connect(self.cb('streamer:connect'),self.state,self.db,server))
        await entered.wait();await on_menu(self.msg,self.state,self.db,CONFIG,server);release.set();await task
        server.cancel_streamer_connect_intent.assert_awaited_with(101,'late-intent')
        self.assertEqual(await self.state.get_data(),{})

    async def test_channel_selector_shared_permission_and_menu_cancel_no_late_attach(self):
        from bot.handlers.telegram_streamer import cb_streamer_channel
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        data=await self.state.get_data();row=await self.db.get_community_intent(data['telegram_community_intent'])
        keyboard=self.msg.answer.await_args.kwargs['reply_markup']
        self.assertEqual(keyboard.keyboard[0][0].request_chat.chat_is_channel,True)
        self.assertEqual(keyboard.keyboard[1][0].text,'Меню')
        self.assertTrue(keyboard.is_persistent)
        await on_menu(self.msg,self.state,self.db,CONFIG)
        self.assertEqual((await self.db.get_community_intent(row[0]))[6],'cancelled')
        self.msg.chat_shared=SimpleNamespace(request_id=row[2],chat_id=-1001)
        await on_streamer_community_shared(self.msg,self.db,self.state)
        self.assertEqual(await self.db.list_streamer_communities(101),[])
        self.assertEqual([[b.text for b in r] for r in self.msg.answer.await_args.kwargs['reply_markup'].keyboard],[['Меню']])

    async def test_completed_channel_restores_menu_and_preserves_saved_connection(self):
        from bot.handlers.telegram_streamer import cb_streamer_channel
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        data=await self.state.get_data();row=await self.db.get_community_intent(data['telegram_community_intent'])
        self.msg.chat_shared=SimpleNamespace(request_id=row[2],chat_id=-1001)
        await on_streamer_community_shared(self.msg,self.db,self.state)
        self.assertEqual(len(await self.db.list_streamer_communities(101)),1)
        self.assertEqual([[b.text for b in r] for r in self.msg.answer.await_args.kwargs['reply_markup'].keyboard],[['Меню']])
        await on_menu(self.msg,self.state,self.db,CONFIG)
        self.assertEqual(len(await self.db.list_streamer_communities(101)),1)

    async def test_channel_cancel_via_inline_home_restores_single_menu_keyboard(self):
        from bot.handlers.telegram_streamer import cb_streamer_channel
        from bot.handlers.streams import cb_menu_home
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_streamer_channel(self.cb('streamer:channel'),self.state,self.db)
        await cb_menu_home(self.cb('menu:home'),self.state,CONFIG,db=self.db)
        self.assertEqual([[b.text for b in row] for row in self.msg.answer.await_args.kwargs['reply_markup'].keyboard],[['Меню']])
        self.assertEqual(await self.db.list_streamer_communities(101),[])

    async def test_wrong_actor_or_unverified_channel_cannot_create_intent(self):
        from bot.handlers.telegram_streamer import cb_streamer_channel
        for actor in (101,202):
            await cb_streamer_channel(self.cb('streamer:channel',actor),self.state,self.db)
        self.assertEqual((await (await self.db.conn.execute('SELECT count(*) FROM streamer_community_intents')).fetchone())[0],0)

    async def test_menu_during_legacy_oauth_prevents_late_identity_and_token(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def oauth(*args,**kwargs): entered.set();await release.wait();return UserTokenResult('alpha','11','access','refresh',time.time()+300)
        config=SimpleNamespace(twitch_client_id='test',twitch_client_secret='test')
        with patch('bot.handlers.auth.run_authorization_flow',side_effect=oauth):
            task=asyncio.create_task(cmd_streamer_connect(self.msg,self.db,config,object(),state=self.state))
            await entered.wait();await on_menu(self.msg,self.state,self.db,CONFIG);release.set();await task
        self.assertIsNone(await self.db.get_streamer_identity(101))
        self.assertIsNone(await self.db.get_user_token('alpha'))

    async def test_menu_during_legacy_import_prevents_late_token_and_import_draft(self):
        from bot.handlers.streams import _run_import_follows
        entered=asyncio.Event();release=asyncio.Event()
        async def oauth(*args,**kwargs): entered.set();await release.wait();return UserTokenResult('alpha','11','access','refresh',time.time()+300)
        config=SimpleNamespace(twitch_client_id='test',twitch_client_secret='test')
        with patch('bot.handlers.streams.run_authorization_flow',side_effect=oauth), \
             patch('bot.handlers.streams.TokenStore.execute_with_token',new=AsyncMock(return_value=['beta'])):
            task=asyncio.create_task(_run_import_follows(self.msg,self.state,self.db,config,object()))
            await entered.wait();await on_menu(self.msg,self.state,self.db,CONFIG);release.set();await task
        self.assertIsNone(await self.db.get_user_token('alpha'))
        self.assertEqual(await self.state.get_data(),{})

    async def test_connections_keep_legacy_groups_without_new_group_suggestion(self):
        from bot.handlers.streams import cb_menu_manage_group
        await self.db.add_channel(-1001,'alpha')
        self.msg.bot.get_chat.return_value.type='supergroup'
        self.msg.bot.get_me=AsyncMock(return_value=SimpleNamespace(username='TwitchSignalTestbot'))
        await cb_menu_manage_group(self.cb('menu:manage_group'),self.db)
        call=self.msg.edit_text.await_args
        buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any(b.callback_data=='managegroup:-1001' for b in buttons))
        self.assertFalse(any(b.url and 'startgroup=' in b.url for b in buttons))
        self.assertTrue(any(b.callback_data=='streamer:channel' for b in buttons))

    async def test_old_community_deep_link_binds_cancellable_intent_and_keeps_menu(self):
        from bot.handlers.streams import cmd_start_link
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await self.db.create_community_intent('old-link-intent-101',101,123,'channel',now=time.time())
        await cmd_start_link(self.msg,SimpleNamespace(args='tscommunity_old-link-intent-101'),self.state,self.db,SimpleNamespace(),CONFIG)
        keyboard=self.msg.answer.await_args.kwargs['reply_markup']
        self.assertEqual(keyboard.keyboard[-1][0].text,'Меню')
        self.assertEqual((await self.state.get_data())['telegram_community_intent'],'old-link-intent-101')
        await on_menu(self.msg,self.state,self.db,CONFIG)
        self.assertEqual((await self.db.get_community_intent('old-link-intent-101'))[6],'cancelled')
