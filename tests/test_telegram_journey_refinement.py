"""Native journey contracts with real router/FSM/SQLite and fake transport."""
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
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
