import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.config import ConfigError, load_config
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationRetryAfter, NotificationWorker
from bot.poller import StreamPoller
from bot.twitch import StreamInfo
import main as application


class NotificationCutoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.add_channel(1, "alpha")
        self.stream = StreamInfo("alpha", "s1", "Live", "Game", 42, "2026-01-01T00:00:00Z")
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": self.stream}))
        self.poller = StreamPoller(
            SimpleNamespace(), self.db, twitch, 60,
            notification_queue_enabled=True,
        )
        self.poller._notify = AsyncMock(return_value=321)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_go_live_is_queued_without_telegram_send_and_repeat_poll_is_idempotent(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        self.poller._notify.assert_not_awaited()
        state = await self.db.get_live_state(1, "alpha")
        self.assertTrue(state[0])
        self.assertEqual(state[1], "s1")
        self.assertIsNone(state[2])
        self.assertEqual((await NotificationQueue(self.db).depth_snapshot(1000.0))["pending_jobs"], 1)
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        self.poller._notify.assert_not_awaited()
        self.assertEqual((await NotificationQueue(self.db).depth_snapshot(1060.0))["pending_jobs"], 1)

    async def test_poll_persists_verified_broadcaster_and_clears_it_for_unknown_new_stream(self):
        self.stream.broadcaster_id = "11"
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        cursor = await self.db.conn.execute(
            "SELECT last_broadcaster_id FROM tracked_channels WHERE chat_id = 1 AND twitch_login = 'alpha'"
        )
        self.assertEqual((await cursor.fetchone())[0], "11")
        self.stream.stream_id = "s2"
        self.stream.broadcaster_id = None
        with patch("bot.poller.time.time", return_value=2000.0):
            await self.poller._check_streams()
        cursor = await self.db.conn.execute(
            "SELECT last_broadcaster_id FROM tracked_channels WHERE chat_id = 1 AND twitch_login = 'alpha'"
        )
        self.assertIsNone((await cursor.fetchone())[0])

    async def test_worker_send_sets_message_only_for_current_stream(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(1000.0, limit=1, lease_seconds=60.0))[0]
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.poller._notify.assert_awaited_once()
        self.assertTrue(self.poller._notify.await_args.kwargs["direct"])
        self.assertEqual((await self.db.get_live_post_state(1, "alpha")).message_id, 321)

    async def test_worker_checks_channel_membership_by_chat_id_without_full_scan(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        job = (await NotificationQueue(self.db).claim_due(
            1000.0, limit=1, lease_seconds=60.0
        ))[0]
        self.db.is_telegram_channel = AsyncMock(return_value=False)
        self.db.telegram_channel_ids = AsyncMock(
            side_effect=AssertionError("worker must not scan all Telegram channels")
        )
        self.db.list_live_channels = AsyncMock(wraps=self.db.list_live_channels)
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.SENT)
        self.db.is_telegram_channel.assert_awaited_once_with(1)
        self.db.list_live_channels.assert_awaited_once_with(1, twitch_login="alpha")

    async def test_worker_waits_for_first_sample_before_sending_post(self):
        await self.db.set_live_state(
            1, "alpha", True, "s1", title="Live", last_seen_live_at=1000.0,
            queued_go_live=True,
        )
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(1000.0, limit=1, lease_seconds=60.0))[0]
        with self.assertRaises(RuntimeError):
            await self.poller.send_queued_job(job)
        self.poller._notify.assert_not_awaited()

    async def test_unsubscribed_or_replaced_stream_is_stale_before_send(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(1000.0, limit=1, lease_seconds=60.0))[0]
        await self.db.set_notify_enabled(1, "alpha", False)
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.poller._notify.assert_not_awaited()
        await self.db.set_notify_enabled(1, "alpha", True)
        await self.db.set_live_state(1, "alpha", True, "s2", title="Next")
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.poller._notify.assert_not_awaited()

    async def test_state_change_during_send_removes_stale_telegram_post(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(1000.0, limit=1, lease_seconds=60.0))[0]
        self.poller._bot.delete_message = AsyncMock()

        async def send_then_unsubscribe(*_args, **_kwargs):
            await self.db.set_notify_enabled(1, "alpha", False)
            return 321

        self.poller._notify.side_effect = send_then_unsubscribe
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        self.poller._bot.delete_message.assert_awaited_once_with(1, 321)

    async def test_queued_telegram_retry_after_is_deferred(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        self.poller._notify.side_effect = NotificationRetryAfter(7.0)
        worker = NotificationWorker(
            NotificationQueue(self.db), self.poller.send_queued_job,
            max_concurrency=1, per_chat_interval=0.0,
            clock=lambda: 1000.0,
        )
        await worker.run_once()
        cursor = await self.db.conn.execute(
            "SELECT status, due_at, last_error_class FROM notification_jobs"
        )
        self.assertEqual(await cursor.fetchone(),
                         ("pending", 1007.0, "NotificationRetryAfter"))

    async def test_reenabled_notification_can_send_after_stale_job_was_acked(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        queue = NotificationQueue(self.db)
        job = (await queue.claim_due(1000.0, limit=1, lease_seconds=60.0))[0]
        await self.db.set_notify_enabled(1, "alpha", False)
        self.assertEqual(await self.poller.send_queued_job(job), NotificationOutcome.STALE)
        await queue.ack(job.id, job.attempt_count, now=1001.0)
        await self.db.set_notify_enabled(1, "alpha", True)
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        self.poller._notify.assert_awaited_once()
        self.assertTrue(self.poller._notify.await_args.kwargs["silent"])
        self.assertEqual((await self.db.get_live_post_state(1, "alpha")).message_id, 321)

    async def test_waiting_jobs_are_loaded_once_per_poll_not_per_destination(self):
        await self.db.add_channel(2, "alpha")
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        statements = []
        await self.db.conn.set_trace_callback(statements.append)
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        await self.db.conn.set_trace_callback(None)
        queue_lookups = [sql for sql in statements
                         if "FROM notification_jobs" in sql and "kind = 'go_live'" in sql]
        self.assertLessEqual(len(queue_lookups), 1)
        cursor = await self.db.conn.execute("EXPLAIN QUERY PLAN " + queue_lookups[0])
        details = " ".join(str(row[3]) for row in await cursor.fetchall())
        self.assertIn("idx_notification_jobs_active_go_live", details)

    async def test_queue_insert_failure_rolls_back_live_transition(self):
        await self.db.conn.execute(
            "CREATE TRIGGER fail_go_live BEFORE INSERT ON notification_jobs "
            "BEGIN SELECT RAISE(ABORT, 'injected enqueue failure'); END"
        )
        await self.db.conn.commit()
        with self.assertRaisesRegex(Exception, "injected enqueue failure"):
            await self.db.set_live_state(
                1, "alpha", True, "s1", title="Live", last_seen_live_at=1000.0,
                queued_go_live=True,
            )
        state = await self.db.get_live_state(1, "alpha")
        self.assertFalse(state[0])
        self.assertIsNone(state[1])

    async def test_next_poll_does_not_erase_worker_message_saved_after_snapshot(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        real_snapshot_jobs = self.db.snapshot_queued_live_starts

        async def worker_finishes_during_poll():
            waiting = await real_snapshot_jobs()
            await self.db.set_live_message_if_current(1, "alpha", "s1", 321)
            return waiting

        self.db.snapshot_queued_live_starts = worker_finishes_during_poll
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        self.assertEqual((await self.db.get_live_post_state(1, "alpha")).message_id, 321)

    async def test_completed_job_between_snapshots_does_not_send_resume_duplicate(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()

        async def worker_completes_before_queue_snapshot():
            await self.db.set_live_message_if_current(1, "alpha", "s1", 321)
            return set()

        self.db.snapshot_queued_live_starts = worker_completes_before_queue_snapshot
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        self.poller._notify.assert_not_awaited()
        self.assertEqual((await self.db.get_live_post_state(1, "alpha")).message_id, 321)

    async def test_offline_transition_preserves_concurrent_worker_message_for_cleanup(self):
        with patch("bot.poller.time.time", return_value=1000.0):
            await self.poller._check_streams()
        self.poller._twitch.get_live_streams.return_value = {}
        real_set_state = self.db.set_live_state

        async def worker_finishes_before_offline_write(*args, **kwargs):
            if not args[2]:
                await self.db.set_live_message_if_current(1, "alpha", "s1", 321)
            return await real_set_state(*args, **kwargs)

        self.db.set_live_state = worker_finishes_before_offline_write
        with patch("bot.poller.time.time", return_value=1060.0):
            await self.poller._check_streams()
        state = await self.db.get_live_state(1, "alpha")
        self.assertFalse(state[0])
        self.assertEqual(state[2], 321)


class NotificationFlagTests(unittest.TestCase):
    def test_flag_is_rejected_outside_railway_staging(self):
        railway = {
            "TELEGRAM_BOT_TOKEN": "123:test",
            "TWITCH_CLIENT_ID": "client",
            "TWITCH_CLIENT_SECRET": "secret",
            "TOKEN_ENCRYPTION_KEY": "test-key",
            "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
            "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
            "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "RAILWAY_PUBLIC_DOMAIN": "test.example.up.railway.app",
            "DB_PATH": "/data/bot.db",
            "NOTIFICATION_QUEUE_ENABLED": "1",
        }
        with patch.dict(os.environ, {**railway, "RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            with self.assertRaises(ConfigError):
                load_config()
        with patch.dict(os.environ, {**railway, "RAILWAY_ENVIRONMENT_NAME": "staging"}, clear=True):
            self.assertTrue(load_config().notification_queue_enabled)
        with patch.dict(
            os.environ,
            {**railway, "RAILWAY_ENVIRONMENT_NAME": "staging",
             "RAILWAY_ENVIRONMENT_ID": "af6d873b-a2cf-45aa-be42-cd9efbd102a7"},
            clear=True,
        ):
            with self.assertRaises(ConfigError):
                load_config()

    def test_runtime_worker_factory_is_off_by_default(self):
        poller = SimpleNamespace(send_queued_job=AsyncMock())
        self.assertIsNone(application._make_notification_worker(
            SimpleNamespace(notification_queue_enabled=False), SimpleNamespace(), poller
        ))
        worker = application._make_notification_worker(
            SimpleNamespace(notification_queue_enabled=True), SimpleNamespace(), poller
        )
        self.assertEqual(worker._max_concurrency, 4)


if __name__ == "__main__":
    unittest.main()
