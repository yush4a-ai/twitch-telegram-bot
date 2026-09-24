from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.config import ConfigError, load_config
from bot.database import Database
from bot.live_post import (
    LivePostContent,
    LivePostMediaResult,
    LivePostMediaStatus,
    LocalVideo,
    TelegramVideo,
)
from bot.oauth import evaluate_runtime_health
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


def _preview_module():
    try:
        return importlib.import_module("bot.preview_runtime")
    except ModuleNotFoundError as error:
        raise AssertionError("P3 preview runtime module must exist") from error


async def _settle(turns: int = 12) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


async def _wait_until(predicate, *, turns: int = 1000) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("condition was not reached")


class ManualClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.sleeps: list[tuple[float, asyncio.Future]] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        item = (self.now + delay, future)
        self.sleeps.append(item)
        try:
            await future
        finally:
            if item in self.sleeps:
                self.sleeps.remove(item)

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        due = [item for item in self.sleeps if item[0] <= self.now]
        for _deadline, future in due:
            if not future.done():
                future.set_result(None)
        await _settle()


class ControlledSession:
    def __init__(self, provider, key) -> None:
        self.provider = provider
        self.key = key
        self.closed = False

    async def create_artifact(self, request):
        provider = self.provider
        provider.create_calls.append((self.key, request))
        provider.active_creates += 1
        provider.max_active_creates = max(
            provider.max_active_creates, provider.active_creates
        )
        provider.create_entered.set()
        try:
            gate = provider.create_gate_by_login.get(
                self.key.twitch_login, provider.create_gate
            )
            if gate is not None:
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        if not provider.ignore_create_cancellation:
                            raise
            outcome = (
                provider.outcome_by_login[self.key.twitch_login]
                if self.key.twitch_login in provider.outcome_by_login
                else (
                    provider.outcomes.popleft()
                    if provider.outcomes
                    else provider.default_outcome
                )
            )
            if isinstance(outcome, BaseException):
                raise outcome
            if callable(outcome):
                outcome = outcome(request)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
            return outcome
        finally:
            provider.active_creates -= 1

    async def release_artifact(self, artifact) -> None:
        self.provider.release_entered.set()
        gate = self.provider.release_gate_by_login.get(self.key.twitch_login)
        if gate is not None:
            while not gate.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    if not self.provider.ignore_release_cancellation:
                        raise
        if self.provider.reject_release_after_close and self.closed:
            raise RuntimeError("release after close")
        self.provider.released.append(artifact)
        self.provider.cleanup_events.append(
            ("release", self.key.physical_stream_id)
        )

    async def close(self) -> None:
        gate = self.provider.close_gate_by_physical.get(self.key.physical_stream_id)
        if gate is not None:
            while not gate.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    if not self.provider.ignore_close_cancellation:
                        raise
        self.closed = True
        self.provider.closed.append(self.key)
        self.provider.cleanup_events.append(("close", self.key.physical_stream_id))


class ControlledProvider:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.open_calls: list[object] = []
        self.open_times: list[float] = []
        self.sessions: list[ControlledSession] = []
        self.create_calls: list[tuple[object, object]] = []
        self.released: list[object] = []
        self.closed: list[object] = []
        self.outcomes: deque[object] = deque()
        self.default_outcome: object = None
        self.open_error: BaseException | None = None
        self.open_gate: asyncio.Event | None = None
        self.open_gate_by_physical: dict[str, asyncio.Event] = {}
        self.create_gate_by_login: dict[str, asyncio.Event] = {}
        self.release_gate_by_login: dict[str, asyncio.Event] = {}
        self.close_gate_by_physical: dict[str, asyncio.Event] = {}
        self.outcome_by_login: dict[str, object] = {}
        self.ignore_open_cancellation = False
        self.create_gate: asyncio.Event | None = None
        self.ignore_create_cancellation = False
        self.ignore_release_cancellation = False
        self.ignore_close_cancellation = False
        self.reject_release_after_close = False
        self.cleanup_events: list[tuple[str, str]] = []
        self.create_entered = asyncio.Event()
        self.release_entered = asyncio.Event()
        self.active_creates = 0
        self.max_active_creates = 0

    async def open_session(self, key):
        self.open_calls.append(key)
        self.open_times.append(self.clock())
        gate = self.open_gate_by_physical.get(key.physical_stream_id, self.open_gate)
        if gate is not None:
            while not gate.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    if not self.ignore_open_cancellation:
                        raise
        if self.open_error is not None:
            raise self.open_error
        session = ControlledSession(self, key)
        self.sessions.append(session)
        return session


class RecordingUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.results: deque[object] = deque()
        self.default_result = LivePostMediaResult(
            LivePostMediaStatus.APPLIED, "uploaded-file-id"
        )
        self.apply_gate: asyncio.Event | None = None
        self.apply_entered = asyncio.Event()
        self.active_lock_count = 0

    async def apply_video(self, **kwargs):
        content = kwargs["build_content"]()
        if inspect.isawaitable(content):
            content = await content
        kwargs = dict(kwargs)
        kwargs["content"] = content
        self.calls.append(kwargs)
        self.apply_entered.set()
        if self.apply_gate is not None:
            await self.apply_gate.wait()
        current = kwargs["is_current_physical_stream"]()
        if inspect.isawaitable(current):
            current = await current
        if not current:
            return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
        result = self.results.popleft() if self.results else self.default_result
        if isinstance(result, BaseException):
            raise result
        return result


class PreviewRuntimeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.preview = _preview_module()
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "preview.db"))
        await self.db.connect()
        self.clock = ManualClock()
        self.provider = ControlledProvider(self.clock)
        self.updater = RecordingUpdater()
        self.managers: list[object] = []

    async def asyncTearDown(self) -> None:
        for manager in reversed(self.managers):
            await manager.shutdown()
        await self.db.close()
        self.directory.cleanup()

    async def seed(
        self,
        chat_id: int,
        login: str = "channel",
        *,
        preview: bool = True,
        notify: bool = True,
        live: bool = True,
        logical: str | None = "logical-1",
        message_id: int | None = 701,
        kind: str = "text",
    ) -> None:
        await self.db.add_channel(chat_id, login)
        await self.db.set_preview_enabled(chat_id, login, preview)
        await self.db.set_notify_enabled(chat_id, login, notify)
        await self.db.set_live_state(
            chat_id,
            login,
            live,
            logical,
            message_id,
            "Title",
            stream_started_at="2026-01-01T00:00:00Z",
            last_seen_live_at=self.clock(),
            message_kind=kind,
        )

    def observation(
        self,
        login: str = "channel",
        physical: str | None = "physical-A",
        *,
        online: bool = True,
        title: str = "Title",
    ):
        return self.preview.PreviewObservation(
            twitch_login=login,
            online=online,
            physical_stream_id=physical if online else None,
            title=title,
            game_name="Game",
            viewer_count=42,
            twitch_started_at="2026-01-01T00:00:00Z",
        )

    async def build_content(self, observation, destination) -> LivePostContent:
        return LivePostContent(
            html=f"{observation.twitch_login}:{destination.chat_id}:{observation.title}",
            reply_markup=None,
        )

    def manager(
        self,
        *,
        enabled: bool = True,
        initial_delay: float = 0,
        interval: float = 300,
        concurrency: int = 1,
        timeout: float = 1,
        poll_interval: float = 60,
        db=None,
        provider=None,
        updater=None,
    ):
        manager = self.preview.PreviewManager(
            db or self.db,
            updater or self.updater,
            provider or self.provider,
            enabled=enabled,
            initial_delay_seconds=initial_delay,
            interval_seconds=interval,
            max_concurrent_jobs=concurrency,
            job_timeout_seconds=timeout,
            poll_interval_seconds=poll_interval,
            build_content=self.build_content,
            clock=self.clock,
            sleep=self.clock.sleep,
        )
        self.managers.append(manager)
        return manager

    async def start_online(self, manager, *observations) -> None:
        manager.start()
        manager.observe_cycle(observations or (self.observation(),))
        await _settle()

    async def test_global_switch_false_creates_no_tasks_or_provider_sessions(self) -> None:
        await self.seed(101)
        manager = self.manager(enabled=False)

        manager.start()
        manager.observe_cycle((self.observation(),))
        await _settle()

        self.assertEqual(self.provider.open_calls, [])
        health = manager.health_snapshot()
        self.assertFalse(health["enabled"])
        self.assertFalse(health["manager_running"])
        self.assertEqual(health["active_sessions"], 0)
        self.assertEqual(health["active_jobs"], 0)

    async def test_preview_disabled_destination_creates_no_session(self) -> None:
        await self.seed(101, preview=False)
        manager = self.manager()

        await self.start_online(manager)

        self.assertEqual(self.provider.open_calls, [])

    async def test_enabled_destination_starts_one_physical_session(self) -> None:
        await self.seed(101)
        manager = self.manager(initial_delay=75)

        await self.start_online(manager)

        self.assertEqual(len(self.provider.open_calls), 1)
        self.assertEqual(self.provider.open_calls[0].twitch_login, "channel")
        self.assertEqual(self.provider.open_calls[0].physical_stream_id, "physical-A")
        self.assertEqual(manager.health_snapshot()["active_sessions"], 1)

    async def test_five_destinations_share_one_provider_session_and_job(self) -> None:
        for offset in range(5):
            await self.seed(101 + offset, message_id=701 + offset)
        self.provider.default_outcome = LocalVideo(Path("artifact.mp4"))
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 5)

        self.assertEqual(len(self.provider.open_calls), 1)
        self.assertEqual(len(self.provider.create_calls), 1)
        self.assertEqual(len(self.updater.calls), 5)

    async def test_request_is_first_preview_when_message_kind_is_text(self) -> None:
        await self.seed(101, kind="text")
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)

        self.assertTrue(self.provider.create_calls[0][1].is_first_preview)

    async def test_request_is_not_first_preview_when_message_kind_is_video(self) -> None:
        await self.seed(101, logical="physical-A", message_id=701)
        self.assertTrue(
            await self.db.begin_video_transition(101, "channel", "physical-A", 701)
        )
        self.assertTrue(
            await self.db.finish_video_transition(101, "channel", "physical-A", 701)
        )
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)

        self.assertFalse(self.provider.create_calls[0][1].is_first_preview)

    async def test_physical_change_gets_its_own_first_preview_state(self) -> None:
        await self.seed(101, logical="physical-A", message_id=701)
        self.assertTrue(
            await self.db.begin_video_transition(101, "channel", "physical-A", 701)
        )
        self.assertTrue(
            await self.db.finish_video_transition(101, "channel", "physical-A", 701)
        )
        self.provider.default_outcome = None
        manager = self.manager()
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        self.assertFalse(self.provider.create_calls[0][1].is_first_preview)

        # A new physical stream posts a fresh message: last_message_id changes,
        # so the durable message_kind resets to "text" for physical-B.
        await self.db.set_live_state(
            101,
            "channel",
            True,
            "physical-B",
            702,
            "Title",
            stream_started_at="2026-01-01T00:00:00Z",
            last_seen_live_at=self.clock(),
        )
        manager.observe_cycle((self.observation(physical="physical-B"),))
        await _wait_until(lambda: len(self.provider.create_calls) == 2)

        self.assertTrue(self.provider.create_calls[1][1].is_first_preview)

    async def test_disabled_notify_and_missing_message_destinations_are_excluded(self) -> None:
        await self.seed(101, preview=False)
        await self.seed(102, notify=False)
        await self.seed(103, message_id=None)
        await self.seed(104)
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 1)

        self.assertEqual(self.updater.calls[0]["target"].chat_id, 104)

    async def test_offline_destination_state_is_not_eligible(self) -> None:
        await self.seed(101, live=False)
        manager = self.manager()

        await self.start_online(manager)

        self.assertEqual(self.provider.open_calls, [])

    async def test_physical_change_closes_a_and_starts_fresh_b_generation(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = None
        manager = self.manager(initial_delay=75)
        await self.start_online(manager)
        first_session = self.provider.sessions[0]

        manager.observe_cycle((self.observation(physical="physical-B"),))
        await _wait_until(lambda: len(self.provider.sessions) == 2)

        self.assertTrue(first_session.closed)
        self.assertEqual(self.provider.open_calls[1].physical_stream_id, "physical-B")

    async def test_late_artifact_from_a_is_released_and_never_applied_to_b(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.ignore_create_cancellation = True
        self.provider.outcomes.extend([LocalVideo(Path("late-a.mp4")), None])
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        manager.observe_cycle((self.observation(physical="physical-B"),))
        await _settle()
        self.provider.create_gate.set()
        await _settle(1000)
        self.assertEqual(
            len(self.provider.open_calls),
            2,
            msg=(
                f"health={manager.health_snapshot()} "
                f"released={self.provider.released} closed={self.provider.closed}"
            ),
        )

        self.assertEqual(self.updater.calls, [])
        self.assertEqual(self.provider.released, [LocalVideo(Path("late-a.mp4"))])
        self.assertEqual(self.provider.open_calls[-1].physical_stream_id, "physical-B")

    async def test_physical_change_replaces_generation_even_when_logical_id_is_merged(self) -> None:
        await self.seed(101, logical="one-logical-session")
        self.provider.default_outcome = None
        manager = self.manager()
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        first_generation = self.provider.create_calls[0][1].generation

        manager.observe_cycle((self.observation(physical="physical-B"),))
        await _wait_until(lambda: len(self.provider.create_calls) == 2)
        second_generation = self.provider.create_calls[1][1].generation

        state = await self.db.get_live_post_state(101, "channel")
        self.assertEqual(state.logical_stream_id, "one-logical-session")
        self.assertNotEqual(first_generation, second_generation)

    async def test_offline_then_same_physical_id_gets_new_generation(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = None
        manager = self.manager()
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        first = self.provider.create_calls[0][1].generation

        manager.observe_cycle((self.observation(online=False),))
        await _settle()
        await self.db.set_live_state(
            101, "channel", True, "logical-1", 701, "Title", last_seen_live_at=self.clock()
        )
        manager.observe_cycle((self.observation(),))
        await _wait_until(lambda: len(self.provider.create_calls) == 2)

        self.assertNotEqual(first, self.provider.create_calls[1][1].generation)

    async def test_full_warmup_starts_after_successful_provider_open(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = None
        manager = self.manager(initial_delay=75)

        await self.start_online(manager)
        self.assertEqual(self.provider.open_times, [1000.0])
        self.assertEqual(self.provider.create_calls, [])
        await _wait_until(
            lambda: any(deadline == 1075.0 for deadline, _future in self.clock.sleeps)
        )
        await self.clock.advance(74)
        self.assertEqual(self.provider.create_calls, [])
        await self.clock.advance(1)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        self.assertEqual(len(self.provider.create_calls), 1)

    async def test_normal_interval_is_measured_from_attempt_completion(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = None
        manager = self.manager(interval=300)
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        await _settle()

        await self.clock.advance(299)
        self.assertEqual(len(self.provider.create_calls), 1)
        manager.observe_cycle((self.observation(),))
        await _settle()
        await self.clock.advance(1)
        self.assertEqual(len(self.provider.create_calls), 2)

    async def test_long_job_has_no_overlap_or_catch_up_queue(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        manager = self.manager(interval=300)
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        await self.clock.advance(1200)
        self.assertEqual(len(self.provider.create_calls), 1)
        self.assertEqual(self.provider.max_active_creates, 1)
        manager.observe_cycle((self.observation(),))
        await _settle()
        self.provider.create_gate.set()
        await _settle()
        await self.clock.advance(299)
        self.assertEqual(len(self.provider.create_calls), 1)
        manager.observe_cycle((self.observation(),))
        await _settle()
        await self.clock.advance(1)
        self.assertEqual(len(self.provider.create_calls), 2)

    async def test_global_semaphore_limits_artifact_creation_not_fanout(self) -> None:
        await self.seed(101, "alpha", message_id=701)
        await self.seed(102, "beta", message_id=702)
        self.provider.create_gate = asyncio.Event()
        manager = self.manager(concurrency=1)
        await self.start_online(
            manager,
            self.observation("alpha", "physical-alpha"),
            self.observation("beta", "physical-beta"),
        )
        await self.provider.create_entered.wait()
        await _settle()

        self.assertEqual(len(self.provider.create_calls), 1)
        self.assertEqual(self.provider.max_active_creates, 1)
        self.provider.create_gate.set()
        await _wait_until(lambda: len(self.provider.create_calls) == 2)
        self.assertEqual(self.provider.max_active_creates, 1)

    async def test_provider_failure_is_local_and_uses_exponential_backoff(self) -> None:
        await self.seed(101)
        self.provider.outcomes.extend(
            [RuntimeError("provider-secret-one"), RuntimeError("provider-secret-two"), None]
        )
        manager = self.manager(interval=300)
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        await _wait_until(
            lambda: manager.health_snapshot()["consecutive_provider_failures"] == 1
        )

        self.assertEqual(manager.health_snapshot()["consecutive_provider_failures"], 1)
        await self.clock.advance(59)
        self.assertEqual(len(self.provider.create_calls), 1)
        manager.observe_cycle((self.observation(),))
        await _settle()
        await self.clock.advance(1)
        self.assertEqual(len(self.provider.create_calls), 2)
        await _wait_until(
            lambda: manager.health_snapshot()["consecutive_provider_failures"] == 2
        )
        await self.clock.advance(119)
        self.assertEqual(len(self.provider.create_calls), 2)
        manager.observe_cycle((self.observation(),))
        await _settle()
        await self.clock.advance(1)
        await _wait_until(lambda: len(self.provider.create_calls) == 3)
        await _wait_until(
            lambda: manager.health_snapshot()["consecutive_provider_failures"] == 0
        )
        self.assertEqual(len(self.provider.create_calls), 3)
        self.assertEqual(manager.health_snapshot()["consecutive_provider_failures"], 0)
        self.assertNotIn("provider-secret", str(manager.health_snapshot()))

    async def test_provider_failure_health_keeps_max_for_still_failing_session(self) -> None:
        await self.seed(101, "alpha", message_id=701)
        await self.seed(102, "beta", message_id=702)
        self.provider.outcome_by_login.update(
            {"alpha": RuntimeError("alpha-secret"), "beta": None}
        )
        manager = self.manager(concurrency=1)

        await self.start_online(
            manager,
            self.observation("alpha", "physical-alpha"),
            self.observation("beta", "physical-beta"),
        )
        await _wait_until(lambda: len(self.provider.create_calls) == 2)
        await _settle(30)

        self.assertEqual(
            manager.health_snapshot()["consecutive_provider_failures"], 1
        )
        self.assertNotIn("alpha-secret", str(manager.health_snapshot()))

    async def test_backoff_caps_at_preview_interval(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = RuntimeError("broken")
        manager = self.manager(interval=200)
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)
        await _wait_until(
            lambda: manager.health_snapshot()["consecutive_provider_failures"] == 1
        )

        for expected_delay, expected_calls in ((60, 2), (120, 3), (200, 4), (200, 5)):
            await self.clock.advance(expected_delay - 1)
            manager.observe_cycle((self.observation(),))
            await _settle()
            await self.clock.advance(1)
            self.assertEqual(len(self.provider.create_calls), expected_calls)
            await _wait_until(
                lambda expected=expected_calls: (
                    manager.health_snapshot()["consecutive_provider_failures"]
                    == expected
                )
            )

    async def test_none_artifact_is_normal_completion_not_failure(self) -> None:
        await self.seed(101)
        self.provider.default_outcome = None
        manager = self.manager(interval=300)
        await self.start_online(manager)
        await _wait_until(lambda: len(self.provider.create_calls) == 1)

        self.assertEqual(manager.health_snapshot()["consecutive_provider_failures"], 0)
        await self.clock.advance(299)
        self.assertEqual(len(self.provider.create_calls), 1)

    async def test_hung_provider_times_out_and_releases_global_slot(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        manager = self.manager(timeout=0.01)
        await self.start_online(manager)
        await asyncio.sleep(0.2)

        self.assertEqual(self.provider.active_creates, 0)
        self.assertEqual(manager.health_snapshot()["active_jobs"], 0)
        self.assertEqual(manager.health_snapshot()["consecutive_provider_failures"], 1)

    async def test_cancellation_resistant_create_releases_slot_and_late_artifact(self) -> None:
        await self.seed(101, "alpha", message_id=701)
        await self.seed(102, "beta", message_id=702)
        alpha_gate = asyncio.Event()
        self.provider.create_gate_by_login["alpha"] = alpha_gate
        self.provider.ignore_create_cancellation = True
        late_artifact = LocalVideo(Path("late-alpha.mp4"))
        self.provider.outcome_by_login.update(
            {
                "alpha": lambda _request: (
                    late_artifact
                    if sum(
                        key.twitch_login == "alpha"
                        for key, _request in self.provider.create_calls
                    )
                    == 1
                    else None
                ),
                "beta": None,
            }
        )
        manager = self.manager(timeout=0.01, concurrency=1)
        manager.start()
        manager.observe_cycle((self.observation("alpha", "physical-alpha"),))
        await self.provider.create_entered.wait()

        manager.observe_cycle(
            (
                self.observation("alpha", "physical-alpha"),
                self.observation("beta", "physical-beta"),
            )
        )
        await asyncio.sleep(0.2)
        await self.clock.advance(60)
        alpha_creates = lambda: sum(
            key.twitch_login == "alpha" for key, _request in self.provider.create_calls
        )
        try:
            self.assertTrue(
                any(key.twitch_login == "beta" for key, _request in self.provider.create_calls),
                "timed-out cancellation-resistant create must release the global slot",
            )
            self.assertEqual(
                alpha_creates(),
                1,
                "one session must not start a replacement while its late operation lives",
            )
        finally:
            alpha_gate.set()
            await _settle(30)

        await _wait_until(lambda: late_artifact in self.provider.released)
        self.assertFalse(
            any(call["target"].twitch_login == "alpha" for call in self.updater.calls)
        )

    async def test_cancellation_resistant_open_times_out_and_closes_late_session(self) -> None:
        await self.seed(101)
        open_gate = asyncio.Event()
        self.provider.open_gate = open_gate
        self.provider.ignore_open_cancellation = True
        manager = self.manager(timeout=0.01)
        manager.start()
        manager.observe_cycle((self.observation(),))

        await asyncio.sleep(0.04)
        try:
            self.assertEqual(
                manager.health_snapshot()["consecutive_provider_failures"], 1
            )
        finally:
            open_gate.set()
            await _settle(30)

        await _wait_until(lambda: len(self.provider.closed) == 1)
        self.assertEqual(self.provider.create_calls, [])

    async def test_shutdown_drains_late_artifact_before_closing_its_session(self) -> None:
        await self.seed(101)
        create_gate = asyncio.Event()
        self.provider.create_gate = create_gate
        self.provider.ignore_create_cancellation = True
        self.provider.reject_release_after_close = True
        artifact = LocalVideo(Path("shutdown-late.mp4"))
        self.provider.default_outcome = artifact
        manager = self.manager(timeout=0.05)
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        async def finish_during_shutdown() -> None:
            await asyncio.sleep(0.01)
            create_gate.set()

        finisher = asyncio.create_task(finish_during_shutdown())
        await manager.shutdown()
        await finisher

        self.assertEqual(self.provider.released, [artifact])
        self.assertEqual(
            self.provider.cleanup_events,
            [("release", "physical-A"), ("close", "physical-A")],
        )
        self.assertEqual(len(self.provider.closed), 1)
        self.assertEqual(manager._cleanup_tasks, set())

    async def test_shutdown_waits_for_inflight_release_before_session_close(self) -> None:
        await self.seed(101)
        release_gate = asyncio.Event()
        self.provider.release_gate_by_login["channel"] = release_gate
        self.provider.ignore_release_cancellation = True
        self.provider.reject_release_after_close = True
        artifact = LocalVideo(Path("inflight-release.mp4"))
        self.provider.default_outcome = artifact
        manager = self.manager(timeout=0.05)
        await self.start_online(manager)
        await self.provider.release_entered.wait()

        async def finish_during_shutdown() -> None:
            await asyncio.sleep(0.01)
            release_gate.set()

        finisher = asyncio.create_task(finish_during_shutdown())
        await manager.shutdown()
        await finisher

        self.assertEqual(self.provider.released, [artifact])
        self.assertEqual(
            self.provider.cleanup_events,
            [("release", "physical-A"), ("close", "physical-A")],
        )
        self.assertEqual(len(self.provider.closed), 1)

    async def test_physical_change_does_not_wait_for_cancellation_resistant_open(self) -> None:
        await self.seed(101)
        open_a_gate = asyncio.Event()
        self.provider.open_gate_by_physical["physical-A"] = open_a_gate
        self.provider.ignore_open_cancellation = True
        manager = self.manager(timeout=1)
        manager.start()
        manager.observe_cycle((self.observation(physical="physical-A"),))
        await _wait_until(lambda: len(self.provider.open_calls) == 1)

        manager.observe_cycle((self.observation(physical="physical-B"),))
        await asyncio.sleep(0.04)
        try:
            self.assertTrue(
                any(
                    key.physical_stream_id == "physical-B"
                    for key in self.provider.open_calls
                ),
                "physical B must not wait for cancellation-resistant open of A",
            )
        finally:
            open_a_gate.set()
            await _settle(30)

        await _wait_until(
            lambda: any(
                key.physical_stream_id == "physical-A"
                for key in self.provider.closed
            )
        )

    async def test_shutdown_is_bounded_when_provider_close_suppresses_cancellation(self) -> None:
        await self.seed(101)
        close_gate = asyncio.Event()
        self.provider.close_gate_by_physical["physical-A"] = close_gate
        self.provider.ignore_close_cancellation = True
        manager = self.manager(initial_delay=75, timeout=0.01)
        await self.start_online(manager)
        self.assertEqual(len(self.provider.sessions), 1)

        shutdown_task = asyncio.create_task(manager.shutdown())
        try:
            done, _pending = await asyncio.wait({shutdown_task}, timeout=0.1)
            completed = bool(done)
        finally:
            close_gate.set()
            if not shutdown_task.done():
                shutdown_task.cancel()
            await asyncio.gather(shutdown_task, return_exceptions=True)
            await _settle(30)

        self.assertTrue(completed, "PreviewManager.shutdown() must be bounded")
        health = manager.health_snapshot()
        self.assertEqual(health["consumer_tasks"], 0)
        self.assertEqual(health["session_tasks"], 0)
        self.assertEqual(health["job_tasks"], 0)
        self.assertEqual(health["active_sessions"], 0)

    async def test_offline_cancels_job_closes_session_and_releases_artifact(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = LocalVideo(Path("cancelled.mp4"))
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        manager.observe_cycle((self.observation(online=False),))
        await _settle(30)

        self.assertEqual(manager.health_snapshot()["active_sessions"], 0)
        self.assertEqual(manager.health_snapshot()["active_jobs"], 0)
        self.assertEqual(len(self.provider.closed), 1)
        self.assertEqual(self.updater.calls, [])

    async def test_zero_eligible_destinations_stops_existing_session(self) -> None:
        await self.seed(101)
        manager = self.manager(initial_delay=75)
        await self.start_online(manager)
        await self.db.set_preview_enabled(101, "channel", False)

        manager.observe_cycle((self.observation(),))
        await _settle(30)

        self.assertEqual(manager.health_snapshot()["active_sessions"], 0)
        self.assertEqual(len(self.provider.closed), 1)

    async def test_one_disabled_destination_does_not_stop_shared_session(self) -> None:
        await self.seed(101, message_id=701)
        await self.seed(102, message_id=702)
        manager = self.manager(initial_delay=75)
        await self.start_online(manager)
        await self.db.set_preview_enabled(101, "channel", False)

        manager.observe_cycle((self.observation(),))
        await _settle()

        self.assertEqual(manager.health_snapshot()["active_sessions"], 1)
        self.assertEqual(self.provider.closed, [])

    async def test_destination_joining_running_job_waits_for_next_artifact(self) -> None:
        await self.seed(101, message_id=701)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager(interval=300)
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        await self.seed(102, message_id=702)
        manager.observe_cycle((self.observation(),))
        self.provider.create_gate.set()
        await _wait_until(lambda: len(self.updater.calls) == 1)
        self.assertEqual([call["target"].chat_id for call in self.updater.calls], [101])

        await self.clock.advance(299)
        manager.observe_cycle((self.observation(),))
        await _settle()
        await self.clock.advance(1)
        await _wait_until(lambda: len(self.updater.calls) == 3)
        self.assertEqual(
            [call["target"].chat_id for call in self.updater.calls[1:]], [101, 102]
        )

    async def test_midstream_enable_without_session_starts_full_warmup(self) -> None:
        await self.seed(101, preview=False)
        manager = self.manager(initial_delay=75)
        await self.start_online(manager)
        await self.db.set_preview_enabled(101, "channel", True)

        manager.observe_cycle((self.observation(),))
        await _wait_until(lambda: len(self.provider.open_calls) == 1)
        await _wait_until(lambda: len(self.clock.sleeps) == 1)
        await self.clock.advance(74)
        self.assertEqual(self.provider.create_calls, [])
        await self.clock.advance(1)
        self.assertEqual(len(self.provider.create_calls), 1)

    async def test_first_local_upload_file_id_is_reused_for_remaining_destinations(self) -> None:
        for chat_id in (101, 102, 103):
            await self.seed(chat_id, message_id=600 + chat_id)
        artifact = LocalVideo(Path("artifact.mp4"), "preview.mp4")
        self.provider.default_outcome = artifact
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 3)
        await _wait_until(lambda: self.provider.released == [artifact])

        self.assertEqual(self.updater.calls[0]["video"], artifact)
        self.assertEqual(
            [call["video"] for call in self.updater.calls[1:]],
            [TelegramVideo("uploaded-file-id"), TelegramVideo("uploaded-file-id")],
        )
        self.assertEqual(self.provider.released, [artifact])

    async def test_failed_first_target_makes_second_retry_local_upload(self) -> None:
        await self.seed(101, message_id=701)
        await self.seed(102, message_id=702)
        artifact = LocalVideo(Path("artifact.mp4"))
        self.provider.default_outcome = artifact
        self.updater.results.extend(
            [
                LivePostMediaResult(LivePostMediaStatus.RETRY_LATER),
                LivePostMediaResult(LivePostMediaStatus.APPLIED, "second-file"),
            ]
        )
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 2)

        self.assertEqual(self.updater.calls[0]["video"], artifact)
        self.assertEqual(self.updater.calls[1]["video"], artifact)

    async def test_applied_without_file_id_keeps_local_input_for_next_target(self) -> None:
        await self.seed(101, message_id=701)
        await self.seed(102, message_id=702)
        artifact = LocalVideo(Path("artifact.mp4"))
        self.provider.default_outcome = artifact
        self.updater.results.extend(
            [
                LivePostMediaResult(LivePostMediaStatus.APPLIED),
                LivePostMediaResult(LivePostMediaStatus.APPLIED, "later-file"),
            ]
        )
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 2)

        self.assertEqual(
            [call["video"] for call in self.updater.calls], [artifact, artifact]
        )

    async def test_provider_telegram_file_id_is_reused_for_every_target(self) -> None:
        await self.seed(101, message_id=701)
        await self.seed(102, message_id=702)
        artifact = TelegramVideo("provider-file")
        self.provider.default_outcome = artifact
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 2)

        self.assertEqual(
            [call["video"] for call in self.updater.calls], [artifact, artifact]
        )

    async def test_one_telegram_exception_does_not_block_remaining_fanout(self) -> None:
        await self.seed(101, message_id=701)
        await self.seed(102, message_id=702)
        self.provider.default_outcome = TelegramVideo("provider-file")
        self.updater.results.extend(
            [RuntimeError("telegram-sensitive-text"), self.updater.default_result]
        )
        manager = self.manager()

        await self.start_online(manager)
        await _wait_until(lambda: len(self.updater.calls) == 2)

        self.assertEqual([call["target"].chat_id for call in self.updater.calls], [101, 102])
        self.assertTrue(manager.health_snapshot()["manager_running"])
        self.assertNotIn("telegram-sensitive", str(manager.health_snapshot()))

    async def test_replaced_message_revision_is_skipped_before_apply(self) -> None:
        await self.seed(101, message_id=701)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()
        await self.db.set_live_state(101, "channel", True, "logical-1", 702, "Replacement")

        self.provider.create_gate.set()
        await _settle(30)

        self.assertEqual(self.updater.calls, [])

    async def test_removed_destination_is_skipped_before_apply(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()
        await self.db.remove_channel(101, "channel")

        self.provider.create_gate.set()
        await _settle(30)

        self.assertEqual(self.updater.calls, [])

    async def test_preview_toggle_during_job_is_respected(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()
        await self.db.set_preview_enabled(101, "channel", False)

        self.provider.create_gate.set()
        await _settle(30)

        self.assertEqual(self.updater.calls, [])

    async def test_notify_toggle_during_job_is_respected_by_manager(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()
        await self.db.set_notify_enabled(101, "channel", False)

        self.provider.create_gate.set()
        await _settle(30)

        self.assertEqual(self.updater.calls, [])

    async def test_new_destination_during_job_is_not_in_frozen_participants(self) -> None:
        await self.seed(101, message_id=701)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()
        await self.seed(102, message_id=702)

        self.provider.create_gate.set()
        await _wait_until(lambda: len(self.updater.calls) == 1)

        self.assertEqual(self.updater.calls[0]["target"].chat_id, 101)

    async def test_same_physical_stream_uses_latest_observation_for_fanout_content(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = TelegramVideo("provider-file")
        manager = self.manager()
        manager.start()
        manager.observe_cycle((self.observation(title="Old title"),))
        await self.provider.create_entered.wait()

        manager.observe_cycle((self.observation(title="Fresh title"),))
        await _settle()
        self.provider.create_gate.set()
        await _wait_until(lambda: len(self.updater.calls) == 1)

        self.assertEqual(
            self.updater.calls[0]["content"].html,
            "channel:101:Fresh title",
        )

    async def test_latest_state_coalesces_intermediate_observations(self) -> None:
        await self.seed(101)
        manager = self.manager(initial_delay=75)
        manager.start()

        manager.observe_cycle((self.observation(physical="physical-A"),))
        manager.observe_cycle((self.observation(physical="physical-B"),))
        manager.observe_cycle((self.observation(physical="physical-C"),))
        await _settle()

        self.assertEqual(len(self.provider.open_calls), 1)
        self.assertEqual(self.provider.open_calls[0].physical_stream_id, "physical-C")

    async def test_full_batch_prunes_untracked_login_and_bounds_registry(self) -> None:
        await self.seed(101)
        manager = self.manager(initial_delay=75)
        await self.start_online(manager)
        self.assertEqual(manager.health_snapshot()["active_sessions"], 1)

        manager.observe_cycle(())
        await _settle(30)

        self.assertEqual(manager.health_snapshot()["active_sessions"], 0)
        self.assertEqual(len(self.provider.closed), 1)

    async def test_observation_lease_blocks_media_after_poll_outage(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        self.provider.default_outcome = LocalVideo(Path("stale.mp4"))
        manager = self.manager(poll_interval=10)
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        await self.clock.advance(21)
        self.provider.create_gate.set()
        await _settle(30)

        self.assertEqual(self.updater.calls, [])
        self.assertEqual(self.provider.released, [LocalVideo(Path("stale.mp4"))])

    async def test_restart_has_empty_runtime_and_requires_new_observation_and_warmup(self) -> None:
        await self.seed(101)
        first = self.manager(initial_delay=75)
        await self.start_online(first)
        await first.shutdown()

        second = self.manager(initial_delay=75)
        second.start()
        await _settle()
        self.assertEqual(second.health_snapshot()["active_sessions"], 0)
        second.observe_cycle((self.observation(),))
        await _wait_until(lambda: len(self.provider.open_calls) == 2)
        self.assertEqual(len(self.provider.create_calls), 0)

    async def test_shutdown_leaves_no_preview_tasks_sessions_registries_or_locks(self) -> None:
        await self.seed(101)
        self.provider.create_gate = asyncio.Event()
        manager = self.manager()
        await self.start_online(manager)
        await self.provider.create_entered.wait()

        await manager.shutdown()

        health = manager.health_snapshot()
        self.assertFalse(health["manager_running"])
        self.assertEqual(health["active_sessions"], 0)
        self.assertEqual(health["active_jobs"], 0)
        self.assertEqual(health["consumer_tasks"], 0)
        self.assertEqual(health["session_tasks"], 0)
        self.assertEqual(health["job_tasks"], 0)
        self.assertEqual(self.updater.active_lock_count, 0)


class PreviewDatabaseProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _preview_module()
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "projection.db"))
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.directory.cleanup()

    async def test_named_destination_projection_contains_all_eligibility_and_content_fields(self) -> None:
        await self.db.add_channel(-100101, "channel")
        await self.db.register_telegram_channel(-100101, "Public")
        await self.db.set_notify_enabled(-100101, "channel", False)
        await self.db.set_preview_enabled(-100101, "channel", True)
        await self.db.set_live_state(
            -100101,
            "channel",
            True,
            "logical-1",
            701,
            "Title",
            stream_started_at="2026-01-01T00:00:00Z",
            message_kind="video",
        )
        self.assertTrue(
            await self.db.begin_video_transition(
                -100101, "channel", "logical-1", 701
            )
        )
        self.assertTrue(
            await self.db.finish_video_transition(
                -100101, "channel", "logical-1", 701
            )
        )

        states = await self.db.list_preview_destination_states("channel")

        self.assertEqual(len(states), 1)
        state = states[0]
        self.assertEqual(state.chat_id, -100101)
        self.assertEqual(state.twitch_login, "channel")
        self.assertFalse(state.notify_enabled)
        self.assertTrue(state.preview_enabled)
        self.assertTrue(state.is_live)
        self.assertEqual(state.logical_stream_id, "logical-1")
        self.assertEqual(state.message_id, 701)
        self.assertEqual(state.message_kind, "video")
        self.assertTrue(state.include_track_link)
        self.assertTrue(hasattr(state, "last_stream_ended_at"))

    async def test_fresh_single_destination_read_returns_none_after_removal(self) -> None:
        await self.db.add_channel(101, "channel")
        await self.db.set_preview_enabled(101, "channel", True)
        await self.db.remove_channel(101, "channel")

        state = await self.db.get_preview_destination_state(101, "channel")

        self.assertIsNone(state)


class PreviewPollerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.preview = _preview_module()
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "poller.db"))
        await self.db.connect()
        await self.db.add_channel(101, "channel")
        self.telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=701)),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(),
            delete_message=AsyncMock(),
        )
        self.stream = StreamInfo(
            user_login="channel",
            stream_id="physical-A",
            title="Title",
            game_name="Game",
            viewer_count=42,
            started_at="2026-01-01T00:00:00Z",
        )
        self.twitch = SimpleNamespace(
            get_live_streams=AsyncMock(return_value={"channel": self.stream})
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.directory.cleanup()

    async def test_online_send_and_durable_commit_precede_observation(self) -> None:
        order: list[str] = []
        original_set_live_state = self.db.set_live_state

        async def recorded_set_live_state(*args, **kwargs):
            result = await original_set_live_state(*args, **kwargs)
            order.append("commit")
            return result

        self.db.set_live_state = recorded_set_live_state

        class Observer:
            def __init__(inner_self):
                inner_self.observations = None

            def observe_cycle(inner_self, observations):
                self.assertIsNotNone(self.telegram.send_message.await_args)
                order.append("observe")
                inner_self.observations = tuple(observations)

        observer = Observer()
        poller = StreamPoller(
            self.telegram, self.db, self.twitch, 60, preview_observer=observer
        )
        poller._maybe_snapshot_followers = AsyncMock()

        with patch("bot.poller.time.time", return_value=1000.0):
            await poller._check_streams()

        self.assertEqual(order[-2:], ["commit", "observe"])
        self.assertEqual(observer.observations, (
            self.preview.PreviewObservation(
                twitch_login="channel",
                online=True,
                physical_stream_id="physical-A",
                title="Title",
                game_name="Game",
                viewer_count=42,
                twitch_started_at="2026-01-01T00:00:00Z",
            ),
        ))
        state = await self.db.get_live_post_state(101, "channel")
        self.assertEqual(state.message_id, 701)

    async def test_observer_exception_does_not_fail_successful_poll_cycle(self) -> None:
        class BrokenObserver:
            def observe_cycle(self, _observations):
                raise RuntimeError("preview observer exploded")

        poller = StreamPoller(
            self.telegram, self.db, self.twitch, 60, preview_observer=BrokenObserver()
        )
        poller._maybe_snapshot_followers = AsyncMock()

        with patch("bot.poller.time.time", return_value=1000.0):
            await poller._check_streams()

        state = await self.db.get_live_post_state(101, "channel")
        self.assertTrue(state is not None and state.message_id == 701)

    async def test_failed_twitch_stage_does_not_publish_false_offline(self) -> None:
        observations: list[tuple] = []
        observer = SimpleNamespace(observe_cycle=lambda batch: observations.append(tuple(batch)))
        self.twitch.get_live_streams.side_effect = TimeoutError("twitch unavailable")
        poller = StreamPoller(
            self.telegram, self.db, self.twitch, 60, preview_observer=observer
        )

        with self.assertRaises(TimeoutError):
            await poller._check_streams()

        self.assertEqual(observations, [])

    async def test_registered_telegram_channel_gets_new_layout_end_to_end(self) -> None:
        await self.db.add_channel(-100501, "channel")
        await self.db.register_telegram_channel(-100501, "Public Channel")
        poller = StreamPoller(self.telegram, self.db, self.twitch, 60)
        poller._maybe_snapshot_followers = AsyncMock()

        await poller._check_streams()

        calls = [
            call
            for call in self.telegram.send_message.await_args_list
            if call.args[0] == -100501
        ]
        self.assertEqual(len(calls), 1)
        text = calls[0].args[1]
        self.assertTrue(text.startswith("🔴 Стрим «"))
        self.assertNotIn("🔴 channel", text)

    async def test_unregistered_private_chat_uses_private_layout_end_to_end(self) -> None:
        # Positive Telegram chat ids represent private chats. Channel registration
        # is intentionally absent, so the compact private layout is used.
        poller = StreamPoller(self.telegram, self.db, self.twitch, 60)
        poller._maybe_snapshot_followers = AsyncMock()

        await poller._check_streams()

        calls = [
            call
            for call in self.telegram.send_message.await_args_list
            if call.args[0] == 101
        ]
        self.assertEqual(len(calls), 1)
        text = calls[0].args[1]
        self.assertTrue(text.startswith("<b>channel</b> «<a href="))
        self.assertIn("в эфире", text)

    async def test_empty_authoritative_cycle_prunes_previous_login(self) -> None:
        batches: list[tuple] = []
        observer = SimpleNamespace(observe_cycle=lambda batch: batches.append(tuple(batch)))
        await self.db.remove_channel(101, "channel")
        poller = StreamPoller(
            self.telegram, self.db, self.twitch, 60, preview_observer=observer
        )

        await poller._check_streams()

        self.assertEqual(batches, [()])
        self.twitch.get_live_streams.assert_not_awaited()

    async def test_preview_content_preserves_live_html_deep_link_and_single_button(self) -> None:
        await self.db.register_telegram_channel(101, "Public")
        await self.db.set_display_name("channel", "Channel & Friends")
        poller = StreamPoller(self.telegram, self.db, self.twitch, 60)
        destination_type = getattr(importlib.import_module("bot.database"), "PreviewDestinationState")
        destination = destination_type(
            chat_id=101,
            twitch_login="channel",
            notify_enabled=True,
            preview_enabled=True,
            is_live=True,
            logical_stream_id="logical-1",
            message_id=701,
            message_kind="text",
            include_track_link=True,
            last_stream_ended_at=None,
        )
        observation = self.preview.PreviewObservation(
            twitch_login="channel",
            online=True,
            physical_stream_id="physical-A",
            title="Co-op with @friend",
            game_name="Game & More",
            viewer_count=42,
            twitch_started_at="2026-01-01T00:00:00Z",
        )

        content = await poller.build_preview_content(observation, destination)

        self.assertEqual(
            content.html,
            "🔴 Стрим «<a href=\"https://www.twitch.tv/channel\">Co-op with</a>» уже идёт!"
            "\n🤝 Вместе с: <a href=\"https://www.twitch.tv/friend\">friend</a>\n\n"
            "🎮 <b>Категория:</b> Game &amp; More\n👥 Зрителей: 42\n\n"
            "🔔 <a href=\"https://t.me/twitchSignalBot?start=track_channel\">"
            "Подключить уведомления</a>",
        )
        self.assertEqual(len(content.reply_markup.inline_keyboard), 1)
        self.assertEqual(len(content.reply_markup.inline_keyboard[0]), 1)
        button = content.reply_markup.inline_keyboard[0][0]
        self.assertEqual((button.text, button.url), (
            "Смотреть на Twitch", "https://twitch.tv/channel"
        ))


class ChannelLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.preview = _preview_module()
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "channel-layout.db"))
        await self.db.connect()
        self.telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=701)),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(),
            delete_message=AsyncMock(),
        )
        self.twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={}))
        self.poller = StreamPoller(self.telegram, self.db, self.twitch, 60)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.directory.cleanup()

    def _destination(self, chat_id: int, *, include_track_link: bool):
        destination_type = getattr(
            importlib.import_module("bot.database"), "PreviewDestinationState"
        )
        return destination_type(
            chat_id=chat_id,
            twitch_login="paverpapa",
            notify_enabled=True,
            preview_enabled=True,
            is_live=True,
            logical_stream_id="logical-1",
            message_id=701,
            message_kind="text",
            include_track_link=include_track_link,
            last_stream_ended_at=None,
        )

    def _observation(self, *, title: str = "Разбор эфира", viewer_count: int = 977):
        return self.preview.PreviewObservation(
            twitch_login="paverpapa",
            online=True,
            physical_stream_id="physical-A",
            title=title,
            game_name="Just Chatting",
            viewer_count=viewer_count,
            twitch_started_at="2026-01-01T00:00:00Z",
        )

    async def test_channel_body_has_no_standalone_streamer_row(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        self.assertNotIn("🔴 paverpapa", content.html)
        self.assertNotIn(">paverpapa<", content.html)

    async def test_channel_first_row_wraps_clickable_title(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        first_line = content.html.split("\n", 1)[0]
        self.assertTrue(first_line.startswith("🔴 Стрим «"))
        self.assertTrue(first_line.endswith("» уже идёт!"))

    async def test_channel_title_is_clickable_to_correct_twitch_url(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        self.assertIn(
            '<a href="https://www.twitch.tv/paverpapa">Разбор эфира</a>', content.html
        )

    async def test_channel_title_html_escaped(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(title="<script>alert(1)</script> & со скидкой"),
            self._destination(-100201, include_track_link=True),
        )
        self.assertNotIn("<script>", content.html)
        self.assertIn("&lt;script&gt;", content.html)
        self.assertIn("&amp;", content.html)

    async def test_channel_category_line_format(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        self.assertIn("🎮 <b>Категория:</b> Just Chatting", content.html)

    async def test_channel_viewers_line_format_without_emoji_digits(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(viewer_count=977),
            self._destination(-100201, include_track_link=True),
        )
        self.assertIn("👥 Зрителей: 977", content.html)
        self.assertNotIn("⃣", content.html)  # combining keycap marker (emoji digits)

    async def test_channel_notification_link_preserved(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        self.assertIn("🔔 <a href=", content.html)
        self.assertIn("Подключить уведомления</a>", content.html)

    async def test_channel_has_no_separate_textual_twitch_url_outside_anchor(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        # The only occurrence of the Twitch URL must be inside the title anchor's href.
        self.assertEqual(content.html.count("twitch.tv/paverpapa"), 1)

    async def test_channel_keyboard_keeps_single_twitch_button(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100201, include_track_link=True)
        )
        self.assertEqual(len(content.reply_markup.inline_keyboard), 1)
        button = content.reply_markup.inline_keyboard[0][0]
        self.assertEqual(button.text, "Смотреть на Twitch")
        self.assertEqual(button.url, "https://twitch.tv/paverpapa")

    async def test_channel_video_caption_uses_same_layout_as_text(self) -> None:
        destination = self._destination(-100201, include_track_link=True)
        content = await self.poller.build_preview_content(self._observation(), destination)
        self.assertTrue(content.html.startswith("🔴 Стрим «"))
        self.assertIn("🎮 <b>Категория:</b>", content.html)
        self.assertIn("👥 Зрителей:", content.html)

    async def test_channel_layout_stable_across_viewer_and_title_updates(self) -> None:
        destination = self._destination(-100201, include_track_link=True)
        first = await self.poller.build_preview_content(
            self._observation(title="Первый", viewer_count=10), destination
        )
        second = await self.poller.build_preview_content(
            self._observation(title="Второй заголовок", viewer_count=999), destination
        )
        for content in (first, second):
            self.assertTrue(content.html.startswith("🔴 Стрим «"))
            self.assertIn("🎮 <b>Категория:</b>", content.html)
        self.assertIn("👥 Зрителей: 10", first.html)
        self.assertIn("👥 Зрителей: 999", second.html)

    async def test_long_title_respects_caption_limit(self) -> None:
        from bot.live_post import CAPTION_UTF16_LIMIT

        long_title = "Очень длинное название стрима " * 30
        content = await self.poller.build_preview_content(
            self._observation(title=long_title),
            self._destination(-100201, include_track_link=True),
        )
        length = len(content.html.encode("utf-16-le")) // 2
        self.assertLessEqual(length, CAPTION_UTF16_LIMIT)

    async def test_title_with_heavy_html_escaping_still_respects_caption_limit(self) -> None:
        from bot.live_post import CAPTION_UTF16_LIMIT

        # "&" expands 5x via HTML-escaping ("&amp;"); this must not blow the budget.
        adversarial_title = "&" * 400
        content = await self.poller.build_preview_content(
            self._observation(title=adversarial_title),
            self._destination(-100201, include_track_link=True),
        )
        length = len(content.html.encode("utf-16-le")) // 2
        self.assertLessEqual(length, CAPTION_UTF16_LIMIT)
        opens = content.html.count("<a ")
        closes = content.html.count("</a>")
        self.assertEqual(opens, closes)

    async def test_long_title_truncation_keeps_html_well_formed(self) -> None:
        long_title = "Очень длинное название стрима " * 30
        content = await self.poller.build_preview_content(
            self._observation(title=long_title),
            self._destination(-100201, include_track_link=True),
        )
        self.assertIn('<a href="https://www.twitch.tv/paverpapa">', content.html)
        opens = content.html.count("<a ")
        closes = content.html.count("</a>")
        self.assertEqual(opens, closes)
        self.assertIn("</a>» уже идёт!", content.html)

    async def test_private_chat_uses_compact_layout(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(555555, include_track_link=False)
        )
        self.assertNotIn("Стрим «", content.html)
        self.assertIn("👁 977 зрителей", content.html)
        self.assertIn("🎮 Just Chatting", content.html)
        self.assertNotIn("🔔 <a href=", content.html)

    async def test_group_chat_layout_unchanged(self) -> None:
        content = await self.poller.build_preview_content(
            self._observation(), self._destination(-100999, include_track_link=False)
        )
        self.assertNotIn("Стрим «", content.html)
        self.assertIn("👁 Сейчас смотрят:", content.html)
        self.assertIn("🎮 Just Chatting", content.html)
        self.assertNotIn("🔔 <a href=", content.html)


class LivePostNotifyRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_disabled_during_content_boundary_blocks_media_call(self) -> None:
        _preview_module()
        directory = tempfile.TemporaryDirectory()
        db = Database(os.path.join(directory.name, "notify-race.db"))
        await db.connect()
        try:
            await db.add_channel(101, "channel")
            await db.set_preview_enabled(101, "channel", True)
            await db.set_live_state(101, "channel", True, "logical-1", 701, "Title")
            bot = SimpleNamespace(edit_message_media=AsyncMock())
            updater_type = importlib.import_module("bot.live_post").LivePostUpdater
            target_type = importlib.import_module("bot.live_post").LivePostTarget

            async def disable_notify_during_content_build():
                await db.set_notify_enabled(101, "channel", False)
                return LivePostContent("fresh", None)

            result = await updater_type(bot, db).apply_video(
                target=target_type(101, "channel", "logical-1", 701),
                video=TelegramVideo("file-id"),
                is_current_physical_stream=lambda: True,
                build_content=disable_notify_during_content_build,
            )

            self.assertEqual(result.status, LivePostMediaStatus.SKIPPED_DISABLED)
            bot.edit_message_media.assert_not_awaited()
            state = await db.get_live_post_state(101, "channel")
            self.assertEqual(state.message_id, 701)
        finally:
            await db.close()
            directory.cleanup()


class PreviewManagerSupervisionAndHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_crash_disables_only_preview_and_core_task_keeps_running(self) -> None:
        preview = _preview_module()
        core_stop = asyncio.Event()
        core_task = asyncio.create_task(core_stop.wait())
        broken_db = SimpleNamespace(
            list_preview_destination_states=AsyncMock(
                side_effect=RuntimeError("sqlite-sensitive-detail")
            )
        )
        clock = ManualClock()
        manager = preview.PreviewManager(
            broken_db,
            RecordingUpdater(),
            ControlledProvider(clock),
            enabled=True,
            initial_delay_seconds=0,
            interval_seconds=300,
            max_concurrent_jobs=1,
            job_timeout_seconds=1,
            poll_interval_seconds=60,
            build_content=AsyncMock(return_value=LivePostContent("content", None)),
            clock=clock,
            sleep=clock.sleep,
        )
        try:
            manager.start()
            manager.observe_cycle((preview.PreviewObservation(
                "channel", True, "physical-A", "Title", "Game", 42,
                "2026-01-01T00:00:00Z",
            ),))
            await _wait_until(
                lambda: manager.health_snapshot()["disabled_reason"] == "manager_crash"
            )

            self.assertFalse(core_task.done())
            health = manager.health_snapshot()
            self.assertFalse(health["enabled"])
            self.assertFalse(health["manager_running"])
            self.assertNotIn("sqlite-sensitive", str(health))
            manager.observe_cycle(())
        finally:
            core_stop.set()
            await core_task
            await manager.shutdown()

    async def test_preview_health_is_diagnostic_sanitized_and_contains_no_ids_or_paths(self) -> None:
        preview = _preview_module()
        clock = ManualClock()
        manager = preview.PreviewManager(
            SimpleNamespace(), RecordingUpdater(), ControlledProvider(clock),
            enabled=False,
            disabled_reason="config_error",
            initial_delay_seconds=75,
            interval_seconds=300,
            max_concurrent_jobs=1,
            job_timeout_seconds=120,
            poll_interval_seconds=60,
            build_content=AsyncMock(),
            clock=clock,
            sleep=clock.sleep,
        )

        health = manager.health_snapshot()

        self.assertEqual(
            set(health),
            {
                "enabled", "manager_running", "active_sessions", "active_jobs",
                "consumer_tasks", "session_tasks", "job_tasks",
                "latest_observation_age_seconds", "last_success_age_seconds",
                "last_error", "consecutive_provider_failures", "disabled_reason",
            },
        )
        rendered = str(health)
        self.assertNotIn("chat_id", rendered)
        self.assertNotIn("twitch_login", rendered)
        self.assertNotIn("\\", rendered)

    def test_preview_failure_does_not_change_core_healthz_result(self) -> None:
        now = 1000.0
        poller = {
            "running": True,
            "stopping": False,
            "last_successful_cycle_at": 999.0,
            "last_successful_cycle_age_seconds": 1.0,
            "last_cycle_error": None,
            "uptime_seconds": 20.0,
            "stale_after_seconds": 180.0,
            "preview": {
                "enabled": False,
                "manager_running": False,
                "disabled_reason": "manager_crash",
            },
        }
        eventsub = {
            "running": True,
            "stopping": False,
            "configured_logins": 1,
            "ready_logins": 1,
            "last_error": None,
        }

        healthy, reason = evaluate_runtime_health(poller, eventsub, now)

        self.assertTrue(healthy)
        self.assertEqual(reason, "ok")


class PreviewConfigTests(unittest.TestCase):
    BASE_ENV = {
        "TELEGRAM_BOT_TOKEN": "123:token",
        "TWITCH_CLIENT_ID": "client",
        "TWITCH_CLIENT_SECRET": "secret",
        "PUBLIC_URL": "http://localhost:8765",
    }

    def test_defaults_keep_production_preview_disabled_with_safe_values(self) -> None:
        _preview_module()
        with patch.dict(os.environ, self.BASE_ENV, clear=True):
            config = load_config()

        self.assertFalse(config.preview.enabled)
        self.assertEqual(config.preview.initial_delay_seconds, 75)
        self.assertEqual(config.preview.interval_seconds, 300)
        self.assertEqual(config.preview.max_concurrent_jobs, 1)
        self.assertEqual(config.preview.job_timeout_seconds, 120)
        self.assertIsNone(config.preview.disabled_reason)

    def test_invalid_preview_boolean_disables_only_preview_and_sanitizes_warning(self) -> None:
        _preview_module()
        env = dict(self.BASE_ENV, PREVIEW_RUNTIME_ENABLED="do-not-log-this-value")
        with patch.dict(os.environ, env, clear=True), self.assertLogs("bot.config", "WARNING") as logs:
            config = load_config()

        self.assertEqual(config.telegram_bot_token, "123:token")
        self.assertFalse(config.preview.enabled)
        self.assertEqual(config.preview.disabled_reason, "config_error")
        self.assertNotIn("do-not-log-this-value", "\n".join(logs.output))

    def test_invalid_preview_timing_uses_defaults_and_does_not_break_core_config(self) -> None:
        _preview_module()
        env = dict(
            self.BASE_ENV,
            PREVIEW_RUNTIME_ENABLED="true",
            PREVIEW_INTERVAL_SECONDS="0",
            PREVIEW_JOB_TIMEOUT_SECONDS="not-a-number",
        )
        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        self.assertFalse(config.preview.enabled)
        self.assertEqual(config.preview.disabled_reason, "config_error")
        self.assertEqual(config.preview.interval_seconds, 300)
        self.assertEqual(config.preview.job_timeout_seconds, 120)

    def test_invalid_preview_concurrency_disables_only_preview(self) -> None:
        _preview_module()
        env = dict(
            self.BASE_ENV,
            PREVIEW_RUNTIME_ENABLED="true",
            PREVIEW_MAX_CONCURRENT_JOBS="-2",
        )
        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        self.assertFalse(config.preview.enabled)
        self.assertEqual(config.preview.max_concurrent_jobs, 1)
        self.assertEqual(config.preview.disabled_reason, "config_error")

    def test_core_config_validation_remains_fatal(self) -> None:
        _preview_module()
        env = dict(
            self.BASE_ENV,
            POLL_INTERVAL_SECONDS="0",
            PREVIEW_RUNTIME_ENABLED="false",
        )
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ConfigError):
                load_config()


if __name__ == "__main__":
    unittest.main()


class ProviderDiagnosticLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_logs_allowlisted_phase_without_message(self) -> None:
        preview = _preview_module()

        class PhaseError(RuntimeError):
            phase = "analysis"

        async def no_sleep(_delay: float) -> None:
            return None

        manager = preview.PreviewManager(
            object(), object(), object(), enabled=True,
            initial_delay_seconds=0, interval_seconds=300,
            max_concurrent_jobs=1, job_timeout_seconds=1,
            poll_interval_seconds=60, build_content=lambda *_args: None,
            sleep=no_sleep,
        )

        key = preview.PreviewSessionKey("channel", "physical-A")
        token = preview.PreviewGeneration("channel", "physical-A", 1)
        record = preview._ManagedSession(key, token)
        manager._sessions["channel"] = record

        with self.assertLogs("bot.preview_runtime", level="WARNING") as captured:
            await manager._provider_failed(
                record, PhaseError("https://secret.example/token")
            )

        rendered = "\n".join(captured.output)
        self.assertIn("phase=analysis", rendered)
        self.assertNotIn("secret.example", rendered)
        self.assertEqual(manager.health_snapshot()["last_error"], "PhaseError")
