import unittest
from types import SimpleNamespace

from bot.database import Database
from bot.poller import StreamPoller


class GrowthActivationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def _activated_at(self, user_id):
        row = await (await self.db.conn.execute(
            "SELECT activated_at FROM growth_attributions WHERE telegram_user_id=?",
            (user_id,),
        )).fetchone()
        return row[0]

    async def test_first_successful_add_activates_but_limit_and_duplicate_do_not(self):
        self.assertTrue(await self.db.record_growth_touch(101, "src_site", now=100))
        self.assertEqual(await self.db.add_channel_with_limit(101, "alpha", 0), "limit")
        self.assertIsNone(await self._activated_at(101))
        self.assertEqual(await self.db.add_channel_with_limit(101, "alpha", 1), "created")
        activated = await self._activated_at(101)
        self.assertIsNotNone(activated)
        self.assertGreaterEqual(activated, 100)
        self.assertEqual(await self.db.add_channel_with_limit(101, "alpha", 1), "already")
        self.assertEqual(await self._activated_at(101), activated)

    async def test_simple_add_activates_only_an_attributed_private_user(self):
        self.assertTrue(await self.db.record_growth_touch(202, "src_site", now=100))
        self.assertTrue(await self.db.add_channel(202, "beta"))
        self.assertIsNotNone(await self._activated_at(202))
        self.assertFalse(await self.db.add_channel(202, "beta"))
        self.assertTrue(await self.db.add_channel(-1001, "beta"))
        self.assertEqual((await (await self.db.conn.execute(
            "SELECT COUNT(*) FROM growth_attributions"
        )).fetchone())[0], 1)

    async def test_staging_live_post_track_link_points_to_testbot(self):
        poller = StreamPoller(
            SimpleNamespace(), self.db, SimpleNamespace(), 60,
            bot_username="SignalStreamsBot",
        )
        text = await poller._build_live_text(
            "paverpapa", "Demo", "Game", 12, None,
            include_track_link=True, is_channel=True,
        )
        self.assertIn("https://t.me/SignalStreamsBot?start=track_paverpapa", text)
        self.assertNotIn("https://t.me/twitchSignalBot", text)


if __name__ == "__main__":
    unittest.main()
