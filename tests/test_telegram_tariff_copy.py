"""Entry labels differ from immutable product and payment names."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from bot.database import Database
from bot.handlers.telegram_plus import cb_plus,cb_buy
from bot.handlers.telegram_streamer import cb_streamer
from bot.telegram_ui import more_keyboard
from tests.test_telegram_navigation import message


class TariffCopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_entry_labels_and_product_cta_keep_exact_prices(self):
        db=Database(':memory:');await db.connect();self.addAsyncCleanup(db.close)
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        msg=message();cb=SimpleNamespace(message=msg,from_user=msg.from_user,answer=AsyncMock(),data='menu:plus')
        self.assertEqual(next(b.text for row in more_keyboard().inline_keyboard for b in row if b.callback_data=='menu:plus'),'⭐ Тариф')
        await cb_plus(cb,state,db)
        call=msg.edit_text.await_args;self.assertIn('<b>Зритель Plus</b>\n<b>150 ₽ / месяц</b>',call.args[0])
        buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertEqual(buttons[0].text,'Оформить Зритель Plus: 150 ₽')
        self.assertEqual(buttons[1].text,'Тариф для стримера')
        await db.link_streamer_identity(101,'11','alpha',verified_at=1)
        cb.data='menu:streamer';await cb_streamer(cb,db)
        self.assertEqual(next(b.text for row in msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row if b.callback_data=='plus:show:streamer_plus:streamer'),'Тариф для стримера')
        cb.data='plus:buy:streamer_plus';await cb_buy(cb,state,db)
        self.assertIn('<b>Стример Plus</b>\n<b>300 ₽ / месяц</b>',msg.edit_text.await_args.args[0])
