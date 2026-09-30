from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, NoReturn

from bot.live_post import LocalAnimation
from bot.preview_analysis import (
    AnalysisResult,
    AnalysisStatus,
    HighlightAnalyzer,
    HighlightSelection,
)
from bot.preview_capture import (
    CaptureEndReason,
    CaptureOutcome,
    SnapshotAcquireResult,
    SnapshotStatus,
)
from bot.preview_render import PreviewRenderer, RenderResult, RenderStatus
from bot.preview_render.models import DIAGNOSTIC_CODES
from bot.preview_runtime import (
    PreviewArtifact,
    PreviewArtifactRequest,
    PreviewArtifactSession,
    PreviewSessionKey,
)
from bot.preview_source import (
    CaptureRetryAction,
    PlaybackResolveStatus,
    RetryDisposition,
    TwitchCaptureSource,
    TwitchCaptureStartResult,
    TwitchCaptureStartStatus,
    capture_retry_action,
)


_SNAPSHOT_WINDOW_SECONDS = 90.0
_CAPTURE_RESET_ANALYSIS_DIAGNOSTICS = frozenset(
    {"missing_segment", "empty_snapshot"}
)
_CAPTURE_RESET_RENDER_DIAGNOSTICS = frozenset({"source_file_missing"})
_RECOVER_CAPTURE = object()
_MAX_CYCLE_SNIPPETS = 5
_CYCLE_COMPLETE_SECONDS = 29.0


@dataclass
class _PendingCycle:
    snippet: Any
    output: Any
    snippets: tuple[Any, ...]
    obsolete: tuple[Any, ...]
    cutoff: tuple[int, float] | None

    def release(self) -> None:
        released: set[int] = set()
        for owner in (self.output, self.snippet):
            identity = id(owner)
            if identity in released:
                continue
            released.add(identity)
            try:
                owner.release()
            except BaseException:
                continue


_SOURCE_RESOLVE_PHASES = frozenset(
    f"source_resolve_{status.value}"
    for status in PlaybackResolveStatus
    if status is not PlaybackResolveStatus.RESOLVED
)
_SOURCE_CAPTURE_PHASES = frozenset(
    f"source_capture_{reason.value}" for reason in CaptureEndReason
)
_PROVIDER_ERROR_PHASES = (
    frozenset({
        "source_open", "source_invalid_identity", "source_capture_capacity",
        "capture_state", "snapshot", "analysis", "render", "artifact",
        "request", "session_close", "capture_recovery", "provider",
    })
    | _SOURCE_RESOLVE_PHASES
    | _SOURCE_CAPTURE_PHASES
)
_RENDER_FAILURE_STATUSES = frozenset(
    status.value for status in RenderStatus if status is not RenderStatus.SUCCESS
)


