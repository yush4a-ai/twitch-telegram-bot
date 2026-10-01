import unittest
from unittest.mock import patch

from bot.capabilities import CapabilityService
from bot.database import Database


class MiniAppCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.access = CapabilityService(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_free_keeps_basic_notifications(self):
        await self.db.add_channel(101, "alpha")
        result = await self.access.for_user(101, now=150)
        self.assertTrue(result.basic_notifications)
        self.assertEqual(result.viewer_channel_limit, 50)
        self.assertEqual(result.viewer_video_slots, 0)
        self.assertFalse(result.viewer_filters)
        self.assertFalse(result.viewer_category_alerts)
        self.assertFalse(result.streamer_preview)

    async def test_viewer_and_streamer_products_are_independent(self):
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=80)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=80)
        await self.db.issue_test_viewer_plus(
            101, "viewer-only", starts_at=100, expires_at=200,
            issued_by=999, now=90,
        )
        await self.db.issue_test_streamer_plus(
            "22", "streamer-only", starts_at=100, expires_at=200,
            issued_by=999, now=90,
        )
        viewer = await self.access.for_user(101, now=150)
        streamer = await self.access.for_user(202, now=150)
        self.assertTrue(viewer.viewer_filters)
        self.assertTrue(viewer.viewer_category_alerts)
        self.assertEqual(viewer.viewer_channel_limit, 200)
        self.assertEqual(viewer.viewer_video_slots, 5)
        self.assertFalse(viewer.streamer_custom_post)
        self.assertFalse(streamer.viewer_filters)
        self.assertEqual(streamer.viewer_channel_limit, 50)
        self.assertTrue(streamer.streamer_preview)
        self.assertTrue(streamer.streamer_custom_post)
        self.assertTrue(streamer.streamer_post_stats)

    async def test_placement_cannot_borrow_other_broadcaster_plus(self):
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=80)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=80)
        await self.db.issue_test_streamer_plus(
            "11", "placement-11", starts_at=100, expires_at=200,
            issued_by=999, now=90,
        )
        self.assertTrue(await self.db.add_streamer_community(
            101, -1001, "Alpha group", "group", now=150,
        ))
        own = await self.access.for_placement("11", -1001, now=150)
        wrong_broadcaster = await self.access.for_placement("22", -1001, now=150)
        wrong_chat = await self.access.for_placement("11", -1002, now=150)
        self.assertTrue(own.basic_post)
        self.assertTrue(own.streamer_custom_post)
        self.assertTrue(own.streamer_preview)
        self.assertFalse(wrong_broadcaster.streamer_custom_post)
        self.assertFalse(wrong_broadcaster.basic_post)
        self.assertFalse(wrong_chat.streamer_preview)

    async def test_expiry_is_effective_without_ui_refresh(self):
        await self.db.issue_test_viewer_plus(
            101, "viewer-expiry", starts_at=100, expires_at=200,
            issued_by=999, now=90,
        )
        before = await self.access.for_user(101, now=199.99)
        after = await self.access.for_user(101, now=200)
        self.assertTrue(before.viewer_filters)
        self.assertEqual(before.viewer_video_slots, 5)
        self.assertFalse(after.viewer_filters)
        self.assertEqual(after.viewer_video_slots, 0)
        self.assertTrue(after.basic_notifications)

    async def test_backend_access_failure_does_not_enable_plus(self):
        with patch.object(self.db, "has_viewer_plus", side_effect=RuntimeError("DB unavailable")), patch.object(
            self.db, "has_streamer_plus", side_effect=RuntimeError("DB unavailable")
        ):
            result = await self.access.for_user(101, now=150)
        self.assertTrue(result.basic_notifications)
        self.assertFalse(result.viewer_filters)
        self.assertFalse(result.streamer_custom_post)
        self.assertEqual(result.viewer_video_slots, 0)
