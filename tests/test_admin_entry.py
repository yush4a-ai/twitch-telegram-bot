import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatType

from bot.handlers.streams import _main_menu_keyboard, cmd_admin, cmd_start
from main import _private_bot_commands


OWNER_ID = 425785231
CONFIG = SimpleNamespace(owner_chat_id=OWNER_ID, oauth_public_base_url="https://staging.example.test", admin_panel_access_key="emergency-key")


def fake_message(user_id: int, chat_id: int, chat_type: str):
    return SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type), from_user=SimpleNamespace(id=user_id), answer=AsyncMock())


class AdminEntryTests(unittest.IsolatedAsyncioTestCase):
    def test_global_private_commands_do_not_list_admin(self):
        self.assertNotIn("admin", [item.command for item in _private_bot_commands([])])
        self.assertIn("admin", [item.command for item in _private_bot_commands([], owner=True)])

    def test_default_menus_never_expose_admin(self):
        for chat_type in (ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
            keyboard = _main_menu_keyboard(chat_type)
            self.assertNotIn("Админ-панель", str(keyboard))
            self.assertNotIn("/admin", str(keyboard))

    async def test_non_owner_and_group_command_have_no_answer(self):
        for message in (fake_message(OWNER_ID + 1, OWNER_ID + 1, ChatType.PRIVATE), fake_message(OWNER_ID, -100123, ChatType.SUPERGROUP), fake_message(OWNER_ID, -100123, ChatType.CHANNEL)):
            await cmd_admin(message, CONFIG)
            message.answer.assert_not_awaited()

    async def test_owner_private_command_has_only_owner_entry(self):
        message = fake_message(OWNER_ID, OWNER_ID, ChatType.PRIVATE)
        await cmd_admin(message, CONFIG)
        message.answer.assert_awaited_once()
        args, kwargs = message.answer.await_args
        self.assertIn("Админ-панель", args[0])
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.web_app.url, "https://staging.example.test/admin")

    async def test_start_menu_is_owner_only(self):
        state = SimpleNamespace(clear=AsyncMock())
        db = SimpleNamespace(mark_known_private_user=AsyncMock())
        owner = fake_message(OWNER_ID, OWNER_ID, ChatType.PRIVATE)
        regular = fake_message(OWNER_ID + 1, OWNER_ID + 1, ChatType.PRIVATE)
        group = fake_message(OWNER_ID, -100123, ChatType.SUPERGROUP)
        for message in (owner, regular, group):
            await cmd_start(message, state, db, CONFIG)
        self.assertIn("Админ-панель", str(owner.answer.await_args.kwargs["reply_markup"]))
        self.assertNotIn("Админ-панель", str(regular.answer.await_args.kwargs["reply_markup"]))
        self.assertNotIn("Админ-панель", str(group.answer.await_args.kwargs["reply_markup"]))


if __name__ == "__main__":
    unittest.main()
