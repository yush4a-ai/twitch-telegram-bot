"""C6: /stats и /health доступны только в личном чате, где пишет сам владелец.

Раньше хватало совпадения ``chat_id``: при ошибочно заданном групповом ID
телеметрию увидел бы любой участник группы. Теперь OWNER_CHAT_ID обязан быть
положительным (это личный чат), а автор сообщения сверяется с владельцем.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiogram.enums import ChatType

from bot.config import ConfigError, load_config
from bot.handlers.streams import cmd_health, cmd_stats
from tests.test_admin_telegram_auth import OWNER_ID


class OwnerCommandScopeTests(unittest.IsolatedAsyncioTestCase):
    def stats_db(self):
        return SimpleNamespace(get_bot_stats=AsyncMock(return_value={
            "private_users": 1, "groups": 0, "tracked_channels": 0,
            "unique_twitch_channels": 0, "live_now": 0, "history_rows": 0,
            "quiet_hours_chats": 0, "deferred_reports": 0,
        }))

    def health_dependencies(self):
        never = Mock(side_effect=AssertionError("зависимость не должна вызываться"))
        return (
            SimpleNamespace(health_snapshot=never),
            SimpleNamespace(health_snapshot=never),
            SimpleNamespace(health_snapshot=never),
            SimpleNamespace(health_snapshot=never),
            SimpleNamespace(health_snapshot=AsyncMock(side_effect=AssertionError)),
        )

    async def test_stats_is_refused_in_a_group_even_with_the_owner_chat_id(self):
        db = self.stats_db()
        answer = AsyncMock()
        group = SimpleNamespace(
            chat=SimpleNamespace(id=OWNER_ID, type=ChatType.GROUP),
            from_user=SimpleNamespace(id=OWNER_ID), answer=answer,
        )
        await cmd_stats(group, db, SimpleNamespace(owner_chat_id=OWNER_ID))
        answer.assert_not_awaited()
        db.get_bot_stats.assert_not_awaited()

    async def test_stats_is_refused_when_the_author_is_not_the_owner(self):
        db = self.stats_db()
        answer = AsyncMock()
        foreign = SimpleNamespace(
            chat=SimpleNamespace(id=OWNER_ID, type=ChatType.PRIVATE),
            from_user=SimpleNamespace(id=OWNER_ID + 1), answer=answer,
        )
        await cmd_stats(foreign, db, SimpleNamespace(owner_chat_id=OWNER_ID))
        answer.assert_not_awaited()
        db.get_bot_stats.assert_not_awaited()

    async def test_stats_is_sent_in_the_owners_own_private_chat(self):
        db = self.stats_db()
        answer = AsyncMock()
        owner = SimpleNamespace(
            chat=SimpleNamespace(id=OWNER_ID, type=ChatType.PRIVATE),
            from_user=SimpleNamespace(id=OWNER_ID), answer=answer,
        )
        await cmd_stats(owner, db, SimpleNamespace(owner_chat_id=OWNER_ID))
        answer.assert_awaited_once()
        db.get_bot_stats.assert_awaited_once()

    async def test_health_is_refused_in_a_group_and_never_collects_data(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=OWNER_ID, type=ChatType.SUPERGROUP),
            from_user=SimpleNamespace(id=OWNER_ID), answer=AsyncMock(),
        )
        await cmd_health(message, SimpleNamespace(owner_chat_id=OWNER_ID),
                         *self.health_dependencies())
        message.answer.assert_not_awaited()

    async def test_health_requires_the_owner_as_the_author_of_a_private_message(self):
        for author in (None, OWNER_ID + 1):
            with self.subTest(author=author):
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=OWNER_ID, type=ChatType.PRIVATE),
                    from_user=None if author is None else SimpleNamespace(id=author),
                    answer=AsyncMock(),
                )
                await cmd_health(message, SimpleNamespace(owner_chat_id=OWNER_ID),
                                 *self.health_dependencies())
                message.answer.assert_not_awaited()


class OwnerChatIdConfigTests(unittest.TestCase):
    REQUIRED = {
        "TELEGRAM_BOT_TOKEN": "unit-test-token", "TWITCH_CLIENT_ID": "unit-test-client",
        "TWITCH_CLIENT_SECRET": "unit-test-secret", "DB_PATH": ":memory:",
    }

    def test_zero_and_negative_owner_chat_ids_are_configuration_errors(self):
        for raw in ("0", "-1001234567890", "-1"):
            with self.subTest(raw=raw):
                with patch.dict(os.environ, {**self.REQUIRED, "OWNER_CHAT_ID": raw}, clear=True):
                    with self.assertRaisesRegex(ConfigError, "OWNER_CHAT_ID.*больше нуля"):
                        load_config()

    def test_positive_owner_chat_id_and_absent_value_are_accepted(self):
        with patch.dict(os.environ, {**self.REQUIRED, "OWNER_CHAT_ID": "425785231"}, clear=True):
            self.assertEqual(load_config().owner_chat_id, 425785231)
        with patch.dict(os.environ, {**self.REQUIRED, "OWNER_CHAT_ID": ""}, clear=True):
            self.assertIsNone(load_config().owner_chat_id)


if __name__ == "__main__":
    unittest.main()
