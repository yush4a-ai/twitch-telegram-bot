import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import (
    NotificationOutcome, NotificationRetryAfter, NotificationWorker,
)
from bot.poller import StreamPoller


class QueuedOfflineCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.bot = SimpleNamespace(
            delete_message=AsyncMock(return_value=True),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        self.poller = StreamPoller(
            self.bot, self.db, SimpleNamespace(), 60,
            notification_queue_enabled=True,
        )
        self.queue = NotificationQueue(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def seed_offline(self, chat_id, message_id=701, *, offline_age=400, kind="text"):
        await self.db.add_channel(chat_id, "alpha")
        await self.db.set_live_state(
            chat_id, "alpha", True, "s1", message_id, "Live", message_kind=kind
        )
        if kind != "text":
            await self.db.set_live_message_kind_if_current(
                chat_id, "alpha", "s1", message_id, kind, False
            )
        await self.db.set_live_state(
            chat_id, "alpha", False, "s1", message_id, "Live",
            offline_since=time.time() - offline_age, message_kind=kind,
        )

    async def claim(self):
        jobs = await self.queue.claim_due(time.time(), limit=1, lease_seconds=60)
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    async def test_poll_enqueues_once_after_grace_without_telegram_call(self):
        await self.seed_offline(-100, offline_age=100)
        await self.poller._cleanup_offline_posts()
        self.assertEqual((await self.queue.depth_snapshot(time.time()))["pending_jobs"], 0)
        await self.db.set_live_state(
            -100, "alpha", False, "s1", 701, "Live",
            offline_since=time.time() - 400,
        )
        await self.poller._cleanup_offline_posts()
        await self.poller._cleanup_offline_posts()
        self.bot.delete_message.assert_not_awaited()
        cursor = await self.db.conn.execute(
            "SELECT kind, payload_version, status FROM notification_jobs"
        )
        self.assertEqual(await cursor.fetchall(), [("offline_cleanup", 701, "pending")])

    async def test_poll_batches_multiple_cleanup_candidates(self):
        await self.seed_offline(-100)
        await self.seed_offline(101, message_id=702)
        with patch.object(
            self.db, "get_offline_cleanup_state",
            side_effect=AssertionError("per-destination SELECT"),
        ):
            await self.poller._cleanup_offline_posts()
        cursor = await self.db.conn.execute(
            "SELECT chat_id, payload_version FROM notification_jobs ORDER BY chat_id"
        )
        self.assertEqual(await cursor.fetchall(), [(-100, 701), (101, 702)])

    async def test_worker_deletes_current_public_post_and_clears_pointer(self):
        await self.seed_offline(-100)
        await self.poller._cleanup_offline_posts()
        job = await self.claim()
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.bot.delete_message.assert_awaited_once_with(-100, 701)
        self.assertIsNone((await self.db.get_live_post_state(-100, "alpha")).message_id)

    async def test_reconnect_or_new_post_makes_old_cleanup_stale(self):
        await self.seed_offline(-100)
        await self.poller._cleanup_offline_posts()
        job = await self.claim()
        await self.db.set_live_state(-100, "alpha", True, "s1", 702, "Back")
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.delete_message.assert_not_awaited()
        await self.db.set_live_state(
            -100, "alpha", False, "s1", 702, "Back",
            offline_since=time.time() - 400,
        )
        await self.poller._cleanup_offline_posts()
        cursor = await self.db.conn.execute(
            "SELECT payload_version FROM notification_jobs WHERE kind = 'offline_cleanup' "
            "ORDER BY payload_version"
        )
        self.assertEqual(await cursor.fetchall(), [(701,), (702,)])

    async def test_private_post_is_edited_to_ended_not_deleted(self):
        await self.seed_offline(101)
        await self.poller._cleanup_offline_posts()
        job = await self.claim()
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.bot.edit_message_text.assert_awaited_once()
        self.bot.delete_message.assert_not_awaited()
        self.assertTrue(await self.db.get_live_post_ended(101, "alpha"))

    async def test_private_cleanup_clears_finished_session_after_stats_sent(self):
        await self.seed_offline(101)
        await self.db.mark_stats_sent(101, "alpha")
        await self.poller._cleanup_offline_posts()
        self.assertEqual(
            await self.poller.send_queued_job(await self.claim()),
            NotificationOutcome.SENT,
        )
        state = await self.db.get_live_state(101, "alpha")
        self.assertIsNone(state[1])
        self.assertIsNone(state[2])

    async def test_transient_delete_failure_defers_job_and_keeps_pointer(self):
        await self.seed_offline(-100)
        await self.poller._cleanup_offline_posts()
        self.bot.delete_message.side_effect = RuntimeError("transient")
        worker = NotificationWorker(
            self.queue, self.poller.send_queued_job,
            max_concurrency=1, per_chat_interval=0,
            group_chat_interval=0, global_interval=0,
        )
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual((await self.queue.depth_snapshot(time.time()))["pending_jobs"], 1)
        self.assertEqual((await self.db.get_live_post_state(-100, "alpha")).message_id, 701)

    async def test_worker_never_cleans_up_before_grace_even_if_job_is_early(self):
        await self.seed_offline(-100, offline_age=100)
        now = time.time()
        await self.queue.enqueue(
            "offline_cleanup", -100, "alpha", "s1", 701, due_at=now, now=now
        )
        with self.assertRaises(NotificationRetryAfter):
            await self.poller.send_queued_job(await self.claim())
        self.bot.delete_message.assert_not_awaited()

    async def test_private_animation_keeps_media_kind_when_ended(self):
        await self.seed_offline(101, kind="animation")
        await self.poller._cleanup_offline_posts()
        self.assertEqual(
            await self.poller.send_queued_job(await self.claim()),
            NotificationOutcome.SENT,
        )
        self.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(
            (await self.db.get_live_post_state(101, "alpha")).message_kind,
            "animation",
        )


if __name__ == "__main__":
    unittest.main()
