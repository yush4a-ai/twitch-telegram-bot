import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatType

from bot.database import Database
from bot.handlers import streams
from main import _private_bot_commands


STAGE = SimpleNamespace(
    growth_enabled=True, admin_telegram_bot_username="TwitchSignalTestbot",
    owner_chat_id=425785231, admin_panel_access_key=None,
    oauth_public_base_url="https://staging.example.test", viewer_plus_enabled=False,
)


def message(chat_id=101, user_id=101, chat_type=ChatType.PRIVATE):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        from_user=SimpleNamespace(id=user_id), answer=AsyncMock(),
    )


class GrowthHandlersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={}))
        self.twitch = SimpleNamespace()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_invite_is_private_stage_only_and_opaque(self):
        own = message()
        await streams.cmd_invite(own, self.db, STAGE)
        text = own.answer.await_args.args[0]
        self.assertIn("https://t.me/TwitchSignalTestbot?start=ref_", text)
        self.assertNotIn("101", text)
        for denied, config in (
            (message(-1001, 101, ChatType.GROUP), STAGE),
            (message(101, 202), STAGE),
            (message(), SimpleNamespace(**{**vars(STAGE), "growth_enabled": False})),
        ):
            with self.subTest(chat=denied.chat.id, stage=config.growth_enabled):
                await streams.cmd_invite(denied, self.db, config)
                denied.answer.assert_not_awaited()

    async def test_start_records_only_exact_private_actor_and_keeps_base_menu(self):
        own = message()
        await streams.cmd_start_link(
            own, SimpleNamespace(args="src_site"), self.state, self.db, self.twitch, STAGE,
        )
        self.assertEqual(own.answer.await_args.args[0],streams.MENU_TEXT)
        self.assertEqual(len(own.answer.await_args.kwargs["reply_markup"].inline_keyboard),4)
        self.assertNotIn("Админ-панель", str(own.answer.await_args.kwargs["reply_markup"]))
        for denied in (
            message(-1001, 101, ChatType.GROUP), message(202, 101),
        ):
            await streams.cmd_start_link(
                denied, SimpleNamespace(args="src_site"), self.state,
                self.db, self.twitch, STAGE,
            )
        rows = await (await self.db.conn.execute(
            "SELECT telegram_user_id,source_kind FROM growth_attributions"
        )).fetchall()
        self.assertEqual(rows, [(101, "site")])
        regular = message(303, 303)
        await streams.cmd_start_link(
            regular, SimpleNamespace(args="src_site"), self.state,
            self.db, self.twitch, SimpleNamespace(**{**vars(STAGE), "growth_enabled": False}),
        )
        self.assertEqual((await (await self.db.conn.execute(
            "SELECT COUNT(*) FROM growth_attributions"
        )).fetchone())[0], 1)

    def test_invite_command_is_stage_private_only_without_admin_leak(self):
        names = lambda **kwargs: [command.command for command in _private_bot_commands([], **kwargs)]
        self.assertNotIn("invite", names())
        self.assertIn("invite", names(growth_enabled=True))
        self.assertNotIn("admin", names(growth_enabled=True))
        self.assertIn("admin", names(growth_enabled=True, owner=True))


if __name__ == "__main__":
    unittest.main()
