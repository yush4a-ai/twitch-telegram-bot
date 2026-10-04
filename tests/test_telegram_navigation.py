"""New Telegram home exercised with real FSM storage and no Telegram transport."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Dispatcher, Router
from tests.test_stars_provider import FakeTelegram, FakeSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Update, Message, Chat, User
from datetime import datetime, timezone

from bot.handlers.streams import cmd_start, cb_menu_home, AddChannel, QuietHoursSetup

HOME = "<b>Оповещения о Twitch</b>\n\nСледи за стримерами или подключи свой канал."
LABELS = ["Открыть приложение", "➕ Добавить оповещения", "🎥 Я стример", "Ещё"]
CONFIG = SimpleNamespace(mini_app_enabled=True, viewer_plus_enabled=True,
                         oauth_public_base_url="https://staging.example.test",
                         owner_chat_id=101, admin_panel_access_key="test-key")


def message(actor=101, chat=None, kind="private"):
    return SimpleNamespace(chat=SimpleNamespace(id=actor if chat is None else chat, type=kind),
                           from_user=SimpleNamespace(id=actor), answer=AsyncMock(), answer_photo=AsyncMock(), edit_text=AsyncMock(), edit_media=AsyncMock(), photo=None)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=999, chat_id=101, user_id=101))
        self.db = SimpleNamespace(mark_known_private_user=AsyncMock(), cancel_community_intent=AsyncMock(), list_channels=AsyncMock(return_value=[]), list_channels_with_routing=AsyncMock(return_value=[]), list_live_channels=AsyncMock(return_value=[]), get_streamer_identity=AsyncMock(return_value=None))

    def assert_home(self, call):
        self.assertEqual(call.kwargs.get('caption',call.args[0] if call.args else None), HOME)
        rows = call.kwargs["reply_markup"].inline_keyboard
        self.assertEqual([len(r) for r in rows], [1]*4)
        self.assertEqual([r[0].text for r in rows], LABELS)
        self.assertNotIn("admin", str(rows))
        return rows

    async def test_start_and_callback_have_same_exact_home_for_owner_and_regular(self):
        for actor in (101, 202):
            msg = message(actor)
            await cmd_start(msg, self.state, self.db, CONFIG)
            rows = self.assert_home(msg.answer_photo.await_args)
            self.assertEqual(rows[0][0].web_app.url, CONFIG.oauth_public_base_url+'/app')
            callback = SimpleNamespace(message=msg, from_user=msg.from_user, answer=AsyncMock())
            await cb_menu_home(callback, self.state, CONFIG)
            self.assertEqual(msg.edit_media.await_args.kwargs['media'].caption,HOME)
            self.assertEqual(msg.edit_media.await_args.kwargs['reply_markup'].inline_keyboard, rows)

    async def test_reply_keyboard_only_menu_persistent_and_start_preserves_saved_data(self):
        await self.state.set_state(AddChannel.waiting_for_login)
        await self.state.set_data({'target_chat_id':101,'draft':'alpha'})
        msg = message()
        await cmd_start(msg, self.state, self.db, CONFIG)
        keyboard = msg.answer.await_args_list[0].kwargs['reply_markup']
        self.assertEqual([[b.text for b in r] for r in keyboard.keyboard], [['Меню']])
        self.assertIs(keyboard.is_persistent, True)
        self.assertIs(keyboard.resize_keyboard, True)
        self.assertIs(keyboard.one_time_keyboard, False)
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.db.mark_known_private_user.assert_awaited_once_with(101)

    async def test_more_prioritizes_streamers_and_live_and_keeps_owner_admin_only(self):
        from bot.handlers.navigation import cb_more
        for actor, kind, chat in ((101,'private',101),(202,'private',202),(101,'supergroup',-5)):
            msg = message(actor, chat, kind)
            callback = SimpleNamespace(message=msg, from_user=msg.from_user, answer=AsyncMock())
            await cb_more(callback, CONFIG)
            call = msg.edit_text.await_args
            self.assertEqual(call.args[0], '<b>Ещё</b>')
            buttons = [b for r in call.kwargs['reply_markup'].inline_keyboard for b in r]
            rows=call.kwargs['reply_markup'].inline_keyboard
            self.assertEqual([[b.text for b in row] for row in rows[:4]],[
                ['📡 Мои стримеры','🔴 Сейчас в эфире'],
                ['🔔 Настройки','💬 Telegram-каналы'],
                ['⭐ Тариф','❓ Помощь'],['📊 Отчёты']])
            self.assertEqual([[b.callback_data for b in row] for row in rows[:4]],[
                ['menu:list','menu:live'],['menu:quiet_hours','menu:manage_group'],
                ['menu:plus','menu:help'],['menu:report']])
            self.assertEqual(rows[-1][0].callback_data,'menu:home')
            actions = {b.callback_data for b in buttons}
            self.assertEqual(actions - {None}, {'menu:list','menu:live','menu:report','menu:quiet_hours',
                             'menu:manage_group','menu:plus','menu:help','menu:home'})
            admin = [b for b in buttons if b.text=='🛡 Админ-панель']
            self.assertEqual(len(admin), int(actor==101 and kind=='private'))
            if admin: self.assertEqual(admin[0].web_app.url, CONFIG.oauth_public_base_url+'/admin')

    async def test_menu_intercepts_all_fsm_inputs_before_their_handlers(self):
        from bot.handlers.navigation import build_navigation_router
        for pending in (AddChannel.waiting_for_login, QuietHoursSetup.waiting_for_custom_time,
                        'oauth:waiting','channel:waiting','payment:method'):
            dp = Dispatcher(storage=MemoryStorage())
            dp.include_router(build_navigation_router())
            other = Router()
            swallowed = AsyncMock()
            async def legacy_input(message):
                await swallowed(message)
            other.message.register(legacy_input)
            dp.include_router(other)
            bot = FakeTelegram(FakeSession())
            state = dp.fsm.get_context(bot, chat_id=101, user_id=101)
            await state.set_state(pending)
            await state.set_data({'draft':'unconfirmed'})
            msg = Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=101,type='private'),
                          from_user=User(id=101,is_bot=False,first_name='Test'),text='Меню')
            with unittest.mock.patch.object(Message,'answer',new_callable=AsyncMock), unittest.mock.patch.object(Message,'answer_photo',new_callable=AsyncMock) as answers:
                await dp.feed_update(bot,Update(update_id=1,message=msg),db=self.db,config=CONFIG)
                self.assert_home(answers.await_args)
            swallowed.assert_not_awaited()
            self.assertIsNone(await state.get_state())
            self.assertEqual(await state.get_data(),{})
            await bot.session.close()

    async def test_menu_cancels_only_own_pending_intents(self):
        from bot.handlers.navigation import on_menu
        oauth = SimpleNamespace(cancel_streamer_connect_intent=AsyncMock())
        await self.state.set_data({'telegram_oauth_intent':'own-connect','telegram_community_intent':'own-channel'})
        await on_menu(message(),self.state,self.db,CONFIG,oauth)
        oauth.cancel_streamer_connect_intent.assert_awaited_once_with(101,'own-connect')
        self.db.cancel_community_intent.assert_awaited_once_with('own-channel',101)
