from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Protocol, Sequence, TypeAlias

from .database import Database, PreviewDestinationState
from .live_post import (
    LivePostContent,
    LivePostMediaStatus,
    LivePostTarget,
    LivePostUpdater,
    LocalVideo,
    TelegramVideo,
)


logger = logging.getLogger(__name__)

_PROVIDER_FAILURE_PHASES = frozenset({
    "source_open", "source_invalid_identity",
    "source_resolve_invalid_login", "source_resolve_capability_unavailable",
    "source_resolve_timeout", "source_resolve_process_failed",
    "source_resolve_malformed_output", "source_resolve_internal_error",
    "source_capture_clean_eof", "source_capture_process_exit",
    "source_capture_stalled", "source_capture_invalid_input",
    "source_capture_disk_pressure", "source_capture_permission_denied",
    "source_capture_start_failed", "source_capture_shutdown",
    "source_capture_capability_unavailable", "source_capture_internal_error",
    "source_capture_capacity", "capture_state", "snapshot", "analysis",
    "render", "artifact", "request", "session_close",
    "capture_recovery", "provider",
})

PreviewArtifact: TypeAlias = LocalVideo | TelegramVideo


@dataclass(frozen=True)
class PreviewObservation:
    twitch_login: str
    online: bool
    physical_stream_id: str | None
    title: str
    game_name: str | None
    viewer_count: int
    twitch_started_at: str | None

    def __post_init__(self) -> None:
        if self.online and not self.physical_stream_id:
            raise ValueError("online preview observation requires physical_stream_id")


@dataclass(frozen=True)
class PreviewSessionKey:
    twitch_login: str
    physical_stream_id: str


@dataclass(frozen=True)
class PreviewGeneration:
    twitch_login: str
    physical_stream_id: str
    generation: int


@dataclass(frozen=True)
class PreviewArtifactRequest:
    generation: PreviewGeneration
    observation: PreviewObservation
    is_first_preview: bool = False


class PreviewSessionState(Enum):
    WARMING = "warming"
    WAITING = "waiting"
    RUNNING = "running"
    BACKOFF = "backoff"
    STOPPED = "stopped"


class PreviewArtifactSession(Protocol):
    async def create_artifact(
        self, request: PreviewArtifactRequest
    ) -> PreviewArtifact | None: ...

    async def release_artifact(self, artifact: PreviewArtifact) -> None: ...

    async def close(self) -> None: ...


class PreviewArtifactProvider(Protocol):
    async def open_session(self, key: PreviewSessionKey) -> PreviewArtifactSession: ...


class PreviewObserver(Protocol):
    def observe_cycle(self, observations: Sequence[PreviewObservation]) -> None: ...


class _NoopPreviewArtifactSession:
    async def create_artifact(
        self, request: PreviewArtifactRequest
    ) -> PreviewArtifact | None:
        return None

    async def release_artifact(self, artifact: PreviewArtifact) -> None:
        return None

    async def close(self) -> None:
        return None


class NoopPreviewArtifactProvider:
    async def open_session(self, key: PreviewSessionKey) -> PreviewArtifactSession:
        return _NoopPreviewArtifactSession()


class DisabledPreviewObserver:
    def __init__(self, disabled_reason: str = "startup_error") -> None:
        self._disabled_reason = disabled_reason

    def observe_cycle(self, observations: Sequence[PreviewObservation]) -> None:
        return None

    def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def health_snapshot(self, now: float | None = None) -> dict[str, object]:
        return {
            "enabled": False,
            "manager_running": False,
            "active_sessions": 0,
            "active_jobs": 0,
            "consumer_tasks": 0,
            "session_tasks": 0,
            "job_tasks": 0,
            "latest_observation_age_seconds": None,
            "last_success_age_seconds": None,
            "last_error": None,
            "consecutive_provider_failures": 0,
            "disabled_reason": self._disabled_reason,
        }


