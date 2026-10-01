"""A busy viewer-video roster must not open one capture per live stream."""

import asyncio
import unittest

from bot.database import PreviewDestinationState
from bot.live_post import (
    LivePostContent, LivePostMediaResult, LivePostMediaStatus,
    LocalAnimation, TelegramAnimation,
)
from bot.preview_runtime import PreviewManager, PreviewObservation


class _Db:
    def __init__(self, recipients=1):
        self.recipients = recipients

    async def list_preview_destination_states(self, login):
        return [self._state(login, index) for index in range(self.recipients)]

    def _state(self, login, index):
        return PreviewDestinationState(
            chat_id=101 + index, twitch_login=login, notify_enabled=True,
            preview_enabled=True, is_live=True, logical_stream_id="logical-1",
            message_id=701 + index, message_kind="photo", include_track_link=False,
            last_stream_ended_at=None,
        )

    async def get_preview_destination_state(self, chat_id, login):
        index = chat_id - 101
        return self._state(login, index) if 0 <= index < self.recipients else None


class _Session:
    def __init__(self, provider):
        self.provider = provider

    async def create_artifact(self, _request):
        self.provider.created += 1
        return self.provider.artifact

    async def release_artifact(self, _artifact):
        return None

    async def close(self):
        self.provider.active -= 1


class _Provider:
    def __init__(self, artifact=None):
        self.active = 0
        self.peak = 0
        self.opened = []
        self.created = 0
        self.artifact = artifact

    async def open_session(self, key):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.opened.append(key.twitch_login)
        return _Session(self)


class _Updater:
    def __init__(self):
        self.calls = []

    async def apply_animation(self, *, target, animation, **_kwargs):
        self.calls.append((target.chat_id, animation))
        return LivePostMediaResult(LivePostMediaStatus.APPLIED, "same-bot-file")


class _RateLimitedUpdater(_Updater):
    async def apply_animation(self, *, target, animation, **kwargs):
        self.calls.append((target.chat_id, animation))
        return LivePostMediaResult(LivePostMediaStatus.RETRY_LATER)


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.waiters = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        if delay <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + delay, future))
        try:
            await future
        finally:
            self.waiters = [item for item in self.waiters if item[1] is not future]

    async def advance(self, seconds):
        self.now += seconds
        for due, future in self.waiters:
            if due <= self.now and not future.done():
                future.set_result(None)
        for _ in range(20):
            await asyncio.sleep(0)


class PreviewAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_hundred_viewers_share_one_artifact_and_one_upload(self):
        artifact = LocalAnimation("artifact.mp4", 6)
        provider = _Provider(artifact)
        updater = _Updater()
        manager = PreviewManager(
            _Db(recipients=100), updater, provider, enabled=True,
            initial_delay_seconds=0, interval_seconds=300, max_concurrent_jobs=1,
            max_active_sessions=2, job_timeout_seconds=1, poll_interval_seconds=60,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
        )
        manager.start()
        try:
            manager.observe_cycle((PreviewObservation("alpha", True, "physical-alpha", "Title", "Game", 1, None),))
            for _ in range(300):
                if len(updater.calls) == 100:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(updater.calls), 100)
            self.assertEqual(provider.opened, ["alpha"])
            self.assertEqual(provider.created, 1)
            self.assertEqual(updater.calls[0][1], artifact)
            self.assertTrue(all(
                isinstance(animation, TelegramAnimation) and animation.file_id == "same-bot-file"
                for _, animation in updater.calls[1:]
            ))
        finally:
            await manager.shutdown()

    async def test_five_physical_streams_share_two_capture_slots_fairly(self):
        provider = _Provider(LocalAnimation("artifact.mp4", 6))
        updater = _Updater()
        manager = PreviewManager(
            _Db(), updater, provider, enabled=True, initial_delay_seconds=0,
            interval_seconds=300, max_concurrent_jobs=1, max_active_sessions=2,
            job_timeout_seconds=1, poll_interval_seconds=60,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
        )
        observations = tuple(
            PreviewObservation(login, True, "physical-" + login, "Title", "Game", 1, None)
            for login in ("alpha", "beta", "gamma", "delta", "epsilon")
        )
        manager.start()
        try:
            for expected in (2, 4, 6):
                manager.observe_cycle(observations)
                for _ in range(200):
                    if (
                        provider.created >= expected
                        and len(updater.calls) >= expected
                        and all(record.completed_rounds for record in manager._sessions.values())
                    ):
                        break
                    await asyncio.sleep(0.01)
                self.assertGreaterEqual(provider.created, expected)
                self.assertGreaterEqual(len(updater.calls), expected)
                self.assertLessEqual(manager.health_snapshot()["active_sessions"], 2)
                self.assertEqual(manager.health_snapshot()["deferred_sessions"], 3)
            self.assertLessEqual(provider.peak, 2)
            self.assertEqual(set(provider.opened), {"alpha", "beta", "gamma", "delta", "epsilon"})
        finally:
            await manager.shutdown()
        self.assertEqual(provider.active, 0)

    async def test_default_warmup_is_not_cancelled_by_faster_poll_cycles(self):
        provider = _Provider(LocalAnimation("artifact.mp4", 6))
        updater = _Updater()
        manager = PreviewManager(
            _Db(), updater, provider, enabled=True,
            initial_delay_seconds=0.08, interval_seconds=300,
            max_concurrent_jobs=1, max_active_sessions=2,
            job_timeout_seconds=1, poll_interval_seconds=1,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
        )
        observations = tuple(
            PreviewObservation(login, True, "physical-" + login, "Title", "Game", 1, None)
            for login in ("alpha", "beta", "gamma", "delta", "epsilon")
        )
        manager.start()
        try:
            for _ in range(4):
                manager.observe_cycle(observations)
                await asyncio.sleep(0.02)
            for _ in range(100):
                if provider.created >= 2 and len(updater.calls) >= 2:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(provider.created, 2)
            self.assertGreaterEqual(len(updater.calls), 2)
            self.assertEqual(provider.opened[:2], ["alpha", "beta"])
            self.assertLessEqual(provider.peak, 2)
        finally:
            await manager.shutdown()

    async def test_75_second_warmup_survives_60_second_poll(self):
        clock = _Clock()
        provider = _Provider(LocalAnimation("artifact.mp4", 6))
        updater = _Updater()
        manager = PreviewManager(
            _Db(), updater, provider, enabled=True,
            initial_delay_seconds=75, interval_seconds=300,
            max_concurrent_jobs=1, max_active_sessions=2,
            job_timeout_seconds=120, poll_interval_seconds=60,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
            clock=clock, sleep=clock.sleep,
        )
        observations = tuple(
            PreviewObservation(login, True, "physical-" + login, "Title", "Game", 1, None)
            for login in ("alpha", "beta", "gamma", "delta", "epsilon")
        )
        manager.start()
        try:
            manager.observe_cycle(observations)
            for _ in range(100):
                if len(provider.opened) == 2 and len(clock.waiters) == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(provider.opened, ["alpha", "beta"])
            await clock.advance(60)
            manager.observe_cycle(observations)
            await clock.advance(15)
            for _ in range(100):
                if provider.created == 2 and len(updater.calls) == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(provider.created, 2)
            self.assertEqual(len(updater.calls), 2)
            self.assertEqual(provider.opened, ["alpha", "beta"])
        finally:
            await manager.shutdown()

    async def test_rate_limit_stops_fanout_for_remaining_viewers(self):
        provider = _Provider(LocalAnimation("artifact.mp4", 6))
        updater = _RateLimitedUpdater()
        manager = PreviewManager(
            _Db(recipients=100), updater, provider, enabled=True,
            initial_delay_seconds=0, interval_seconds=300,
            max_concurrent_jobs=1, max_active_sessions=2,
            job_timeout_seconds=1, poll_interval_seconds=60,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
        )
        manager.start()
        try:
            manager.observe_cycle((PreviewObservation("alpha", True, "physical-alpha", "Title", "Game", 1, None),))
            for _ in range(100):
                if manager._sessions and all(record.completed_rounds for record in manager._sessions.values()):
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(len(updater.calls), 1)
        finally:
            await manager.shutdown()


if __name__ == "__main__":
    unittest.main()
