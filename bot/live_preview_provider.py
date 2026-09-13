from __future__ import annotations

import asyncio
from typing import Any, NoReturn

from bot.live_post import LocalVideo
from bot.preview_analysis import AnalysisResult, AnalysisStatus, HighlightAnalyzer
from bot.preview_capture import CaptureOutcome, SnapshotAcquireResult, SnapshotStatus
from bot.preview_render import PreviewRenderer, RenderResult, RenderStatus
from bot.preview_runtime import (
    PreviewArtifact,
    PreviewArtifactRequest,
    PreviewArtifactSession,
    PreviewSessionKey,
)
from bot.preview_source import (
    CaptureRetryAction,
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


class LivePreviewProviderError(RuntimeError):
    """A sanitized orchestration failure safe to expose to runtime logs."""

    def __init__(self) -> None:
        super().__init__("live preview artifact provider failed")


def _provider_error() -> NoReturn:
    raise LivePreviewProviderError() from None


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
            _provider_error()
        if not _started(result):
            _provider_error()
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
        self._leases: dict[int, tuple[PreviewArtifact, Any]] = {}

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
            _provider_error()

        outcome = getattr(handle, "outcome", None)
        if outcome is not None:
            if not isinstance(outcome, CaptureOutcome):
                _provider_error()
            await self._recover_capture(capture_retry_action(outcome))
            _provider_error()

        try:
            acquired = handle.acquire_snapshot(_SNAPSHOT_WINDOW_SECONDS)
        except Exception:
            _provider_error()
        if not isinstance(acquired, SnapshotAcquireResult):
            _provider_error()
        if acquired.status is SnapshotStatus.EMPTY:
            return None
        if acquired.status is SnapshotStatus.CLOSING:
            try:
                outcome = await handle.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                _provider_error()
            if not isinstance(outcome, CaptureOutcome):
                _provider_error()
            await self._recover_capture(capture_retry_action(outcome))
            _provider_error()
        if acquired.status is not SnapshotStatus.READY or acquired.snapshot is None:
            _provider_error()

        snapshot = acquired.snapshot
        try:
            result = await self._create_from_snapshot(snapshot)
        except asyncio.CancelledError:
            self._release_during_cancellation(snapshot)
            raise
        except Exception:
            try:
                snapshot.release()
            except Exception:
                _provider_error()
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
            _provider_error()
        if result is _RECOVER_CAPTURE:
            await self._recover_capture(
                CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE
            )
            _provider_error()
        if result is None:
            return None
        return self._transfer_rendered_preview(result)

    async def _create_from_snapshot(self, snapshot: Any) -> object | None:
        try:
            analysis = await self._analyzer.analyze(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error()
        if not isinstance(analysis, AnalysisResult):
            _provider_error()
        if analysis.status in {
            AnalysisStatus.NO_SELECTION,
            AnalysisStatus.SNAPSHOT_TOO_SHORT,
        }:
            return None
        if analysis.status is not AnalysisStatus.SUCCESS:
            if (
                analysis.status is AnalysisStatus.SNAPSHOT_INVALIDATED
                or (
                    analysis.status is AnalysisStatus.PROCESS_FAILED
                    and analysis.diagnostic_code
                    in _CAPTURE_RESET_ANALYSIS_DIAGNOSTICS
                )
            ):
                return _RECOVER_CAPTURE
            _provider_error()

        try:
            rendered = await self._renderer.render(snapshot, analysis.selection)
        except asyncio.CancelledError:
            raise
        except Exception:
            _provider_error()
        if not isinstance(rendered, RenderResult):
            _provider_error()
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
            _provider_error()
        return rendered.artifact

    def _transfer_rendered_preview(self, rendered: Any) -> PreviewArtifact:
        release = getattr(rendered, "release", None)
        if not callable(release):
            _provider_error()
        try:
            artifact = LocalVideo(rendered.path, "preview.mp4")
            self._leases[id(artifact)] = (artifact, rendered)
            return artifact
        except asyncio.CancelledError:
            self._release_during_cancellation(rendered)
            raise
        except Exception:
            self._release_owner(rendered)
            _provider_error()

    async def release_artifact(self, artifact: PreviewArtifact) -> None:
        lease = self._leases.get(id(artifact))
        if lease is None or lease[0] is not artifact:
            return
        del self._leases[id(artifact)]
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
        _provider_error()

    async def _recover_capture(self, action: CaptureRetryAction) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                await handle.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                _provider_error()
        if action is CaptureRetryAction.STOP:
            self._terminal = True
            return
        if action is CaptureRetryAction.WAIT_FOR_RESOURCE_IF_ACTIVE:
            return
        if action is not CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE:
            self._terminal = True
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

    @staticmethod
    def _release_during_cancellation(owner: Any) -> None:
        try:
            owner.release()
        except BaseException:
            return


__all__ = ("LivePreviewArtifactProvider", "LivePreviewProviderError")