@dataclass(frozen=True)
class _Observed:
    value: PreviewObservation
    observed_at: float


@dataclass
class _ManagedSession:
    key: PreviewSessionKey
    token: PreviewGeneration
    state: PreviewSessionState = PreviewSessionState.WARMING
    task: asyncio.Task | None = None
    job_task: asyncio.Task | None = None
    provider_session: PreviewArtifactSession | None = None
    provider_operation: asyncio.Task | None = None
    late_cleanup_task: asyncio.Task | None = None
    close_deferred: bool = False
    close_started: bool = False
    stopping: bool = False
    consecutive_failures: int = 0


ContentBuilder: TypeAlias = Callable[
    [PreviewObservation, PreviewDestinationState],
    LivePostContent | Awaitable[LivePostContent],
]
Clock: TypeAlias = Callable[[], float]
Sleeper: TypeAlias = Callable[[float], Awaitable[None]]


class PreviewManager:
    """Fail-open coordinator for one preview pipeline per physical Twitch stream."""

    def __init__(
        self,
        db: Database,
        live_post_updater: LivePostUpdater,
        provider: PreviewArtifactProvider,
        *,
        enabled: bool,
        initial_delay_seconds: float,
        interval_seconds: float,
        max_concurrent_jobs: int,
        job_timeout_seconds: float,
        poll_interval_seconds: float,
        build_content: ContentBuilder,
        disabled_reason: str | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._db = db
        self._live_post_updater = live_post_updater
        self._provider = provider
        self._enabled = bool(enabled)
        self._running = False
        self._accepting = False
        self._initial_delay = max(0.0, float(initial_delay_seconds))
        self._interval = max(0.0, float(interval_seconds))
        self._job_timeout = max(0.001, float(job_timeout_seconds))
        self._cleanup_timeout = min(1.0, self._job_timeout)
        self._lease_seconds = max(0.0, float(poll_interval_seconds) * 2)
        self._build_content = build_content
        self._clock = clock
        self._sleep = sleep
        self._artifact_semaphore = asyncio.Semaphore(max(1, int(max_concurrent_jobs)))
        self._disabled_reason = (
            disabled_reason
            if disabled_reason is not None
            else (None if self._enabled else "config_disabled")
        )

        self._latest: dict[str, _Observed] = {}
        self._revision = 0
        self._observation_event = asyncio.Event()
        self._generation_by_login: dict[str, int] = {}
        self._sessions: dict[str, _ManagedSession] = {}
        self._consumer_task: asyncio.Task | None = None
        self._session_tasks: set[asyncio.Task] = set()
        self._job_tasks: set[asyncio.Task] = set()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._late_provider_tasks: set[asyncio.Task] = set()
        self._latest_observation_at: float | None = None
        self._last_success_at: float | None = None
        self._last_error: str | None = None
        self._consecutive_provider_failures = 0
        self._shutdown_lock = asyncio.Lock()

    def start(self) -> asyncio.Task | None:
        if not self._enabled or self._running:
            return self._consumer_task
        try:
            self._running = True
            self._accepting = True
            self._consumer_task = asyncio.create_task(
                self._consumer_entry(), name="preview-manager"
            )
            return self._consumer_task
        except Exception as error:
            self._running = False
            self._accepting = False
            self._enabled = False
            self._disabled_reason = "startup_error"
            self._last_error = type(error).__name__
            logger.error(
                "Preview runtime не запущен: %s", type(error).__name__
            )
            return None

    def observe_cycle(self, observations: Sequence[PreviewObservation]) -> None:
        if not self._enabled or not self._accepting:
            return
        observed_at = self._clock()
        latest: dict[str, _Observed] = {}
        for observation in observations:
            latest[observation.twitch_login] = _Observed(observation, observed_at)
        self._latest = latest
        self._latest_observation_at = observed_at
        self._revision += 1
        self._observation_event.set()

    async def shutdown(self) -> None:
        async with self._shutdown_lock:
            self._accepting = False
            self._running = False
            consumer = self._consumer_task
            self._consumer_task = None
            if consumer is not None and not consumer.done():
                consumer.cancel()
            await self._stop_all_sessions()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
            jobs = [task for task in self._job_tasks if not task.done()]
            for task in jobs:
                task.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
            await self._drain_auxiliary_tasks()
            self._job_tasks.clear()
            self._cleanup_tasks.clear()
            self._session_tasks.clear()
            self._latest.clear()
            self._generation_by_login.clear()
            self._latest_observation_at = None
            self._observation_event.clear()

    def health_snapshot(self, now: float | None = None) -> dict[str, object]:
        snapshot_at = self._clock() if now is None else now
        return {
            "enabled": self._enabled,
            "manager_running": self._running,
            "active_sessions": len(self._sessions),
            "active_jobs": sum(
                1
                for record in self._sessions.values()
                if record.job_task is not None and not record.job_task.done()
            ),
            "consumer_tasks": int(
                self._consumer_task is not None and not self._consumer_task.done()
            ),
            "session_tasks": sum(1 for task in self._session_tasks if not task.done()),
            "job_tasks": sum(1 for task in self._job_tasks if not task.done()),
            "latest_observation_age_seconds": self._age(
                snapshot_at, self._latest_observation_at
            ),
            "last_success_age_seconds": self._age(snapshot_at, self._last_success_at),
            "last_error": self._last_error,
            "consecutive_provider_failures": self._consecutive_provider_failures,
            "disabled_reason": self._disabled_reason,
        }

    @staticmethod
    def _age(now: float, then: float | None) -> float | None:
        return max(0.0, now - then) if then is not None else None

    async def _consumer_entry(self) -> None:
        try:
            while self._running:
                await self._observation_event.wait()
                self._observation_event.clear()
                revision = self._revision
                latest = dict(self._latest)
                await self._reconcile(latest)
                if revision != self._revision:
                    self._observation_event.set()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._disable_after_crash(error)

    async def _disable_after_crash(self, error: BaseException) -> None:
        self._last_error = type(error).__name__
        self._disabled_reason = "manager_crash"
        self._enabled = False
        self._accepting = False
        self._running = False
        logger.error("PreviewManager отключён после сбоя: %s", type(error).__name__)
        current = asyncio.current_task()
        consumer = self._consumer_task
        if consumer is not None and consumer is not current and not consumer.done():
            consumer.cancel()
        await self._stop_all_sessions(exclude=current)
        await self._drain_auxiliary_tasks(exclude=current)
        self._latest.clear()
        self._generation_by_login.clear()
        self._latest_observation_at = None
        self._observation_event.clear()

    async def _reconcile(self, latest: dict[str, _Observed]) -> None:
        for login, record in list(self._sessions.items()):
            observed = latest.get(login)
            if (
                observed is None
                or not observed.value.online
                or observed.value.physical_stream_id != record.key.physical_stream_id
                or not self._observation_is_fresh(observed)
            ):
                await self._stop_session(login)

        for login, observed in latest.items():
            observation = observed.value
            if (
                not observation.online
                or observation.physical_stream_id is None
                or not self._observation_is_fresh(observed)
            ):
                if login in self._sessions:
                    await self._stop_session(login)
                else:
                    self._bump_generation(login)
                continue
            destinations = await self._db.list_preview_destination_states(login)
            eligible = [state for state in destinations if self._eligible(state)]
            current = self._sessions.get(login)
            if not eligible:
                if current is not None:
                    await self._stop_session(login)
                continue
            if current is not None:
                if (
                    current.key.physical_stream_id == observation.physical_stream_id
                    and current.task is not None
                    and not current.task.done()
                ):
                    continue
                await self._stop_session(login)
            self._start_session(observation)

        for login in set(self._generation_by_login) - set(latest):
            if login not in self._sessions:
                self._generation_by_login.pop(login, None)

    def _start_session(self, observation: PreviewObservation) -> None:
        physical_stream_id = observation.physical_stream_id
        if physical_stream_id is None:
            return
        generation = self._bump_generation(observation.twitch_login)
        token = PreviewGeneration(
            observation.twitch_login, physical_stream_id, generation
        )
        record = _ManagedSession(
            key=PreviewSessionKey(observation.twitch_login, physical_stream_id),
            token=token,
        )
        self._sessions[observation.twitch_login] = record
        task = asyncio.create_task(
            self._session_entry(record),
            name=f"preview-session-{generation}",
        )
        record.task = task
        self._session_tasks.add(task)
        task.add_done_callback(self._session_done)

    def _session_done(self, task: asyncio.Task) -> None:
        self._session_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and self._enabled:
            asyncio.create_task(self._disable_after_crash(error))
        elif self._running:
            self._observation_event.set()

    async def _session_entry(self, record: _ManagedSession) -> None:
        try:
            while self._is_current(record.token):
                await self._wait_record_operation(record)
                if not self._is_current(record.token):
                    break
                if record.provider_session is None:
                    try:
                        record.provider_session = await self._timed_provider_operation(
                            record,
                            self._provider.open_session(record.key),
                            on_late_result=self._close_late_session,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        await self._provider_failed(record, error)
                        continue
                    record.state = PreviewSessionState.WARMING
                    await self._sleep(self._initial_delay)
                    if not self._is_current(record.token):
                        break

                participants = await self._frozen_participants(record.key.twitch_login)
                if not participants:
                    break
                observed = self._latest.get(record.key.twitch_login)
                if observed is None or not self._is_current(record.token):
                    break
                request = PreviewArtifactRequest(
                    record.token,
                    observed.value,
                    is_first_preview=self._is_first_preview(participants),
                )
                artifact: PreviewArtifact | None = None
                job: asyncio.Task | None = None
                try:
                    record.state = PreviewSessionState.RUNNING
                    job = asyncio.create_task(
                        self._create_artifact(record, request),
                        name=f"preview-job-{record.token.generation}",
                    )
                    record.job_task = job
                    self._job_tasks.add(job)
                    artifact = await job
                except asyncio.CancelledError:
                    if job is not None and job.done() and not job.cancelled():
                        try:
                            late_artifact = job.result()
                        except BaseException:
                            late_artifact = None
                        if late_artifact is not None and record.provider_session is not None:
                            await self._bounded_cleanup(
                                record.provider_session.release_artifact(late_artifact),
                                "late artifact release",
                                record=record,
                                on_late_result=lambda _value: (
                                    self._handle_late_release(record)
                                ),
                            )
                    raise
                except Exception as error:
                    await self._provider_failed(record, error)
                    continue
                finally:
                    if record.job_task is not None:
                        self._job_tasks.discard(record.job_task)
                    record.job_task = None

                record.consecutive_failures = 0
                self._refresh_provider_failures()
                self._last_success_at = self._clock()
                if artifact is not None:
                    try:
                        if self._is_current(record.token):
                            await self._fan_out(record.token, request, participants, artifact)
                    finally:
                        await self._bounded_cleanup(
                            record.provider_session.release_artifact(artifact),
                            "artifact release",
                            record=record,
                            on_late_result=lambda _value: (
                                self._handle_late_release(record)
                            ),
                        )
                if not self._is_current(record.token):
                    break
                record.state = PreviewSessionState.WAITING
                await self._sleep(self._interval)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._disable_after_crash(error)
        finally:
            record.state = PreviewSessionState.STOPPED
            if record.provider_session is not None:
                late_cleanup = record.late_cleanup_task
                if record.provider_operation is not None or (
                    late_cleanup is not None and not late_cleanup.done()
                ):
                    record.close_deferred = True
                else:
                    await self._close_record_session(record)

    async def _create_artifact(
        self, record: _ManagedSession, request: PreviewArtifactRequest
    ) -> PreviewArtifact | None:
        async with self._artifact_semaphore:
            provider_session = record.provider_session
            if provider_session is None:
                return None
            return await self._timed_provider_operation(
                record,
                provider_session.create_artifact(request),
                on_late_result=lambda artifact: self._handle_late_artifact(
                    record, provider_session, artifact
                ),
            )

    async def _timed_provider_operation(
        self,
        record: _ManagedSession,
        operation: Awaitable[object],
        *,
        on_late_result: Callable[[object], None],
    ) -> object:
        task = asyncio.create_task(operation)
        record.provider_operation = task
        try:
            done, _pending = await asyncio.wait(
                {task}, timeout=self._job_timeout
            )
            if done:
                record.provider_operation = None
                return task.result()
            task.cancel()
            self._watch_late_result(
                task, on_late_result, record=record
            )
            await asyncio.sleep(0)
            raise asyncio.TimeoutError
        except asyncio.CancelledError:
            if task.done():
                try:
                    result = task.result()
                except BaseException:
                    on_late_result(None)
                else:
                    on_late_result(result)
                record.provider_operation = None
            else:
                task.cancel()
                self._watch_late_result(
                    task, on_late_result, record=record
                )
            raise

    def _watch_late_result(
        self,
        task: asyncio.Task,
        on_late_result: Callable[[object], None],
        *,
        record: _ManagedSession | None = None,
    ) -> None:
        self._late_provider_tasks.add(task)

        def consume(done: asyncio.Task) -> None:
            self._late_provider_tasks.discard(done)
            if record is not None and record.provider_operation is done:
                record.provider_operation = None
            if done.cancelled():
                on_late_result(None)
                return
            try:
                result = done.result()
            except BaseException as error:
                logger.warning(
                    "Preview provider late operation завершилась ошибкой: %s",
                    type(error).__name__,
                )
                on_late_result(None)
                return
            try:
                on_late_result(result)
            except Exception as error:
                logger.warning(
                    "Preview provider late result не удалось утилизировать: %s",
                    type(error).__name__,
                )

        task.add_done_callback(consume)

    def _close_late_session(self, value: object) -> None:
        if value is None:
            return
        session = value
        self._schedule_cleanup(session.close(), "late provider session close")

    def _handle_late_artifact(
        self,
        record: _ManagedSession,
        session: PreviewArtifactSession,
        value: object,
    ) -> None:
        task = asyncio.create_task(
            self._finish_late_artifact(record, session, value),
            name=f"preview-late-cleanup-{record.token.generation}",
        )
        record.late_cleanup_task = task
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _finish_late_artifact(
        self,
        record: _ManagedSession,
        session: PreviewArtifactSession,
        value: object,
    ) -> None:
        if isinstance(value, (LocalVideo, TelegramVideo)):
            await self._bounded_cleanup(
                session.release_artifact(value),
                "late artifact release",
                record=record,
                on_late_result=lambda _value: self._handle_late_release(record),
            )
        if record.close_deferred and record.provider_operation is None:
            await self._close_record_session(record)

    def _handle_late_release(self, record: _ManagedSession) -> None:
        if not record.close_deferred:
            return
        task = asyncio.create_task(
            self._close_record_session(record),
            name=f"preview-deferred-close-{record.token.generation}",
        )
        record.late_cleanup_task = task
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _close_record_session(self, record: _ManagedSession) -> None:
        session = record.provider_session
        if session is None or record.close_started:
            return
        record.close_started = True
        await self._bounded_cleanup(session.close(), "provider session close")

    def _schedule_cleanup(
        self, operation: Awaitable[object], description: str
    ) -> asyncio.Task:
        task = asyncio.create_task(self._bounded_cleanup(operation, description))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return task

    async def _bounded_cleanup(
        self,
        operation: Awaitable[object],
        description: str,
        *,
        record: _ManagedSession | None = None,
        on_late_result: Callable[[object], None] | None = None,
    ) -> None:
        task = asyncio.create_task(operation)
        if record is not None:
            record.provider_operation = task
        late_handler = on_late_result or (lambda _value: None)
        try:
            done, _pending = await asyncio.wait(
                {task}, timeout=self._cleanup_timeout
            )
            if not done:
                task.cancel()
                self._watch_late_result(
                    task, late_handler, record=record
                )
                self._last_error = "TimeoutError"
                logger.warning("Preview cleanup превысил timeout: %s", description)
                return
            if record is not None and record.provider_operation is task:
                record.provider_operation = None
            task.result()
        except asyncio.CancelledError:
            task.cancel()
            self._watch_late_result(
                task, late_handler, record=record
            )
            raise
        except Exception as error:
            self._last_error = type(error).__name__
            logger.warning(
                "Preview cleanup завершился ошибкой (%s): %s",
                description,
                type(error).__name__,
            )

    async def _provider_failed(
        self, record: _ManagedSession, error: BaseException
    ) -> None:
        record.consecutive_failures += 1
        self._refresh_provider_failures()
        self._last_error = type(error).__name__
        record.state = PreviewSessionState.BACKOFF
        phase = getattr(error, "phase", None)
        if phase in _PROVIDER_FAILURE_PHASES:
            logger.warning(
                "Preview provider временно недоступен: %s phase=%s",
                type(error).__name__,
                phase,
            )
        else:
            logger.warning(
                "Preview provider временно недоступен: %s", type(error).__name__
            )
        delay = min(60 * (2 ** (record.consecutive_failures - 1)), self._interval)
        await self._sleep(delay)

        await self._wait_record_operation(record)

    async def _wait_record_operation(self, record: _ManagedSession) -> None:
        pending = record.provider_operation
        if pending is not None and not pending.done():
            await asyncio.wait({pending})
        await asyncio.sleep(0)
        late_cleanup = record.late_cleanup_task
        if late_cleanup is not None and not late_cleanup.done():
            await asyncio.shield(late_cleanup)

    def _refresh_provider_failures(self) -> None:
        self._consecutive_provider_failures = max(
            (record.consecutive_failures for record in self._sessions.values()),
            default=0,
        )

    async def _drain_auxiliary_tasks(
        self, exclude: asyncio.Task | None = None
    ) -> None:
        late_operations = [
            task
            for task in self._late_provider_tasks
            if task is not exclude and not task.done()
        ]
        for task in late_operations:
            task.cancel()
        if late_operations:
            await asyncio.wait(late_operations, timeout=self._cleanup_timeout)

        for _ in range(3):
            await asyncio.sleep(0)
            cleanups = [
                task
                for task in self._cleanup_tasks
                if task is not exclude and not task.done()
            ]
            if not cleanups:
                break
            await asyncio.gather(*cleanups, return_exceptions=True)

    async def _frozen_participants(
        self, login: str
    ) -> tuple[tuple[int, str, int, str], ...]:
        destinations = await self._db.list_preview_destination_states(login)
        return tuple(
            (state.chat_id, state.logical_stream_id, state.message_id, state.message_kind)
            for state in destinations
            if self._eligible(state)
            and state.logical_stream_id is not None
            and state.message_id is not None
        )

    @staticmethod
    def _is_first_preview(
        participants: tuple[tuple[int, str, int, str], ...]
    ) -> bool:
        """True until at least one eligible destination has an applied video
        preview (``message_kind == "video"``) for the current physical stream.
        Reuses the durable P2B lifecycle field already tracked per logical
        stream instead of introducing a second persistent state."""
        return not any(kind == "video" for _, _, _, kind in participants)

    async def _fan_out(
        self,
        token: PreviewGeneration,
        request: PreviewArtifactRequest,
        participants: tuple[tuple[int, str, int, str], ...],
        artifact: PreviewArtifact,
    ) -> None:
        cached_file_id: str | None = None
        for chat_id, logical_stream_id, message_id, _message_kind in participants:
            if not self._is_current(token):
                return
            destination = await self._db.get_preview_destination_state(
                chat_id, token.twitch_login
            )
            if (
                destination is None
                or not self._eligible(destination)
                or destination.logical_stream_id != logical_stream_id
                or destination.message_id != message_id
            ):
                continue
            video: PreviewArtifact = artifact
            if isinstance(artifact, LocalVideo) and cached_file_id is not None:
                video = TelegramVideo(cached_file_id)
            try:
                result = await self._live_post_updater.apply_video(
                    target=LivePostTarget(
                        chat_id=chat_id,
                        twitch_login=token.twitch_login,
                        logical_stream_id=logical_stream_id,
                        message_id=message_id,
                    ),
                    video=video,
                    is_current_physical_stream=lambda token=token: self._is_current(token),
                    build_content=lambda destination=destination: self._content(
                        self._latest_observation_for(
                            token, fallback=request.observation
                        ),
                        destination,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._last_error = type(error).__name__
                logger.warning(
                    "Preview Telegram fan-out пропустил destination: %s",
                    type(error).__name__,
                )
                continue
            if (
                isinstance(artifact, LocalVideo)
                and result.status is LivePostMediaStatus.APPLIED
                and result.file_id
            ):
                cached_file_id = result.file_id

    def _latest_observation_for(
        self,
        token: PreviewGeneration,
        *,
        fallback: PreviewObservation,
    ) -> PreviewObservation:
        observed = self._latest.get(token.twitch_login)
        if (
            observed is not None
            and observed.value.online
            and observed.value.physical_stream_id == token.physical_stream_id
        ):
            return observed.value
        return fallback

    async def _content(
        self, observation: PreviewObservation, destination: PreviewDestinationState
    ) -> LivePostContent:
        content = self._build_content(observation, destination)
        if inspect.isawaitable(content):
            content = await content
        if not isinstance(content, LivePostContent):
            raise TypeError("build_content must return LivePostContent")
        return content

    async def _stop_session(self, login: str) -> None:
        record = self._sessions.pop(login, None)
        self._refresh_provider_failures()
        self._bump_generation(login)
        if record is None:
            return
        record.stopping = True
        task = record.task
        if task is not None and not task.done():
            task.cancel()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)

    async def _stop_all_sessions(self, exclude: asyncio.Task | None = None) -> None:
        records = list(self._sessions.values())
        self._sessions.clear()
        self._refresh_provider_failures()
        tasks: list[asyncio.Task] = []
        for record in records:
            self._bump_generation(record.key.twitch_login)
            record.stopping = True
            task = record.task
            if task is None or task is exclude:
                continue
            if not task.done():
                task.cancel()
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _bump_generation(self, login: str) -> int:
        generation = self._generation_by_login.get(login, 0) + 1
        self._generation_by_login[login] = generation
        return generation

    def _observation_is_fresh(self, observed: _Observed) -> bool:
        age = self._clock() - observed.observed_at
        return 0 <= age <= self._lease_seconds

    def _is_current(self, token: PreviewGeneration) -> bool:
        if not self._enabled or not self._running or not self._accepting:
            return False
        record = self._sessions.get(token.twitch_login)
        observed = self._latest.get(token.twitch_login)
        return bool(
            record is not None
            and record.token == token
            and not record.stopping
            and record.state is not PreviewSessionState.STOPPED
            and observed is not None
            and observed.value.online
            and observed.value.physical_stream_id == token.physical_stream_id
            and self._observation_is_fresh(observed)
        )

    @staticmethod
    def _eligible(state: PreviewDestinationState) -> bool:
        return bool(
            state.preview_enabled
            and state.notify_enabled
            and state.is_live
            and state.logical_stream_id is not None
            and state.message_id is not None
        )
