import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


class ViewerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.add_channel(101, "alpha")
        self.stream = StreamInfo("alpha", "s1", "Live speedrun", "Minecraft", 42, "2026-01-01T00:00:00Z")
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": self.stream}))
        self.poller = StreamPoller(
            SimpleNamespace(), self.db, twitch, 60,
            notification_queue_enabled=True, viewer_filters_enabled=True,
        )
        self.poller._notify = AsyncMock(return_value=321)
        self.grant = await self.db.issue_test_viewer_plus(
            101, "viewer-delivery", starts_at=0, expires_at=9999999999,
            issued_by=425785231, now=0,
        )
        await self.db.save_viewer_filter(
            101, "alpha", expected_version=0,
            games=["Minecraft"], title_keywords=["speedrun"],
            exclude_keywords=["rerun"], now=1000,
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def test_filter_suppresses_only_private_new_post_but_keeps_stream_state(self):
        await self.db.add_channel(-1001, "alpha")
        self.stream.game_name = "Just Chatting"
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        self.assertTrue((await self.db.get_live_state(101, "alpha"))[0])
        cursor = await self.db.conn.execute(
            "SELECT (SELECT COUNT(*) FROM stream_observations) + "
            "(SELECT COUNT(*) FROM stream_samples)"
        )
        self.assertGreater((await cursor.fetchone())[0], 0)
        queue = NotificationQueue(self.db)
        jobs = await queue.claim_due(1000.0, limit=10, lease_seconds=60.0)
        self.assertEqual([job.chat_id for job in jobs], [-1001])
        self.poller._notify.assert_not_awaited()

    async def test_worker_rechecks_rule_changed_after_queue(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        job = (await NotificationQueue(self.db).claim_due(
            1000.0, limit=1, lease_seconds=60.0,
        ))[0]
        await self.db.save_viewer_filter(
            101, "alpha", expected_version=1,
            games=["Other Game"], title_keywords=[], exclude_keywords=[], now=1100,
        )
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.poller._notify.assert_not_awaited()

    async def test_direct_send_suppressed_and_revoke_restores_free_behavior(self):
        self.poller._notification_queue_enabled = False
        self.stream.title = "Casual stream"
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        self.poller._notify.assert_not_awaited()
        self.assertTrue((await self.db.get_live_state(101, "alpha"))[0])
        await self.db.revoke_test_viewer_plus(
            self.grant, revoked_at=1100, issued_by=425785231,
        )
        with patch("bot.poller.time.time", return_value=1200.0):
            await self.poller._check_streams()
        self.poller._notify.assert_awaited_once()

    async def test_existing_post_replacement_can_keep_current_post_after_filter_change(self):
        self.poller._bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=777)
        )
        self.poller._build_private_live_text = AsyncMock(return_value="base")
        self.poller._build_keyboard = AsyncMock(return_value=None)
        self.poller._with_streamer_template = AsyncMock(
            side_effect=lambda _chat, _login, content: content,
        )
        self.assertIsNone(await StreamPoller._notify(
            self.poller, 101, "alpha", "Casual stream", 42, "Minecraft", direct=True,
        ))
        self.poller._bot.send_message.assert_not_awaited()
        self.assertEqual(await StreamPoller._notify(
            self.poller, 101, "alpha", "Casual stream", 42, "Minecraft",
            direct=True, respect_viewer_filter=False,
        ), 777)
        self.poller._bot.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
