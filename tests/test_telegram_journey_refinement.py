"""Native journey contracts with real router/FSM/SQLite and fake transport."""
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher, F, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.database import Database
from bot.handlers.navigation import build_navigation_router
from bot.handlers.auth import on_streamer_community_shared
from tests.test_stars_provider import FakeTelegram, FakeSession
from tests.test_telegram_navigation import CONFIG, message


class JourneyCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(':memory:')
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.bot = FakeTelegram(FakeSession())
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(build_navigation_router())
        self.state = self.dp.fsm.get_context(self.bot, chat_id=101, user_id=101)
        await self.db.link_streamer_identity(101, '11', 'alpha', verified_at=time.time())
        await self.db.add_streamer_community(101, -1009, 'Сохранённый канал', 'channel')
        await self.db.create_community_intent('a'*32, 101, 77, 'channel', now=time.time())
        await self.state.set_data({'telegram_community_intent': 'a'*32})

    def update(self, actor=101):
        msg = Message(message_id=50, date=datetime.now(timezone.utc),
                      chat=Chat(id=101, type='private'),
                      from_user=User(id=12345, is_bot=True, first_name='Bot'))
        cb = CallbackQuery(id='posts', chat_instance='local', message=msg,
                           from_user=User(id=actor, is_bot=False, first_name='User'),
                           data='streamer:posts')
        return Update(update_id=1, callback_query=cb)

    async def test_posts_cancels_pending_selector_restores_menu_and_rejects_late_share(self):
        with (patch.object(Message, 'answer', new_callable=AsyncMock) as replies,
              patch.object(Message, 'edit_text', new_callable=AsyncMock) as edits,
              patch.object(CallbackQuery, 'answer', new_callable=AsyncMock)):
            await self.dp.feed_update(self.bot, self.update(), db=self.db, config=CONFIG)
        self.assertEqual((await self.db.get_community_intent('a'*32))[6], 'cancelled')
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual([[b.text for b in row] for row in replies.await_args.kwargs['reply_markup'].keyboard], [['Меню']])
        self.assertTrue(edits.await_args.kwargs['reply_markup'].inline_keyboard[0][0].web_app.url.endswith('/app'))
        late = message()
        late.bot = self.bot
        late.chat_shared = SimpleNamespace(request_id=77, chat_id=-1001)
        await on_streamer_community_shared(late, self.db, self.state)
        self.assertEqual(await self.db.list_streamer_communities(101), [(-1009, 'Сохранённый канал', 'channel')])

    async def test_wrong_actor_cannot_cancel_owner_selector_or_view_posts(self):
        with (patch.object(Message, 'answer', new_callable=AsyncMock) as replies,
              patch.object(Message, 'edit_text', new_callable=AsyncMock) as edits,
              patch.object(CallbackQuery, 'answer', new_callable=AsyncMock)):
            await self.dp.feed_update(self.bot, self.update(202), db=self.db, config=CONFIG)
        replies.assert_not_awaited()
        edits.assert_not_awaited()
        self.assertEqual((await self.db.get_community_intent('a'*32))[6], 'pending')
        self.assertEqual(await self.state.get_data(), {'telegram_community_intent': 'a'*32})

    async def test_primary_entries_cancel_selector_and_restore_native_menu(self):
        from bot.handlers.streams import cb_menu_list, cb_menu_manage_group
        legacy = Router()
        legacy.callback_query.register(cb_menu_list, F.data == 'menu:list')
        legacy.callback_query.register(cb_menu_manage_group, F.data == 'menu:manage_group')
        self.dp.include_router(legacy)
        for route in ['menu:list','menu:help','menu:open_app','menu:manage_group']:
            with self.subTest(route=route):
                await self.db.conn.execute("UPDATE streamer_community_intents SET status='pending' WHERE intent_id=?", ('a'*32,))
                await self.db.conn.commit()
                await self.state.set_data({'telegram_community_intent': 'a'*32})
                update = self.update()
                update = update.model_copy(update={'callback_query': update.callback_query.model_copy(update={'data': route})})
                with (patch.object(Message, 'answer', new_callable=AsyncMock) as replies,
                      patch.object(Message, 'edit_text', new_callable=AsyncMock),
                      patch.object(CallbackQuery, 'answer', new_callable=AsyncMock)):
                    await self.dp.feed_update(self.bot, update, db=self.db, config=CONFIG)
                self.assertEqual((await self.db.get_community_intent('a'*32))[6], 'cancelled')
                self.assertEqual(await self.state.get_data(), {})
                self.assertEqual([[b.text for b in row] for row in replies.await_args.kwargs['reply_markup'].keyboard], [['Меню']])


class TariffOriginTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamer_offer_secondary_payment_and_back_keep_source_and_product(self):
        from bot.handlers.telegram_streamer import cb_streamer
        from bot.handlers.telegram_plus import cb_plus, cb_buy, cb_payment_method
        from bot.billing import BillingService
        db = Database(':memory:')
        await db.connect()
        self.addAsyncCleanup(db.close)
        await db.link_streamer_identity(101, '11', 'alpha', verified_at=time.time())
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=999, chat_id=101, user_id=101))
        msg = message()
        cb = SimpleNamespace(data='menu:streamer', message=msg, from_user=msg.from_user, answer=AsyncMock())
        buttons = lambda: [b for row in msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        await cb_streamer(cb, db, CONFIG)
        cb.data = next(b.callback_data for b in buttons() if b.text == 'Тариф для стримера')
        await cb_plus(cb, state, db, CONFIG)
        self.assertEqual(buttons()[-1].callback_data, 'menu:streamer')
        cb.data = buttons()[1].callback_data
        await cb_plus(cb, state, db, CONFIG)
        self.assertIn('Зритель Plus\n150 ₽ / месяц', msg.edit_text.await_args.args[0])
        self.assertEqual(buttons()[-1].callback_data, 'menu:streamer')
        cb.data = buttons()[0].callback_data
        await cb_buy(cb, state, db)
        self.assertEqual(buttons()[-1].callback_data, 'plus:show:viewer_plus:streamer')
        cb.data = buttons()[0].callback_data
        await cb_payment_method(cb, state, SimpleNamespace(public_purchase=BillingService.public_purchase))
        self.assertEqual(buttons()[-1].callback_data, 'plus:show:viewer_plus:streamer')
        self.assertEqual((await (await db.conn.execute('SELECT count(*) FROM entitlement_grants')).fetchone())[0], 0)


class NativeListTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(':memory:')
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.msg = message()
        self.msg.bot = SimpleNamespace(id=999, get_chat_member=AsyncMock(return_value=SimpleNamespace(status='administrator')))
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=999, chat_id=101, user_id=101))

    def cb(self, data, actor=101):
        return SimpleNamespace(data=data, message=self.msg, from_user=SimpleNamespace(id=actor), bot=self.msg.bot, answer=AsyncMock())

    def buttons(self):
        call = self.msg.edit_text.await_args
        self.msg.reply_markup = call.kwargs['reply_markup']
        return [b for row in self.msg.reply_markup.inline_keyboard for b in row]

    async def test_42_streamers_show_eight_and_offer_next_page_and_search(self):
        from bot.handlers.streams import cb_menu_list
        for i in range(42):
            await self.db.add_channel(101, f'channel{i:03d}')
        await cb_menu_list(self.cb('menu:list'), self.db)
        buttons = self.buttons()
        self.assertEqual(len([b for b in buttons if (b.callback_data or '').startswith('channelcard:')]), 8)
        self.assertTrue(any(b.text == 'Дальше →' for b in buttons))
        self.assertTrue(any(b.text == 'Поиск по нику' for b in buttons))
        self.assertIn('Личные оповещения', self.msg.edit_text.await_args.args[0])

    async def test_legacy_inline_delete_first_asks_confirmation_without_mutation(self):
        from bot.handlers.streams import cb_untrack
        await self.db.add_channel(101, 'alpha')
        await cb_untrack(self.cb('untrack:101:alpha'), self.db)
        self.assertEqual(await self.db.list_channels(101), ['alpha'])
        buttons = self.buttons()
        self.assertTrue(any(b.text == 'Удалить alpha' for b in buttons))
        self.assertTrue(any(b.text == 'Отменить' for b in buttons))

    async def test_page_and_live_filter_survive_card_and_toggle_return(self):
        from bot.handlers.streams import cb_menu_list, cb_channel_card, cb_toggle_notify
        from bot.telegram_lists import cb_list_page, cb_list_filter
        for i in range(18):
            await self.db.add_channel(101, f'channel{i:03d}')
        await self.db.conn.execute('UPDATE tracked_channels SET is_live=1 WHERE chat_id=101')
        await self.db.conn.commit()
        await cb_menu_list(self.cb('menu:list'), self.db)
        await cb_list_filter(self.cb(next(b.callback_data for b in self.buttons() if b.text == 'В эфире')), self.db)
        await cb_list_page(self.cb(next(b.callback_data for b in self.buttons() if b.text == 'Дальше →')), self.db)
        page_back = next(b.callback_data for b in self.buttons() if b.text == '2 / 3')
        self.assertEqual([b.callback_data for b in self.buttons() if (b.callback_data or '').startswith('channelcard:')],
                         [f'channelcard:101:channel{i:03d}' for i in range(8,16)])
        await cb_channel_card(self.cb('channelcard:101:channel008'), self.db)
        self.assertEqual(self.buttons()[-1].callback_data, page_back)
        await cb_toggle_notify(self.cb('togglenotify:101:channel008'), self.db)
        self.assertEqual(self.buttons()[-1].callback_data, page_back)
        await cb_list_page(self.cb(page_back), self.db)
        self.assertEqual(next(b.text for b in self.buttons() if (b.callback_data or '').startswith('listfilter:') and 'Все' in b.text), 'Все')
        self.assertIn('channel008', self.msg.edit_text.await_args.args[0] + str(self.buttons()))

    async def test_search_is_local_and_other_actor_cannot_replay_page(self):
        from bot.handlers.streams import cb_menu_list
        from bot.telegram_lists import cb_list_page, cb_list_search, process_list_search
        for login in ['minecraftone','minecrafttwo','other']:
            await self.db.add_channel(101, login)
        await self.db.add_channel(202, 'secret')
        await cb_menu_list(self.cb('menu:list'), self.db)
        search = next(b.callback_data for b in self.buttons() if b.text == 'Поиск по нику')
        await cb_list_search(self.cb(search), self.state, self.db)
        self.msg.text = 'MINECRAFT'
        await process_list_search(self.msg, self.state, self.db)
        result = self.msg.answer.await_args
        self.msg.reply_markup = result.kwargs['reply_markup']
        rows = [b for row in self.msg.reply_markup.inline_keyboard for b in row]
        self.assertEqual([b.callback_data for b in rows if (b.callback_data or '').startswith('channelcard:')],
                         ['channelcard:101:minecraftone','channelcard:101:minecrafttwo'])
        self.assertIsNone(await self.state.get_state())
        page = next(b.callback_data for b in rows if (b.callback_data or '').startswith('listpage:'))
        self.msg.edit_text.reset_mock()
        await cb_list_page(self.cb(page, 202), self.db)
        self.msg.edit_text.assert_not_awaited()
        self.assertEqual(await self.db.list_channels(202), ['secret'])

    async def test_confirm_delete_is_owned_once_and_preserves_other_user(self):
        from bot.handlers.streams import cb_untrack
        from bot.telegram_lists import cb_delete_confirm
        for actor in [101,202]:
            await self.db.add_channel(actor, 'alpha')
        await cb_untrack(self.cb('untrack:101:alpha'), self.db)
        choice = next(b.callback_data for b in self.buttons() if b.text == 'Удалить alpha')
        await cb_delete_confirm(self.cb(choice,202), self.db)
        self.assertEqual(await self.db.list_channels(101), ['alpha'])
        await cb_delete_confirm(self.cb(choice), self.db)
        self.assertEqual(await self.db.list_channels(101), [])
        self.assertEqual(await self.db.list_channels(202), ['alpha'])
        changes = self.db.conn.total_changes
        await cb_delete_confirm(self.cb(choice), self.db)
        self.assertEqual(self.db.conn.total_changes, changes)

    async def test_remote_card_returns_directly_to_named_list_with_add(self):
        from bot.handlers.streams import cb_manage_group, cb_channel_card
        from bot.telegram_lists import cb_list_page
        await self.db.register_telegram_channel(-1001, 'Очень длинный канал <любимые эфиры>')
        await self.db.add_channel(-1001, 'alpha')
        await cb_manage_group(self.cb('managegroup:-1001'), self.db)
        self.assertIn('&lt;любимые эфиры&gt;', self.msg.edit_text.await_args.args[0])
        await cb_channel_card(self.cb('channelcard:-1001:alpha'), self.db)
        self.assertIn('&lt;любимые эфиры&gt;', self.msg.edit_text.await_args.args[0])
        back = self.buttons()[-1].callback_data
        self.assertTrue(back.startswith('listpage:-1001:'))
        await cb_list_page(self.cb(back), self.db)
        self.assertTrue(any(b.callback_data == 'menu:add:-1001' for b in self.buttons()))
        self.assertEqual(self.buttons()[-1].callback_data, 'menu:manage_group')

    async def test_cancel_delete_fences_old_confirmation_and_cancel_search_ends_input(self):
        from bot.handlers.streams import cb_untrack, cb_menu_list
        from bot.telegram_lists import cb_list_page, cb_delete_confirm, cb_list_search
        await self.db.add_channel(101, 'alpha')
        await cb_untrack(self.cb('untrack:101:alpha'), self.db)
        choice = next(b.callback_data for b in self.buttons() if b.text == 'Удалить alpha')
        cancel = next(b.callback_data for b in self.buttons() if b.text == 'Отменить')
        await cb_list_page(self.cb(cancel), self.db)
        await cb_delete_confirm(self.cb(choice), self.db)
        self.assertEqual(await self.db.list_channels(101), ['alpha'])
        await cb_menu_list(self.cb('menu:list'), self.db)
        search = next(b.callback_data for b in self.buttons() if b.text == 'Поиск по нику')
        await cb_list_search(self.cb(search), self.state, self.db)
        cancel = next(b.callback_data for b in self.buttons() if b.text == 'Отменить')
        await cb_list_page(self.cb(cancel), self.db, state=self.state)
        self.assertIsNone(await self.state.get_state())

    async def test_expired_page_and_revoked_remote_admin_do_not_mutate_rows(self):
        from bot.handlers.streams import cb_manage_group, cb_untrack
        from bot.telegram_lists import cb_list_page, cb_delete_confirm, _contexts
        await self.db.add_channel(-1001, 'alpha')
        await cb_manage_group(self.cb('managegroup:-1001'), self.db)
        page = next(b.callback_data for b in self.buttons() if (b.callback_data or '').startswith('listpage:'))
        await cb_untrack(self.cb('untrack:-1001:alpha'), self.db)
        choice = next(b.callback_data for b in self.buttons() if b.text == 'Удалить alpha')
        self.msg.bot.get_chat_member.return_value.status = 'member'
        await cb_delete_confirm(self.cb(choice), self.db)
        self.assertEqual(await self.db.list_channels(-1001), ['alpha'])
        _contexts[page.split(':')[2]].expires = time.monotonic()-1
        self.msg.edit_text.reset_mock()
        await cb_list_page(self.cb(page), self.db)
        self.msg.edit_text.assert_not_awaited()

    async def test_old_card_cannot_read_other_private_user_even_with_fake_member_status(self):
        from bot.handlers.streams import cb_channel_card
        for actor, login in [(101,'alpha'),(202,'secret')]:
            await self.db.add_channel(actor, login)
        for data, actor in [('channelcard:101:alpha',202),('channelcard:202:secret',101)]:
            await cb_channel_card(self.cb(data,actor), self.db)
        self.msg.edit_text.assert_not_awaited()