class LivePreviewProviderError(RuntimeError):
    """A sanitized orchestration failure safe to expose to runtime logs."""

    def __init__(
        self,
        phase: str = "provider",
        *,
        status: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.phase = phase if phase in _PROVIDER_ERROR_PHASES else "provider"
        self.status = (
            status
            if self.phase == "render" and status in _RENDER_FAILURE_STATUSES
            else None
        )
        self.reason = (
            reason
            if self.status is not None and reason in DIAGNOSTIC_CODES
            else None
        )
        super().__init__("live preview artifact provider failed")


def _provider_error(
    phase: str = "provider",
    *,
    status: str | None = None,
    reason: str | None = None,
) -> NoReturn:
    raise LivePreviewProviderError(
        phase, status=status, reason=reason
    ) from None


class LivePreviewArtifactProvider:
    def __init__(
        self,
        source: TwitchCaptureSource,
        analyzer: HighlightAnalyzer,
        renderer: PreviewRenderer,
    ) -> None:
        self._source = source
        self._analyzer = analyzer
        self._renderer = renderer

    async def open_session(self, key: PreviewSessionKey) -> PreviewArtifactSession:
        try:
            result = await self._source.open(
                key.twitch_login, key.physical_stream_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error("source_open")
        if not _started(result):
            _provider_error(_source_failure_phase(result))
        return _LivePreviewArtifactSession(
            source=self._source,
            analyzer=self._analyzer,
            renderer=self._renderer,
            key=key,
            handle=result.handle,
        )


def _started(result: object) -> bool:
    return (
        isinstance(result, TwitchCaptureStartResult)
        and result.status is TwitchCaptureStartStatus.STARTED
        and result.handle is not None
    )


def _source_failure_phase(result: object) -> str:
    if not isinstance(result, TwitchCaptureStartResult):
        return "source_open"
    if result.status is TwitchCaptureStartStatus.INVALID_IDENTITY:
        return "source_invalid_identity"
    if (
        result.status is TwitchCaptureStartStatus.RESOLVE_FAILED
        and result.resolve_status is not None
    ):
        candidate = f"source_resolve_{result.resolve_status.value}"
        return candidate if candidate in _PROVIDER_ERROR_PHASES else "source_open"
    if (
        result.status is TwitchCaptureStartStatus.CAPTURE_NOT_STARTED
        and result.capture_outcome is not None
    ):
        outcome = result.capture_outcome
        if (
            outcome.reason is CaptureEndReason.CAPABILITY_UNAVAILABLE
            and outcome.diagnostic_code == "capacity"
        ):
            return "source_capture_capacity"
        candidate = f"source_capture_{outcome.reason.value}"
        return candidate if candidate in _PROVIDER_ERROR_PHASES else "source_open"
    return "source_open"


class _LivePreviewArtifactSession:
    def __init__(
        self,
        *,
        source: TwitchCaptureSource,
        analyzer: HighlightAnalyzer,
        renderer: PreviewRenderer,
        key: PreviewSessionKey,
        handle: Any,
    ) -> None:
        self._source = source
        self._analyzer = analyzer
        self._renderer = renderer
        self._key = key
        self._handle: Any | None = handle
        self._closed = False
        self._terminal = False
        self._leases: dict[int, tuple[PreviewArtifact, Any | None]] = {}
        self._cycle_snippets: tuple[Any, ...] = ()
        self._capture_cutoff: tuple[int, float] | None = None

    async def create_artifact(
        self, request: PreviewArtifactRequest
    ) -> PreviewArtifact | None:
        if self._closed or self._terminal:
            return None
        self._validate_request(request)
        if self._handle is None:
            await self._open_deferred_capture()
        handle = self._handle
        if handle is None:
            _provider_error("capture_state")

        outcome = getattr(handle, "outcome", None)
        if outcome is not None:
            if not isinstance(outcome, CaptureOutcome):
                _provider_error("capture_state")
            await self._recover_capture(capture_retry_action(outcome))
            _provider_error("capture_recovery")

        try:
            acquired = handle.acquire_snapshot(_SNAPSHOT_WINDOW_SECONDS)
        except Exception:
            _provider_error("snapshot")
        if not isinstance(acquired, SnapshotAcquireResult):
            _provider_error("snapshot")
        if acquired.status is SnapshotStatus.EMPTY:
            return None
        if acquired.status is SnapshotStatus.CLOSING:
            try:
                outcome = await handle.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                _provider_error("capture_state")
            if not isinstance(outcome, CaptureOutcome):
                _provider_error("capture_state")
            await self._recover_capture(capture_retry_action(outcome))
            _provider_error("capture_recovery")
        if acquired.status is not SnapshotStatus.READY or acquired.snapshot is None:
            _provider_error("snapshot")

        snapshot = acquired.snapshot
        try:
            result = await self._create_from_snapshot(
                snapshot, request.is_first_preview
            )
        except asyncio.CancelledError:
            self._release_during_cancellation(snapshot)
            raise
        except Exception:
            try:
                snapshot.release()
            except Exception:
                _provider_error("snapshot")
            raise
        try:
            snapshot.release()
        except asyncio.CancelledError:
            if result is not None and result is not _RECOVER_CAPTURE:
                self._release_during_cancellation(result)
            raise
        except Exception:
            if result is not None and result is not _RECOVER_CAPTURE:
                self._release_owner(result)
            _provider_error("snapshot")
        if result is _RECOVER_CAPTURE:
            await self._recover_capture(
                CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE
            )
            _provider_error("capture_recovery")
        if result is None:
            return None
        return self._commit_cycle(result)

    async def _create_from_snapshot(
        self, snapshot: Any, is_first_preview: bool
    ) -> _PendingCycle | object | None:
        try:
            analysis = await self._analyzer.analyze(
                snapshot, include_fallback=is_first_preview
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error("analysis")
        if not isinstance(analysis, AnalysisResult):
            _provider_error("analysis")
        if analysis.status is AnalysisStatus.SNAPSHOT_TOO_SHORT:
            return None
        if analysis.status is AnalysisStatus.NO_SELECTION:
            if analysis.fallback is None:
                return None
            selection = HighlightSelection((analysis.fallback,))
        elif analysis.status is AnalysisStatus.SUCCESS:
            selection = analysis.selection
        else:
            if (
                analysis.status is AnalysisStatus.SNAPSHOT_INVALIDATED
                or (
                    analysis.status is AnalysisStatus.PROCESS_FAILED
                    and analysis.diagnostic_code
                    in _CAPTURE_RESET_ANALYSIS_DIAGNOSTICS
                )
            ):
                return _RECOVER_CAPTURE
            _provider_error("analysis")

        window = self._select_new_window(snapshot, selection)
        if window is None:
            return None
        selection = HighlightSelection((window,))

        try:
            rendered = await self._renderer.render(snapshot, selection)
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error("render")
        if not isinstance(rendered, RenderResult):
            _provider_error("render")
        if rendered.status is not RenderStatus.SUCCESS:
            if (
                rendered.status is RenderStatus.SNAPSHOT_INVALIDATED
                or (
                    rendered.status is RenderStatus.PROCESS_FAILED
                    and rendered.diagnostic_code
                    in _CAPTURE_RESET_RENDER_DIAGNOSTICS
                )
            ):
                return _RECOVER_CAPTURE
            _provider_error(
                "render",
                status=rendered.status.value,
                reason=rendered.diagnostic_code,
            )
        snippet = rendered.artifact
        prospective = (
            (snippet,)
            if self._cycle_complete()
            else self._cycle_snippets + (snippet,)
        )
        obsolete = self._cycle_snippets if len(prospective) == 1 else ()
        if len(prospective) == 1:
            output = snippet
        else:
            try:
                cumulative = await self._renderer.render_cumulative(prospective)
            except asyncio.CancelledError:
                self._release_during_cancellation(snippet)
                raise
            except Exception:
                self._release_owner(snippet)
                _provider_error("render")
            if not isinstance(cumulative, RenderResult):
                self._release_owner(snippet)
                _provider_error("render")
            if cumulative.status is not RenderStatus.SUCCESS:
                self._release_owner(snippet)
                if (
                    cumulative.status is RenderStatus.PROCESS_FAILED
                    and cumulative.diagnostic_code == "source_file_missing"
                ):
                    self._clear_cycle()
                _provider_error(
                    "render",
                    status=cumulative.status.value,
                    reason=cumulative.diagnostic_code,
                )
            output = cumulative.artifact
        return _PendingCycle(
            snippet=snippet,
            output=output,
            snippets=prospective,
            obsolete=obsolete,
            cutoff=self._snapshot_tail(snapshot),
        )

    def _commit_cycle(self, pending: _PendingCycle) -> PreviewArtifact:
        release = getattr(pending.output, "release", None)
        if not callable(release):
            pending.release()
            _provider_error("artifact")
        try:
            artifact = LocalAnimation(
                pending.output.path,
                duration_seconds=pending.output.duration_seconds,
                filename="preview.mp4",
            )
        except asyncio.CancelledError:
            self._release_during_cancellation(pending)
            raise
        except Exception:
            pending.release()
            _provider_error("artifact")
        for owner in pending.obsolete:
            try:
                owner.release()
            except Exception:
                pending.release()
                _provider_error("artifact")
        self._cycle_snippets = pending.snippets
        self._capture_cutoff = pending.cutoff
        lease_owner = None if pending.output is pending.snippet else pending.output
        self._leases[id(artifact)] = (artifact, lease_owner)
        return artifact

    async def release_artifact(self, artifact: PreviewArtifact) -> None:
        lease = self._leases.get(id(artifact))
        if lease is None or lease[0] is not artifact:
            return
        del self._leases[id(artifact)]
        if lease[1] is not None:
            self._release_owner(lease[1])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._terminal = True
        failure = False
        leases = tuple(self._leases.values())
        self._leases.clear()
        for _artifact, owner in leases:
            if owner is None:
                continue
            try:
                owner.release()
            except Exception:
                failure = True
        snippets = self._cycle_snippets
        self._cycle_snippets = ()
        self._capture_cutoff = None
        for owner in snippets:
            try:
                owner.release()
            except Exception:
                failure = True
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                await handle.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                failure = True
        if failure:
            _provider_error()

    def _validate_request(self, request: PreviewArtifactRequest) -> None:
        try:
            generation = request.generation
            observation = request.observation
            valid = (
                generation.twitch_login == self._key.twitch_login
                and generation.physical_stream_id
                == self._key.physical_stream_id
                and observation.twitch_login == self._key.twitch_login
                and observation.physical_stream_id
                == self._key.physical_stream_id
                and observation.online
            )
        except Exception:
            _provider_error()
        if not valid:
            _provider_error()

    async def _open_deferred_capture(self) -> None:
        try:
            result = await self._source.open(
                self._key.twitch_login, self._key.physical_stream_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error()
        if _started(result):
            self._handle = result.handle
            return
        if not isinstance(result, TwitchCaptureStartResult):
            _provider_error()
        self._apply_start_failure(result)
        _provider_error(_source_failure_phase(result))

    async def _recover_capture(self, action: CaptureRetryAction) -> None:
        handle = self._handle
        self._handle = None
        self._capture_cutoff = None
        if handle is not None:
            try:
                await handle.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                _provider_error()
        if action is CaptureRetryAction.STOP:
            self._terminal = True
            self._clear_cycle()
            return
        if action is CaptureRetryAction.WAIT_FOR_RESOURCE_IF_ACTIVE:
            return
        if action is not CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE:
            self._terminal = True
            self._clear_cycle()
            return
        try:
            result = await self._source.open(
                self._key.twitch_login, self._key.physical_stream_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error()
        if _started(result):
            self._handle = result.handle
            return
        if not isinstance(result, TwitchCaptureStartResult):
            _provider_error()
        self._apply_start_failure(result)

    def _apply_start_failure(self, result: TwitchCaptureStartResult) -> None:
        outcome = result.capture_outcome
        if outcome is not None:
            action = capture_retry_action(outcome)
            if action is CaptureRetryAction.STOP:
                self._terminal = True
            return
        if result.retry_disposition is RetryDisposition.TERMINAL:
            self._terminal = True

    @staticmethod
    def _release_owner(owner: Any) -> None:
        try:
            owner.release()
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error()

    def _cycle_complete(self) -> bool:
        if len(self._cycle_snippets) >= _MAX_CYCLE_SNIPPETS:
            return True
        try:
            duration = math.fsum(
                float(item.duration_seconds) for item in self._cycle_snippets
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            return True
        return duration >= _CYCLE_COMPLETE_SECONDS

    def _clear_cycle(self) -> None:
        snippets = self._cycle_snippets
        self._cycle_snippets = ()
        for owner in snippets:
            try:
                owner.release()
            except BaseException:
                continue

    def _select_new_window(
        self, snapshot: Any, selection: HighlightSelection
    ) -> Any | None:
        candidates = []
        for window in selection.windows:
            position = self._snapshot_position(snapshot, window.start_seconds)
            if (
                self._capture_cutoff is not None
                and position is not None
                and position <= self._capture_cutoff
            ):
                continue
            candidates.append(window)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (float(item.score), float(item.start_seconds)),
        )

    @staticmethod
    def _snapshot_position(
        snapshot: Any, offset_seconds: float
    ) -> tuple[int, float] | None:
        try:
            records = tuple(snapshot.records)
            offset = float(offset_seconds)
            if not records or not math.isfinite(offset) or offset < 0:
                return None
            elapsed = 0.0
            for record in records:
                duration = float(record.duration_seconds)
                sequence = record.sequence
                if type(sequence) is not int or not math.isfinite(duration) or duration <= 0:
                    return None
                if offset < elapsed + duration:
                    return sequence, round(offset - elapsed, 6)
                elapsed = math.fsum((elapsed, duration))
            last = records[-1]
            if math.isclose(offset, elapsed, abs_tol=1e-6):
                return last.sequence, round(float(last.duration_seconds), 6)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        return None

    @classmethod
    def _snapshot_tail(cls, snapshot: Any) -> tuple[int, float] | None:
        try:
            return cls._snapshot_position(
                snapshot, float(snapshot.actual_duration_seconds)
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _release_during_cancellation(owner: Any) -> None:
        try:
            owner.release()
        except BaseException:
            return


__all__ = ("LivePreviewArtifactProvider", "LivePreviewProviderError")
