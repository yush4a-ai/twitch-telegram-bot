import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramRetryAfter

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationWorker
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


class QueuedLiveUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.add_channel(101, "alpha")
        await self.db.set_live_state(101, "alpha", True, "s1", 701, "Before")
        self.bot = SimpleNamespace(
            edit_message_text=AsyncMock(return_value=True),
            edit_message_caption=AsyncMock(return_value=True),
            edit_message_media=AsyncMock(return_value=True),
        )
        self.stream = StreamInfo(
            "alpha", "s1", "Updated title", "Game", 42,
            "2026-01-01T00:00:00Z",
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": self.stream}))
        self.poller = StreamPoller(
            self.bot, self.db, twitch, 60, notification_queue_enabled=True,
        )
        self.queue = NotificationQueue(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def poll(self, now=1000.0):
        with patch("bot.poller.time.time", return_value=now):
            await self.poller._check_streams()

    async def claim(self, now=1000.0):
        jobs = await self.queue.claim_due(now, limit=1, lease_seconds=60.0)
        self.assertEqual(len(jobs), 1)
        return jobs[0]

    async def test_poll_queues_update_without_editing_and_worker_uses_latest_sample(self):
        await self.poll()
        self.bot.edit_message_text.assert_not_awaited()
        job = await self.claim()
        self.assertEqual((job.kind, job.payload_version), ("live_update", 701))
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        text = self.bot.edit_message_text.await_args.args[0]
        self.assertIn("Updated title", text)
        self.assertIn("42", text)

    async def test_new_sample_during_lease_requeues_same_job_with_newest_content(self):
        await self.poll()
        first = await self.claim()
        self.stream.viewer_count = 99
        await self.poll(1060.0)
        self.assertTrue(await self.queue.ack(
            first.id, first.attempt_count, now=1060.1, revision=first.revision,
        ))
        second = await self.claim(1060.1)
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.revision, first.revision + 1)
        self.assertEqual(await self.poller.send_queued_job(second), NotificationOutcome.SENT)
        self.assertIn("99", self.bot.edit_message_text.await_args.args[0])

    async def test_offline_or_replaced_message_is_stale_before_edit(self):
        await self.poll()
        job = await self.claim()
        await self.db.set_live_state(
            101, "alpha", False, "s1", 701, "Updated title", offline_since=1001.0,
        )
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.edit_message_text.assert_not_awaited()
        await self.db.set_live_state(101, "alpha", True, "s1", 702, "Back")
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.edit_message_text.assert_not_awaited()

    async def test_offline_transition_between_sample_and_edit_rejects_live_content(self):
        await self.poll()
        job = await self.claim()
        real_is_channel = self.db.is_telegram_channel

        async def offline_before_edit(chat_id):
            result = await real_is_channel(chat_id)
            await self.db.set_live_state(
                101, "alpha", False, "s1", 701, "Updated title",
                offline_since=1001.0,
            )
            return result

        self.db.is_telegram_channel = offline_before_edit
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.edit_message_text.assert_not_awaited()

    async def test_telegram_retry_after_sets_job_due_without_sleeping_poll(self):
        await self.poll()
        self.bot.edit_message_text.side_effect = TelegramRetryAfter(
            SimpleNamespace(), "retry", retry_after=7,
        )
        worker = NotificationWorker(
            self.queue, self.poller.send_queued_job,
            max_concurrency=1, per_chat_interval=0,
            group_chat_interval=0, global_interval=0,
            clock=lambda: 1000.0,
        )
        self.assertEqual(await worker.run_once(), 1)
        cursor = await self.db.conn.execute(
            "SELECT status, due_at, last_error_class FROM notification_jobs "
            "WHERE kind = 'live_update'"
        )
        self.assertEqual(
            await cursor.fetchone(),
            ("pending", 1007.0, "TelegramRetryAfter"),
        )

    async def test_thumbnail_is_applied_by_worker_after_content_edit(self):
        self.stream.thumbnail_url = "https://example.test/{width}x{height}.jpg"
        await self.poll()
        self.bot.edit_message_media.assert_not_awaited()
        job = await self.claim()
        self.assertIn("640x360", job.media_url)
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.bot.edit_message_media.assert_awaited_once()
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")

    async def test_animation_replacement_after_claim_is_not_overwritten_by_thumbnail(self):
        self.stream.thumbnail_url = "https://example.test/{width}x{height}.jpg"
        await self.poll()
        job = await self.claim()
        await self.db.set_live_message_kind_if_current(
            101, "alpha", "s1", 701, "animation", False,
        )
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.bot.edit_message_caption.assert_awaited_once()
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "animation")

    async def test_offline_after_text_edit_prevents_late_thumbnail(self):
        self.stream.thumbnail_url = "https://example.test/{width}x{height}.jpg"
        await self.poll()
        job = await self.claim()

        async def go_offline_after_text(*_args, **_kwargs):
            await self.db.set_live_state(
                101, "alpha", False, "s1", 701, "Updated title",
                offline_since=1001.0,
            )
            return True

        self.bot.edit_message_text.side_effect = go_offline_after_text
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.edit_message_media.assert_not_awaited()

    async def test_pending_media_reconcile_rechecks_offline_before_caption_edit(self):
        await self.poll()
        job = await self.claim()
        self.assertTrue(await self.db.begin_animation_transition(101, "alpha", "s1", 701))
        real_read = self.db.get_live_post_state
        calls = 0

        async def offline_before_reconcile(*args):
            nonlocal calls
            calls += 1
            if calls == 4:
                await self.db.set_live_state(
                    101, "alpha", False, "s1", 701, "Updated title",
                    offline_since=1001.0,
                )
            return await real_read(*args)

        self.db.get_live_post_state = offline_before_reconcile
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.bot.edit_message_caption.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
