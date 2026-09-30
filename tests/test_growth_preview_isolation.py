"""A blocked preview provider must not hold the poll or normal delivery path."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationWorker
from bot.poller import StreamPoller
from bot.preview_runtime import PreviewManager, PreviewObservation
from bot.twitch import StreamInfo
from tests.test_preview_runtime import ControlledProvider, ManualClock


class GrowthPreviewIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_capture_does_not_block_poll_or_queue_worker(self):
        db = Database(":memory:")
        await db.connect()
        self.addAsyncCleanup(db.close)
        await db.add_channel(1, "alpha")
        await db.set_preview_enabled(1, "alpha", True)
        await db.set_live_state(
            1, "alpha", True, "s1", 701, "Live", last_seen_live_at=1000.0
        )
        await db.add_channel(2, "beta")

        clock = ManualClock()
        provider = ControlledProvider(clock)
        provider.create_gate = asyncio.Event()
        preview = PreviewManager(
            db, SimpleNamespace(), provider, enabled=True,
            initial_delay_seconds=0, interval_seconds=300,
            max_concurrent_jobs=1, job_timeout_seconds=30,
            poll_interval_seconds=60, build_content=lambda *_: None,
            clock=clock, sleep=clock.sleep,
        )
        preview.start()
        try:
            preview.observe_cycle((PreviewObservation(
                twitch_login="alpha", online=True,
                physical_stream_id="s1", title="Live", game_name="Game",
                viewer_count=42, twitch_started_at="2026-01-01T00:00:00Z",
            ),))
            await asyncio.wait_for(provider.create_entered.wait(), 3.0)

            streams = {
                login: StreamInfo(
                    login, "s1", "Live", "Game", 42,
                    "2026-01-01T00:00:00Z",
                ) for login in ("alpha", "beta")
            }
            twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value=streams))
            poller = StreamPoller(
                SimpleNamespace(), db, twitch, 60,
                preview_observer=preview, notification_queue_enabled=True,
            )
            poller._edit = AsyncMock(return_value=True)
            poller._notify = AsyncMock(return_value=321)
            with patch("bot.poller.time.time", return_value=1000.0):
                await asyncio.wait_for(poller._check_streams(), 3.0)
            worker = NotificationWorker(
                NotificationQueue(db), poller.send_queued_job,
                max_concurrency=1, per_chat_interval=0,
                group_chat_interval=0, global_interval=0,
                clock=lambda: 1000.0,
            )
            await asyncio.wait_for(worker.run_once(), 3.0)
            self.assertEqual((await db.get_live_post_state(2, "beta")).message_id, 321)
            self.assertEqual(provider.active_creates, 1)
        finally:
            provider.create_gate.set()
            await preview.shutdown()


if __name__ == "__main__":
    unittest.main()
