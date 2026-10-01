import unittest

from bot.database import Database


class StreamerStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=100)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=100)
        await self.db.add_channel(-1001, "alpha")
        await self.db.add_channel(-1002, "beta")
        await self.db.issue_test_streamer_plus(
            "11", "grant-stats", starts_at=1, expires_at=9999999999,
            issued_by=425785231, now=1,
        )
        await self.db.add_streamer_community(101, -1001, "Alpha group", "supergroup", now=100)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_only_successful_current_broadcaster_posts_are_counted(self):
        await self.db.set_live_state(-1001, "alpha", True, "s1", broadcaster_id="11")
        self.assertTrue(await self.db.set_live_message_if_current(-1001, "alpha", "s1", 701))
        self.assertFalse(await self.db.set_live_message_if_current(-1001, "alpha", "s1", 702))
        await self.db.set_live_state(-1002, "beta", True, "s2", broadcaster_id="22")
        self.assertTrue(await self.db.set_live_message_if_current(-1002, "beta", "s2", 801))
        await self.db.set_live_state(-1001, "alpha", True, "s3", broadcaster_id=None)
        self.assertTrue(await self.db.set_live_message_if_current(-1001, "alpha", "s3", 703))
        await self.db.set_live_state(-1001, "alpha", True, "s4", broadcaster_id="22")
        self.assertTrue(await self.db.set_live_message_if_current(-1001, "alpha", "s4", 704))
        own = await self.db.get_streamer_delivery_stats(101, since=0)
        other = await self.db.get_streamer_delivery_stats(202, since=0)
        self.assertEqual(own["connected_communities"], 1)
        self.assertEqual(own["published_posts"], 1)
        self.assertEqual(other["published_posts"], 1)
        self.assertIsNotNone(own["latest_published_at"])

    async def test_empty_and_out_of_period_statistics(self):
        empty = await self.db.get_streamer_delivery_stats(101, since=0)
        self.assertEqual(empty["published_posts"], 0)
        self.assertIsNone(empty["latest_published_at"])
        await self.db.set_live_state(-1001, "alpha", True, "s1", broadcaster_id="11")
        await self.db.set_live_message_if_current(-1001, "alpha", "s1", 701)
        recent = await self.db.get_streamer_delivery_stats(101, since=9999999999)
        self.assertEqual(recent["published_posts"], 0)
        self.assertIsNone(recent["latest_published_at"])
