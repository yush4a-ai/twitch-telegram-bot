"""Server-owned, persistent live reminders use the ordinary fake sender queue."""

import os
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramRetryAfter

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.poller import StreamPoller
from bot.viewer_reminders import ReminderInFlightError, ViewerReminderService


class ViewerReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "reminder.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.service = ViewerReminderService(self.db)
        await self.db.add_channel(101, "alpha")
        self.now = time.time()
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=self.now,
        )

    async def grant(self, user_id=101):
        return await self.db.issue_test_viewer_plus(
            user_id, f"reminder-{user_id}", starts_at=self.now - 5,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now,
        )

    async def test_free_and_forged_stream_identity_cannot_schedule(self):
        with self.assertRaises(PermissionError):
            await self.service.set_reminder(101, "1001", "stream-1", 15, now=self.now)
        await self.grant()
        for broadcaster_id, stream_id, delay in (
            ("other", "stream-1", 15), ("1001", "other", 15), ("1001", "stream-1", 20),
        ):
            with self.subTest(broadcaster_id=broadcaster_id, stream_id=stream_id, delay=delay):
                with self.assertRaises((PermissionError, ValueError)):
                    await self.service.set_reminder(101, broadcaster_id, stream_id, delay, now=self.now)
        self.assertEqual(await self.service.for_user(101), {})

    async def test_repeat_click_reschedules_one_job_and_cancel_blocks_old_version(self):
        await self.grant()
        first = await self.service.set_reminder(101, "1001", "stream-1", 15, now=self.now)
        second = await self.service.set_reminder(101, "1001", "stream-1", 30, now=self.now + 5)
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertEqual(second.due_at, self.now + 5 + 1800)
        queue = NotificationQueue(self.db)
        self.assertEqual(await queue.claim_due(self.now + 900, limit=4, lease_seconds=180), [])
        jobs = await queue.claim_due(second.due_at, limit=4, lease_seconds=180)
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0].kind, jobs[0].payload_version), ("viewer_reminder", 2))
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=second.due_at,
        )
        self.assertTrue(await self.service.ready_for_delivery(jobs[0], now=second.due_at))
        await self.service.cancel_reminder(101, "alpha", now=second.due_at)
        self.assertFalse(await self.service.ready_for_delivery(jobs[0], now=second.due_at))

    async def test_delivery_rechecks_offline_other_stream_expiry_unfollow_quiet_hours(self):
        for case in ("offline", "other_stream", "expiry", "unfollow", "quiet", "notify_off"):
            with self.subTest(case=case):
                user_id = {"offline": 111, "other_stream": 112, "expiry": 113,
                           "unfollow": 114, "quiet": 115, "notify_off": 116}[case]
                await self.db.add_channel(user_id, "alpha")
                await self.db.set_live_state(
                    user_id, "alpha", True, "stream-1", 700, "Title",
                    broadcaster_id="1001", last_seen_live_at=self.now,
                )
                grant_id = await self.grant(user_id)
                saved = await self.service.set_reminder(
                    user_id, "1001", "stream-1", 15, now=self.now,
                )
                jobs = await NotificationQueue(self.db).claim_due(
                    saved.due_at, limit=10, lease_seconds=180,
                )
                job = next(row for row in jobs if row.chat_id == user_id)
                await self.db.set_live_state(
                    user_id, "alpha", True, "stream-1", 700, "Title",
                    broadcaster_id="1001", last_seen_live_at=saved.due_at,
                )
                self.assertTrue(await self.service.ready_for_delivery(job, now=saved.due_at))
                if case == "offline":
                    await self.db.set_live_state(user_id, "alpha", False, "stream-1", 700, "Title")
                elif case == "other_stream":
                    await self.db.set_live_state(user_id, "alpha", True, "stream-2", 700, "Title", broadcaster_id="1001")
                elif case == "expiry":
                    await self.db.revoke_test_viewer_plus(grant_id, revoked_at=saved.due_at, issued_by=425785231)
                elif case == "unfollow":
                    await self.db.remove_channel(user_id, "alpha")
                elif case == "quiet":
                    from datetime import datetime, timezone
                    minute = datetime.fromtimestamp(saved.due_at, timezone.utc).hour * 60 + datetime.fromtimestamp(saved.due_at, timezone.utc).minute
                    await self.db.set_quiet_hours(user_id, minute, (minute + 1) % 1440, 0)
                else:
                    await self.db.set_notify_enabled(user_id, "alpha", False)
                self.assertFalse(await self.service.ready_for_delivery(job, now=saved.due_at))

    async def test_fake_sender_sends_once_and_marks_confirmed_outcome(self):
        await self.grant()
        saved = await self.service.set_reminder(101, "1001", "stream-1", 15, now=self.now)
        jobs = await NotificationQueue(self.db).claim_due(saved.due_at, limit=4, lease_seconds=180)
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=saved.due_at,
        )
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))
        poller = StreamPoller(
            bot, self.db, SimpleNamespace(), 60,
            reminder_clock=lambda: saved.due_at,
        )
        outcome = await poller.send_queued_job(jobs[0])
        self.assertEqual(outcome.value, "sent")
        bot.send_message.assert_awaited_once()
        self.assertEqual((await self.service.for_user(101))["alpha"].status, "sent")
        self.assertFalse(await self.service.ready_for_delivery(jobs[0], now=saved.due_at))

    async def test_reschedule_after_queueing_retires_old_pending_job(self):
        await self.grant()
        first = await self.service.set_reminder(101, "1001", "stream-1", 15, now=self.now)
        queue = NotificationQueue(self.db)
        old_job = (await queue.claim_due(first.due_at, limit=1, lease_seconds=60))[0]
        await queue.defer(old_job.id, old_job.attempt_count,
                          due_at=first.due_at + 20, error_class="TelegramRetryAfter",
                          now=first.due_at)
        second = await self.service.set_reminder(101, "1001", "stream-1", 30,
                                                 now=self.now + 10)
        cursor = await self.db.conn.execute(
            "SELECT payload_version,status FROM notification_jobs "
            "WHERE kind='viewer_reminder' AND chat_id=101 ORDER BY id"
        )
        self.assertEqual(await cursor.fetchall(), [(1, "done")])
        self.assertEqual(await queue.claim_due(first.due_at + 20,
                                               limit=1, lease_seconds=60), [])
        await self.service.cancel_reminder(101, "alpha", now=self.now + 11)
        self.assertEqual(await queue.claim_due(second.due_at,
                                               limit=1, lease_seconds=60), [])

    async def test_concurrent_schedule_has_one_current_version(self):
        await self.grant()
        await asyncio.gather(
            self.service.set_reminder(101, "1001", "stream-1", 15, now=self.now),
            self.service.set_reminder(101, "1001", "stream-1", 30, now=self.now),
        )
        state = (await self.service.for_user(101))["alpha"]
        self.assertEqual(state.version, 2)
        jobs = await NotificationQueue(self.db).claim_due(
            self.now + 1800, limit=2, lease_seconds=180,
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].payload_version, 2)

    async def test_uncertain_worker_outcome_is_visible_and_not_retried(self):
        from bot.notification_worker import NotificationWorker

        await self.grant()
        saved = await self.service.set_reminder(101, "1001", "stream-1", 15,
                                                 now=self.now)
        queue = NotificationQueue(self.db)

        async def uncertain(_job):
            raise TimeoutError("Telegram response lost after request")

        worker = NotificationWorker(
            queue, uncertain, max_concurrency=1, per_chat_interval=0,
            group_chat_interval=0, global_interval=0, clock=lambda: saved.due_at,
        )
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual((await self.service.for_user(101))["alpha"].status,
                         "unknown")
        self.assertEqual(await worker.run_once(), 0)
        cursor = await self.db.conn.execute(
            "SELECT status,last_error_class FROM notification_jobs "
            "WHERE kind='viewer_reminder' AND chat_id=101"
        )
        self.assertEqual(await cursor.fetchone(), ("failed", "UnknownOutcome"))

    async def test_inflight_send_rejects_cancel_and_reschedule(self):
        await self.grant()
        saved = await self.service.set_reminder(101, "1001", "stream-1", 15,
                                                 now=self.now)
        job = (await NotificationQueue(self.db).claim_due(
            saved.due_at, limit=1, lease_seconds=180,
        ))[0]
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=saved.due_at,
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(message_id=900)

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 60,
                              reminder_clock=lambda: saved.due_at)
        sending = asyncio.create_task(poller.send_queued_job(job))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with self.assertRaises(ReminderInFlightError):
                await self.service.cancel_reminder(101, "alpha", now=saved.due_at)
            with self.assertRaises(ReminderInFlightError):
                await self.service.set_reminder(101, "1001", "stream-1", 30,
                                                now=saved.due_at)
            self.assertEqual((await self.service.for_user(101))["alpha"].status,
                             "sending")
        finally:
            release.set()
        self.assertEqual((await sending).value, "sent")
        self.assertEqual((await self.service.for_user(101))["alpha"].status,
                         "sent")
        bot.send_message.assert_awaited_once()

    async def test_expired_send_lease_becomes_unknown_without_second_send(self):
        await self.grant()
        saved = await self.service.set_reminder(101, "1001", "stream-1", 15,
                                                 now=self.now)
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(saved.due_at,
                                     limit=1, lease_seconds=180))[0]
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=saved.due_at,
        )
        self.assertTrue(await self.service.begin_delivery(job, now=saved.due_at))
        self.assertEqual(await queue.claim_due(saved.due_at + 181,
                                               limit=1, lease_seconds=180), [])
        self.assertEqual((await self.service.for_user(101))["alpha"].status,
                         "unknown")
        cursor = await self.db.conn.execute(
            "SELECT status,last_error_class FROM notification_jobs WHERE id=?",
            (job.id,),
        )
        self.assertEqual(await cursor.fetchone(), ("failed", "UnknownOutcome"))

    async def test_rate_limit_returns_to_scheduled_then_retries_once(self):
        from bot.notification_worker import NotificationWorker

        await self.grant()
        saved = await self.service.set_reminder(101, "1001", "stream-1", 15,
                                                 now=self.now)
        clock = [saved.due_at]
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=clock[0],
        )
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[
            TelegramRetryAfter(SimpleNamespace(), "retry", retry_after=5),
            SimpleNamespace(message_id=900),
        ]))
        queue = NotificationQueue(self.db)
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 60,
                              reminder_clock=lambda: clock[0])
        worker = NotificationWorker(
            queue, poller.send_queued_job, max_concurrency=1,
            per_chat_interval=0, group_chat_interval=0, global_interval=0,
            clock=lambda: clock[0],
        )
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual((await self.service.for_user(101))["alpha"].status,
                         "scheduled")
        clock[0] += 5
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=clock[0],
        )
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual((await self.service.for_user(101))["alpha"].status,
                         "sent")
        self.assertEqual(bot.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
