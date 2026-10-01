import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database import Database
from bot.streamer_community import verify_community_permission


class CommunityPermissionTests(unittest.IsolatedAsyncioTestCase):
    def bot(self, *, chat_type="supergroup", user_status="administrator",
            bot_status="administrator", can_post=True, can_edit=True):
        async def member(_chat_id, user_id):
            if user_id == 101:
                return SimpleNamespace(status=user_status)
            return SimpleNamespace(
                status=bot_status, can_post_messages=can_post,
                can_edit_messages=can_edit,
            )
        return SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=SimpleNamespace(type=chat_type, title="My group")),
            get_chat_member=AsyncMock(side_effect=member),
        )

    async def test_group_and_channel_require_user_and_bot_rights(self):
        group = await verify_community_permission(self.bot(), -1001, 101)
        self.assertEqual((group.chat_id, group.chat_type, group.title), (-1001, "supergroup", "My group"))
        channel = await verify_community_permission(
            self.bot(chat_type="channel"), -1002, 101,
        )
        self.assertEqual(channel.chat_type, "channel")
        for bot in (
            self.bot(user_status="member"),
            self.bot(bot_status="member"),
            self.bot(chat_type="channel", can_post=False),
            self.bot(chat_type="channel", can_edit=False),
            self.bot(chat_type="private"),
        ):
            with self.subTest(bot=bot):
                self.assertIsNone(await verify_community_permission(bot, -1001, 101))

    async def test_invalid_id_and_api_error_fail_closed(self):
        bot = self.bot()
        self.assertIsNone(await verify_community_permission(bot, 101, 101))
        bot.get_chat.side_effect = RuntimeError("telegram unavailable")
        self.assertIsNone(await verify_community_permission(bot, -1001, 101))


class CommunityStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=50)

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_two_communities_work_for_free_and_are_scoped_to_owner(self):
        self.assertTrue(await self.db.add_streamer_community(101, -1001, "Group", "supergroup", now=100))
        await self.db.issue_test_streamer_plus(
            "11", "grant", starts_at=100, expires_at=200,
            issued_by=425785231, now=90,
        )
        self.assertTrue(await self.db.add_streamer_community(101, -1001, "Group", "supergroup", now=150))
        self.assertTrue(await self.db.add_streamer_community(101, -1002, "Channel", "channel", now=150))
        self.assertEqual(
            await self.db.list_streamer_communities(101),
            [(-1002, "Channel", "channel"), (-1001, "Group", "supergroup")],
        )
        self.assertEqual(await self.db.list_streamer_communities(202), [])
        self.assertTrue(await self.db.add_streamer_community(101, -1003, "Late", "supergroup", now=200))
        self.assertEqual(len(await self.db.list_streamer_communities(101)), 3)

    async def test_free_verified_streamer_can_link_own_community(self):
        self.assertTrue(await self.db.add_streamer_community(
            101, -1001, "Free group", "supergroup", now=100,
        ))
        self.assertEqual(await self.db.list_streamer_communities(101), [
            (-1001, "Free group", "supergroup"),
        ])
        self.assertEqual(await self.db.list_streamer_communities(202), [])


if __name__ == "__main__":
    unittest.main()
