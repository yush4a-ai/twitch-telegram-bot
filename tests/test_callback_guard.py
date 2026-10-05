"""Кнопка отказа от рассылок должна работать и на старых сообщениях.

Telegram не отдаёт тело сообщений старше 48 часов, а рассылку читают и через
дни. Отписке тело не нужно — достаточно автора нажатия.
"""
import unittest
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, User

from bot.middlewares import CallbackGuardMiddleware


def callback(data: str, message=None) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=777010, is_bot=False, first_name="Тест"),
        chat_instance="ci",
        data=data,
        message=message,
    )


def old_message() -> InaccessibleMessage:
    # У недоступного сообщения и идентификатор, и дата нулевые.
    return InaccessibleMessage(chat=Chat(id=1, type="private"), message_id=0, date=0)


class CallbackGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_optout_reaches_the_handler_without_a_message_body(self):
        handler = AsyncMock(return_value="ok")
        result = await CallbackGuardMiddleware()(
            handler, callback("broadcast_optout", old_message()), {})
        self.assertEqual(result, "ok")
        handler.assert_awaited_once()

    async def test_other_old_button_is_still_refused(self):
        handler = AsyncMock(return_value="ok")
        result = await CallbackGuardMiddleware()(
            handler, callback("menu:more", old_message()), {})
        # Старая кнопка меню по-прежнему не доходит до обработчика.
        self.assertIsNone(result)
        handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
