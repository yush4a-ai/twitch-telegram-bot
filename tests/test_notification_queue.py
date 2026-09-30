import asyncio
import os
import tempfile
import unittest

from bot.database import Database
from bot.notification_queue import NotificationQueue


class NotificationQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.queue = NotificationQueue(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def _enqueue(self, chat_id=1, due_at=10.0):
        return await self.queue.enqueue(
            "go_live", chat_id, "alpha", "s1", 1, due_at=due_at, now=1.0
        )

    async def test_duplicate_enqueue_keeps_one_job_and_original_due_time(self):
        first = await self._enqueue(due_at=10.0)
        second = await self._enqueue(due_at=100.0)
        self.assertEqual(first, second)
        claimed = await self.queue.claim_due(10.0, limit=10, lease_seconds=30.0)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].id, first)
        self.assertEqual(claimed[0].due_at, 10.0)

    async def test_ordered_claims_are_exclusive_across_concurrent_claimers(self):
        for chat_id, due_at in ((1, 20.0), (2, 10.0), (3, 30.0)):
            await self._enqueue(chat_id, due_at)
        first, second = await asyncio.gather(
            self.queue.claim_due(30.0, limit=2, lease_seconds=15.0),
            NotificationQueue(self.db).claim_due(30.0, limit=2, lease_seconds=15.0),
        )
        self.assertEqual(len(first) + len(second), 3)
        self.assertEqual({job.chat_id for job in first + second}, {1, 2, 3})
        self.assertEqual([job.due_at for job in first], sorted(job.due_at for job in first))
        self.assertEqual([job.due_at for job in second], sorted(job.due_at for job in second))

    async def test_separate_connections_cannot_claim_same_job(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "queue.db")
            first_db, second_db = Database(path), Database(path)
            await first_db.connect()
            await second_db.connect()
            try:
                first_queue, second_queue = NotificationQueue(first_db), NotificationQueue(second_db)
                await first_queue.enqueue("go_live", 1, "alpha", "s1", 1,
                                          due_at=10.0, now=1.0)
                first, second = await asyncio.gather(
                    first_queue.claim_due(10.0, limit=1, lease_seconds=10.0),
                    second_queue.claim_due(10.0, limit=1, lease_seconds=10.0),
                )
                self.assertEqual(len(first) + len(second), 1)
            finally:
                await first_db.close()
                await second_db.close()

    async def test_expired_lease_reclaims_after_restart_and_fences_old_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "queue.db")
            original = Database(path)
            await original.connect()
            try:
                queue = NotificationQueue(original)
                job_id = await queue.enqueue("go_live", 1, "alpha", "s1", 1,
                                             due_at=10.0, now=1.0)
                first = (await queue.claim_due(10.0, limit=1, lease_seconds=5.0))[0]
            finally:
                await original.close()
            reopened = Database(path)
            await reopened.connect()
            try:
                queue = NotificationQueue(reopened)
                self.assertEqual(await queue.claim_due(14.9, limit=1, lease_seconds=5.0), [])
                second = (await queue.claim_due(15.0, limit=1, lease_seconds=5.0))[0]
                self.assertEqual(second.id, job_id)
                self.assertEqual(second.attempt_count, 2)
                self.assertFalse(await queue.ack(job_id, first.attempt_count, now=15.1))
                self.assertTrue(await queue.ack(job_id, second.attempt_count, now=15.2))
                self.assertEqual(await queue.claim_due(100.0, limit=1, lease_seconds=5.0), [])
            finally:
                await reopened.close()

    async def test_defer_retry_and_terminal_failure(self):
        job_id = await self._enqueue()
        first = (await self.queue.claim_due(10.0, limit=1, lease_seconds=5.0))[0]
        self.assertTrue(await self.queue.defer(job_id, first.attempt_count, due_at=30.0,
                                               error_class="TelegramRetryAfter", now=11.0))
        self.assertEqual(await self.queue.claim_due(29.0, limit=1, lease_seconds=5.0), [])
        second = (await self.queue.claim_due(30.0, limit=1, lease_seconds=5.0))[0]
        self.assertEqual(second.attempt_count, 2)
        self.assertFalse(await self.queue.fail(job_id, first.attempt_count,
                                               error_class="stale", now=30.1))
        self.assertTrue(await self.queue.fail(job_id, second.attempt_count,
                                              error_class="TelegramForbiddenError", now=30.2))
        self.assertEqual(await self.queue.claim_due(100.0, limit=1, lease_seconds=5.0), [])
        snapshot = await self.queue.depth_snapshot(100.0)
        self.assertEqual(snapshot["failed_jobs"], 1)

    async def test_depth_metrics_expose_counts_and_age_without_payload(self):
        await self._enqueue(chat_id=1, due_at=10.0)
        await self._enqueue(chat_id=2, due_at=40.0)
        snapshot = await self.queue.depth_snapshot(20.0)
        self.assertEqual(snapshot["pending_jobs"], 2)
        self.assertEqual(snapshot["due_jobs"], 1)
        self.assertEqual(snapshot["oldest_due_age_seconds"], 10.0)
        self.assertNotIn("chat_id", snapshot)
        self.assertNotIn("twitch_login", snapshot)
        health = await self.db.health_snapshot(20.0)
        self.assertEqual(health["pending_jobs"], 2)
        self.assertEqual(health["due_jobs"], 1)

    async def test_depth_snapshot_uses_queue_indexes_instead_of_scanning_done_jobs(self):
        statements = []
        await self.db.conn.set_trace_callback(statements.append)
        await self.queue.depth_snapshot(20.0)
        await self.db.conn.set_trace_callback(None)
        queue_queries = [sql for sql in statements if "FROM notification_jobs" in sql]
        self.assertTrue(queue_queries)
        for sql in queue_queries:
            cursor = await self.db.conn.execute("EXPLAIN QUERY PLAN " + sql)
            details = " ".join(str(row[3]) for row in await cursor.fetchall())
            self.assertNotIn("SCAN notification_jobs", details)

    async def test_expired_lease_is_visible_in_oldest_due_age(self):
        await self._enqueue(due_at=10.0)
        await self.queue.claim_due(10.0, limit=1, lease_seconds=5.0)
        snapshot = await self.queue.depth_snapshot(20.0)
        self.assertEqual(snapshot["due_jobs"], 1)
        self.assertEqual(snapshot["oldest_due_age_seconds"], 10.0)

    async def test_invalid_error_class_is_not_persisted(self):
        job_id = await self._enqueue()
        claim = (await self.queue.claim_due(10.0, limit=1, lease_seconds=5.0))[0]
        with self.assertRaises(ValueError):
            await self.queue.defer(job_id, claim.attempt_count, due_at=20.0,
                                   error_class="secret /data/bot.db", now=11.0)
        self.assertEqual((await self.queue.depth_snapshot(11.0))["leased_jobs"], 1)

    async def test_invalid_job_identity_is_rejected_without_silent_insert_ignore(self):
        with self.assertRaises(ValueError):
            await self.queue.enqueue("go_live", None, "alpha", "s1", 1,
                                     due_at=10.0, now=1.0)
        self.assertEqual((await self.queue.depth_snapshot(20.0))["pending_jobs"], 0)

    async def test_retention_removes_only_old_terminal_jobs(self):
        for chat_id in (1, 2, 3, 4):
            await self._enqueue(chat_id=chat_id)
        claims = await self.queue.claim_due(10.0, limit=3, lease_seconds=200.0)
        self.assertTrue(await self.queue.ack(claims[0].id, claims[0].attempt_count, now=20.0))
        self.assertTrue(await self.queue.fail(claims[1].id, claims[1].attempt_count,
                                              error_class="TerminalError", now=20.0))
        await self.db.purge_old_report_data(100.0)
        cursor = await self.db.conn.execute(
            "SELECT status, COUNT(*) FROM notification_jobs GROUP BY status"
        )
        self.assertEqual(dict(await cursor.fetchall()), {"leased": 1, "pending": 1})


if __name__ == "__main__":
    unittest.main()
