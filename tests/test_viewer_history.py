"""Personal history records terminal delivery facts, not observed live guesses."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationTerminalError, NotificationWorker
from bot.viewer_history import ViewerHistoryService
from bot.viewer_reminders import ViewerReminderService
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


class ViewerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.service = ViewerHistoryService(self.db)
        self.queue = NotificationQueue(self.db)
        self.now = time.time()
        await self.db.add_channel(101, "alpha")
        self.grant_id = await self.db.issue_test_viewer_plus(
            101, "history-101", starts_at=self.now - 5,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now,
        )

    def worker(self, sender):
        return NotificationWorker(
            self.queue, sender, max_concurrency=1,
            per_chat_interval=0, group_chat_interval=0, global_interval=0,
            clock=lambda: self.now,
        )

    async def page(self, user_id=101, before_id=None, limit=20):
        return await self.service.list_events(
            user_id, before_id=before_id, limit=limit, now=self.now,
        )

    async def test_observation_and_pending_job_do_not_claim_delivery(self):
        await self.db.set_live_state(101, "alpha", True, "stream-1", None,
                                     "Title", broadcaster_id="1001")
        self.assertEqual((await self.page()).events, ())
        await self.queue.enqueue("go_live", 101, "alpha", "stream-1", 1,
                                 due_at=self.now, now=self.now)
        self.assertEqual((await self.page()).events, ())

        async def confirmed(_job):
            return NotificationOutcome.SENT

        self.assertEqual(await self.worker(confirmed).run_once(), 1)
        events = (await self.page()).events
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].kind, events[0].outcome, events[0].login),
                         ("go_live", "sent", "alpha"))
        self.assertEqual(await self.worker(confirmed).run_once(), 0)
        self.assertEqual(len((await self.page()).events), 1)

    async def test_suppressed_unknown_pagination_ownership_expiry_and_retention(self):
        await self.queue.enqueue("go_live", 101, "alpha", "stream-1", 1,
                                 due_at=self.now, now=self.now)
        await self.worker(lambda _job: self._return(NotificationOutcome.STALE)).run_once()
        self.now += 1
        await self.queue.enqueue("viewer_category_change", 101, "alpha", "stream-1", 1,
                                 due_at=self.now, now=self.now)

        async def uncertain(_job):
            raise TimeoutError("external response unavailable")

        await self.worker(uncertain).run_once()
        self.now += 1
        await self.queue.enqueue("viewer_reminder", 101, "alpha", "stream-1", 1,
                                 due_at=self.now, now=self.now)
        await self.worker(lambda _job: self._return(NotificationOutcome.SENT)).run_once()
        first = await self.page(limit=2)
        self.assertEqual([(event.kind, event.outcome) for event in first.events], [
            ("viewer_reminder", "sent"), ("viewer_category_change", "unknown"),
        ])
        second = await self.page(before_id=first.next_before_id, limit=2)
        self.assertEqual([(event.kind, event.outcome) for event in second.events], [
            ("go_live", "suppressed"),
        ])
        self.assertIsNone(second.next_before_id)
        exact = await self.page(limit=3)
        self.assertEqual(len(exact.events), 3)
        self.assertIsNone(exact.next_before_id)
        await self.db.issue_test_viewer_plus(
            202, "history-202", starts_at=self.now - 5,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now,
        )
        self.assertEqual((await self.page(202)).events, ())
        await self.db.revoke_test_viewer_plus(self.grant_id, revoked_at=self.now,
                                              issued_by=425785231)
        with self.assertRaises(PermissionError):
            await self.page()
        self.assertEqual(await self.service.purge_expired(
            now=self.now + 30 * 86400 + 1,
        ), 3)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM viewer_event_history")
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_direct_legacy_live_requires_confirmed_message_and_saved_state(self):
        stream = StreamInfo(
            "alpha", "stream-2", "Live", "Minecraft", 20,
            "2026-01-01T00:00:00Z",
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": stream}))
        bot = SimpleNamespace(send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=901),
        ))
        poller = StreamPoller(bot, self.db, twitch, 60,
                              notification_queue_enabled=False)
        with patch("bot.poller.time.time", return_value=self.now):
            await poller._check_streams()
        bot.send_message.assert_awaited()
        events = (await self.page()).events
        self.assertEqual([(event.kind, event.outcome) for event in events],
                         [("go_live", "sent")])
        self.assertEqual(events[0].logical_stream_id, "stream-2")

    async def test_free_delivery_does_not_create_paid_history_rows(self):
        await self.db.add_channel(202, "beta")
        await self.queue.enqueue("go_live", 202, "beta", "stream-free", 1,
                                 due_at=self.now, now=self.now)
        await self.worker(lambda _job: self._return(NotificationOutcome.SENT)).run_once()
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM viewer_event_history WHERE telegram_user_id=202"
        )
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_terminal_rejection_is_suppressed_not_sent(self):
        for kind in ("go_live", "viewer_category_change"):
            await self.queue.enqueue(kind, 101, "alpha", "stream-reject", 1,
                                     due_at=self.now, now=self.now)

        async def rejected(_job):
            raise NotificationTerminalError("recipient unavailable")

        await self.worker(rejected).run_once()
        await self.worker(rejected).run_once()
        self.assertEqual({(event.kind, event.outcome) for event in (await self.page()).events},
                         {("go_live", "suppressed"),
                          ("viewer_category_change", "suppressed")})

    async def test_expired_reminder_send_lease_records_unknown_once(self):
        await self.db.set_live_state(101, "alpha", True, "stream-lease", 700,
                                     "Title", broadcaster_id="1001",
                                     last_seen_live_at=self.now)
        reminders = ViewerReminderService(self.db)
        saved = await reminders.set_reminder(101, "1001", "stream-lease", 15,
                                             now=self.now)
        jobs = await self.queue.claim_due(saved.due_at, limit=1, lease_seconds=60)
        self.assertEqual(len(jobs), 1)
        await self.db.set_live_state(101, "alpha", True, "stream-lease", 700,
                                     "Title", broadcaster_id="1001",
                                     last_seen_live_at=saved.due_at)
        self.assertTrue(await reminders.begin_delivery(jobs[0], now=saved.due_at))
        self.assertEqual((await self.page()).events, ())
        self.assertEqual(await self.queue.claim_due(saved.due_at + 61,
                                                    limit=1, lease_seconds=60), [])
        self.now = saved.due_at + 61
        self.assertEqual([(event.kind, event.outcome) for event in (await self.page()).events],
                         [("viewer_reminder", "unknown")])
        await self.queue.claim_due(self.now + 1, limit=1, lease_seconds=60)
        self.assertEqual(len((await self.page()).events), 1)

    async def test_crash_after_confirmed_reminder_send_keeps_sent_outcome(self):
        await self.db.set_live_state(101, "alpha", True, "stream-confirmed", 700,
                                     "Title", broadcaster_id="1001",
                                     last_seen_live_at=self.now)
        reminders = ViewerReminderService(self.db)
        saved = await reminders.set_reminder(101, "1001", "stream-confirmed", 15,
                                             now=self.now)
        job = (await self.queue.claim_due(saved.due_at, limit=1, lease_seconds=60))[0]
        await self.db.set_live_state(101, "alpha", True, "stream-confirmed", 700,
                                     "Title", broadcaster_id="1001",
                                     last_seen_live_at=saved.due_at)
        self.assertTrue(await reminders.begin_delivery(job, now=saved.due_at))
        self.assertTrue(await reminders.mark_sent(job, now=saved.due_at + 1))
        self.now = saved.due_at + 61
        self.assertEqual(await self.queue.claim_due(self.now, limit=1,
                                                    lease_seconds=60), [])
        self.assertEqual([(event.kind, event.outcome) for event in (await self.page()).events],
                         [("viewer_reminder", "sent")])

    @staticmethod
    async def _return(outcome):
        return outcome


if __name__ == "__main__":
    unittest.main()
