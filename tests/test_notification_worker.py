import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.poller import StreamPoller
from bot.twitch import StreamInfo
from bot.notification_worker import (
    NotificationOutcome,
    NotificationRetryAfter,
    NotificationTerminalError,
    NotificationWorker,
)


class NotificationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.queue = NotificationQueue(self.db)
        self.now = [10.0]

    async def asyncTearDown(self):
        await self.db.close()

    async def _enqueue(self, chat_id=1, version=1):
        return await self.queue.enqueue(
            "go_live", chat_id, "alpha", "s1", version, due_at=10.0, now=1.0
        )

    def _worker(self, send, *, concurrency=2, spacing=0.0, lease=60.0,
                monotonic_clock=None, global_interval=0.0, group_interval=0.0):
        options = {"monotonic_clock": monotonic_clock} if monotonic_clock is not None else {}
        return NotificationWorker(
            self.queue, send, max_concurrency=concurrency,
            per_chat_interval=spacing, lease_seconds=lease,
            send_timeout=5.0, clock=lambda: self.now[0], idle_interval=0.01,
            global_interval=global_interval, group_chat_interval=group_interval,
            **options,
        )

    async def test_slow_send_runs_in_background_without_blocking_caller(self):
        await self._enqueue()
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(_job):
            entered.set()
            await release.wait()
            return NotificationOutcome.SENT

        worker = self._worker(send)
        worker.start()
        try:
            await asyncio.wait_for(entered.wait(), 1.0)
            await self.db.add_channel(2, "beta")
            await self.db.set_notify_enabled(2, "beta", False)
            stream = StreamInfo("beta", "s2", "Live", "Game", 10, "2026-01-01T00:00:00Z")
            twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"beta": stream}))
            poller = StreamPoller(SimpleNamespace(), self.db, twitch, 60)
            await asyncio.wait_for(poller._check_streams(), 1.0)
        finally:
            release.set()
            await worker.stop()
        self.assertEqual((await self.queue.depth_snapshot(10.0))["leased_jobs"], 0)

    async def test_claim_batch_caps_concurrency(self):
        for chat_id in range(1, 6):
            await self._enqueue(chat_id)
        active = peak = sends = 0

        async def send(_job):
            nonlocal active, peak, sends
            active += 1
            sends += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return NotificationOutcome.SENT

        worker = self._worker(send, concurrency=2)
        self.assertEqual(await worker.run_once(), 2)
        self.assertEqual(await worker.run_once(), 2)
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual(await worker.run_once(), 0)
        self.assertEqual(peak, 2)
        self.assertEqual(sends, 5)
        self.assertEqual((await self.queue.depth_snapshot(10.0))["pending_jobs"], 0)

    async def test_same_chat_send_starts_are_spaced(self):
        await self._enqueue(1, 1)
        await self._enqueue(1, 2)
        started = []

        async def send(_job):
            started.append(time.monotonic())
            return NotificationOutcome.SENT

        await self._worker(send, spacing=0.05).run_once()
        self.assertEqual(len(started), 2)
        self.assertGreaterEqual(started[1] - started[0], 0.045)

    async def test_different_chats_obey_global_start_interval(self):
        await self._enqueue(1)
        await self._enqueue(2)
        started = []
        async def send(_job):
            started.append(time.monotonic())
            return NotificationOutcome.SENT
        await self._worker(send, global_interval=0.05).run_once()
        self.assertGreaterEqual(started[1] - started[0], 0.045)

    async def test_global_interval_survives_early_asyncio_wakeup(self):
        await self._enqueue(1)
        await self._enqueue(2)
        clock = [0.0]
        started = []
        real_sleep = asyncio.sleep

        async def early_sleep(delay):
            clock[0] += min(delay, 0.03)
            await real_sleep(0)

        async def send(_job):
            started.append(clock[0])
            return NotificationOutcome.SENT

        worker = self._worker(send, monotonic_clock=lambda: clock[0], global_interval=0.05)
        with patch("bot.notification_worker.asyncio.sleep", side_effect=early_sleep):
            await worker.run_once()
        self.assertEqual(len(started), 2)
        self.assertGreaterEqual(started[1] - started[0], 0.05)

    async def test_group_chat_uses_stricter_interval(self):
        await self._enqueue(-100, 1)
        await self._enqueue(-100, 2)
        started = []
        async def send(_job):
            started.append(time.monotonic())
            return NotificationOutcome.SENT
        await self._worker(send, group_interval=0.05).run_once()
        self.assertGreaterEqual(started[1] - started[0], 0.045)

    async def test_retry_after_moves_due_time_without_sleeping_poll(self):
        await self._enqueue()
        attempts = 0

        async def send(_job):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise NotificationRetryAfter(5.0)
            return NotificationOutcome.SENT

        worker = self._worker(send)
        await worker.run_once()
        self.assertEqual((await self.queue.depth_snapshot(10.0))["pending_jobs"], 1)
        self.now[0] = 14.9
        self.assertEqual(await worker.run_once(), 0)
        self.now[0] = 15.0
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual(attempts, 2)

    async def test_transient_and_terminal_errors_have_distinct_states(self):
        await self._enqueue(1)
        await self._enqueue(2)

        async def send(job):
            if job.chat_id == 1:
                raise ConnectionError("secret /data/bot.db")
            raise NotificationTerminalError("blocked")

        await self._worker(send).run_once()
        cursor = await self.db.conn.execute(
            "SELECT chat_id, status, last_error_class FROM notification_jobs ORDER BY chat_id"
        )
        self.assertEqual(await cursor.fetchall(), [
            (1, "pending", "ConnectionError"),
            (2, "failed", "NotificationTerminalError"),
        ])

    async def test_stale_callback_is_acknowledged_without_send(self):
        await self._enqueue()
        async def stale(_job):
            return NotificationOutcome.STALE
        await self._worker(stale).run_once()
        self.assertEqual((await self.queue.depth_snapshot(10.0))["pending_jobs"], 0)
        self.assertEqual((await self.queue.depth_snapshot(10.0))["leased_jobs"], 0)

    async def test_send_before_ack_crash_can_duplicate_after_lease_expiry(self):
        await self._enqueue()
        sends = 0

        async def send(_job):
            nonlocal sends
            sends += 1
            return NotificationOutcome.SENT

        worker = self._worker(send, lease=12.0)
        original_ack = self.queue.ack
        fail_once = True

        async def ack_with_crash(*args, **kwargs):
            nonlocal fail_once
            if fail_once:
                fail_once = False
                raise RuntimeError("process stopped after external send")
            return await original_ack(*args, **kwargs)

        self.queue.ack = ack_with_crash
        with self.assertRaisesRegex(RuntimeError, "after external send"):
            await worker.run_once()
        self.now[0] = 22.0
        await worker.run_once()
        self.assertEqual(sends, 2)
        self.assertEqual((await self.queue.depth_snapshot(22.0))["leased_jobs"], 0)

    async def test_lease_must_cover_serial_sends_for_same_chat(self):
        async def sent(_job):
            return NotificationOutcome.SENT
        with self.assertRaises(ValueError):
            self._worker(sent, concurrency=2, lease=6.0)

    async def test_idle_chat_timing_state_is_pruned(self):
        await self._enqueue()
        monotonic_now = [100.0]
        async def sent(_job):
            return NotificationOutcome.SENT
        worker = self._worker(sent, monotonic_clock=lambda: monotonic_now[0])
        await worker.run_once()
        self.assertEqual(worker.tracked_chat_count, 1)
        monotonic_now[0] = 200.0
        await worker.run_once()
        self.assertEqual(worker.tracked_chat_count, 0)


if __name__ == "__main__":
    unittest.main()
