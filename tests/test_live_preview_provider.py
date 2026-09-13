from __future__ import annotations

import asyncio
import inspect
import traceback
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from bot.live_post import LocalVideo, TelegramVideo
from bot.live_preview_provider import (
    LivePreviewArtifactProvider,
    LivePreviewProviderError,
)
from bot.preview_analysis import (
    AnalysisResult,
    AnalysisStatus,
    HighlightSelection,
    HighlightWindow,
)
from bot.preview_capture import (
    CaptureEndReason,
    CaptureOutcome,
    SnapshotAcquireResult,
    SnapshotStatus,
)
from bot.preview_render import RenderResult, RenderStatus
from bot.preview_runtime import (
    PreviewArtifactRequest,
    PreviewGeneration,
    PreviewObservation,
    PreviewSessionKey,
)
from bot.preview_source import (
    RetryDisposition,
    TwitchCaptureStartResult,
    TwitchCaptureStartStatus,
)


def _request(
    key: PreviewSessionKey,
    *,
    generation: int = 1,
    observation_login: str | None = None,
    observation_physical_id: str | None = None,
    online: bool = True,
    is_first_preview: bool = False,
) -> PreviewArtifactRequest:
    return PreviewArtifactRequest(
        generation=PreviewGeneration(
            twitch_login=key.twitch_login,
            physical_stream_id=key.physical_stream_id,
            generation=generation,
        ),
        observation=PreviewObservation(
            twitch_login=observation_login or key.twitch_login,
            online=online,
            physical_stream_id=(
                observation_physical_id
                if observation_physical_id is not None
                else key.physical_stream_id
            ),
            title="title",
            game_name="game",
            viewer_count=1,
            twitch_started_at="2026-09-13T00:00:00Z",
        ),
        is_first_preview=is_first_preview,
    )


def _selection() -> HighlightSelection:
    return HighlightSelection((HighlightWindow(0.0, 3.0, 0.9),))


def _started(handle: object) -> TwitchCaptureStartResult:
    return TwitchCaptureStartResult(
        status=TwitchCaptureStartStatus.STARTED,
        retry_disposition=RetryDisposition.NONE,
        handle=handle,
    )


def _not_started(outcome: CaptureOutcome) -> TwitchCaptureStartResult:
    return TwitchCaptureStartResult(
        status=TwitchCaptureStartStatus.CAPTURE_NOT_STARTED,
        retry_disposition=RetryDisposition.RETRYABLE,
        capture_outcome=outcome,
    )


class FakeSnapshot:
    def __init__(self, release_error: BaseException | None = None) -> None:
        self.release_calls = 0
        self.release_error = release_error

    def release(self) -> None:
        self.release_calls += 1
        if self.release_error is not None:
            raise self.release_error


class FakeCaptureHandle:
    def __init__(
        self,
        *results: SnapshotAcquireResult,
        outcome: CaptureOutcome | None = None,
        close_outcome: CaptureOutcome | None = None,
    ) -> None:
        self.results = deque(results)
        self.outcome = outcome
        self.close_outcome = close_outcome or outcome or CaptureOutcome(
            CaptureEndReason.SHUTDOWN
        )
        self.acquire_calls: list[float] = []
        self.close_calls = 0
        self.wait_calls = 0

    def acquire_snapshot(self, window_seconds: float) -> SnapshotAcquireResult:
        self.acquire_calls.append(window_seconds)
        if not self.results:
            raise AssertionError("unexpected snapshot acquisition")
        return self.results.popleft()

    async def wait(self) -> CaptureOutcome:
        self.wait_calls += 1
        self.outcome = self.close_outcome
        return self.close_outcome

    async def close(self) -> CaptureOutcome:
        self.close_calls += 1
        self.outcome = self.close_outcome
        return self.close_outcome


class ReleaseAwareCaptureHandle(FakeCaptureHandle):
    def __init__(self, snapshot: FakeSnapshot) -> None:
        super().__init__(SnapshotAcquireResult(SnapshotStatus.READY, snapshot))
        self.snapshot = snapshot

    async def close(self) -> CaptureOutcome:
        if self.snapshot.release_calls != 1:
            raise AssertionError("capture closed while snapshot remained pinned")
        return await super().close()


class FakeSource:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.open_calls: list[tuple[str, str]] = []

    async def open(self, twitch_login: str, physical_stream_id: str):
        self.open_calls.append((twitch_login, physical_stream_id))
        if not self.results:
            raise AssertionError("unexpected source.open")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


class FakeAnalyzer:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.calls: list[object] = []
        self.include_fallback_calls: list[bool] = []

    async def analyze(self, snapshot: object, *, include_fallback: bool = False):
        self.calls.append(snapshot)
        self.include_fallback_calls.append(include_fallback)
        if not self.results:
            raise AssertionError("unexpected analyzer call")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


class FakeRenderer:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.calls: list[tuple[object, HighlightSelection]] = []

    async def render(self, snapshot: object, selection: HighlightSelection):
        self.calls.append((snapshot, selection))
        if not self.results:
            raise AssertionError("unexpected renderer call")
        result = self.results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


class FakeRenderedPreview:
    def __init__(
        self,
        path: Path | str = "C:/private/preview.mp4",
        release_error: Exception | None = None,
    ) -> None:
        self.path = Path(path)
        self.release_calls = 0
        self.release_error = release_error

    def release(self) -> bool:
        self.release_calls += 1
        if self.release_error is not None:
            raise self.release_error
        return self.release_calls == 1


class ProviderTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.key = PreviewSessionKey("private_login", "private_physical_id")
        self.request = _request(self.key)

    async def _session(
        self,
        handle: FakeCaptureHandle,
        analyzer: FakeAnalyzer | None = None,
        renderer: FakeRenderer | None = None,
        *later_source_results: object,
    ):
        source = FakeSource(_started(handle), *later_source_results)
        analyzer = analyzer or FakeAnalyzer()
        renderer = renderer or FakeRenderer()
        provider = LivePreviewArtifactProvider(source, analyzer, renderer)
        session = await provider.open_session(self.key)
        return session, source, analyzer, renderer


class ProviderOpenTests(ProviderTestCase):
    def test_public_provider_has_the_three_composition_dependencies(self) -> None:
        parameters = tuple(inspect.signature(LivePreviewArtifactProvider).parameters)

        self.assertEqual(parameters, ("source", "analyzer", "renderer"))

    async def test_open_session_opens_source_once_with_physical_key(self) -> None:
        handle = FakeCaptureHandle()
        source = FakeSource(_started(handle))
        provider = LivePreviewArtifactProvider(
            source, FakeAnalyzer(), FakeRenderer()
        )

        session = await provider.open_session(self.key)

        self.assertIsNotNone(session)
        self.assertEqual(
            source.open_calls,
            [("private_login", "private_physical_id")],
        )
        self.assertEqual(handle.close_calls, 0)

    async def test_initial_typed_source_failure_is_sanitized(self) -> None:
        source = FakeSource(
            _not_started(CaptureOutcome(CaptureEndReason.START_FAILED))
        )
        provider = LivePreviewArtifactProvider(
            source, FakeAnalyzer(), FakeRenderer()
        )

        with self.assertRaises(LivePreviewProviderError) as raised:
            await provider.open_session(self.key)

        rendered = "".join(traceback.format_exception(raised.exception))
        self.assertNotIn("private_login", rendered)
        self.assertNotIn("private_physical_id", rendered)
        self.assertEqual(len(source.open_calls), 1)

    async def test_unexpected_source_failure_hides_url_path_and_ids(self) -> None:
        secret = (
            "https://usher.ttvnw.net/private-token "
            "C:/private/stream.ts private_login private_physical_id"
        )
        provider = LivePreviewArtifactProvider(
            FakeSource(RuntimeError(secret)), FakeAnalyzer(), FakeRenderer()
        )

        with self.assertRaises(LivePreviewProviderError) as raised:
            await provider.open_session(self.key)

        rendered = "".join(traceback.format_exception(raised.exception))
        for value in (
            "usher.ttvnw.net",
            "private-token",
            "stream.ts",
            "private_login",
            "private_physical_id",
        ):
            self.assertNotIn(value, rendered)


class ArtifactFlowTests(ProviderTestCase):
    async def test_repeated_refreshes_reuse_handle_and_request_exact_window(self) -> None:
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY),
            SnapshotAcquireResult(SnapshotStatus.EMPTY),
        )
        session, source, analyzer, renderer = await self._session(handle)

        first = await session.create_artifact(self.request)
        second = await session.create_artifact(self.request)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(source.open_calls), 1)
        self.assertEqual(handle.acquire_calls, [90.0, 90.0])
        self.assertEqual(analyzer.calls, [])
        self.assertEqual(renderer.calls, [])

    async def test_empty_snapshot_is_a_normal_refresh(self) -> None:
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, _source, analyzer, renderer = await self._session(handle)

        artifact = await session.create_artifact(self.request)

        self.assertIsNone(artifact)
        self.assertEqual(handle.acquire_calls, [90.0])
        self.assertEqual(analyzer.calls, [])
        self.assertEqual(renderer.calls, [])

    async def test_snapshot_too_short_is_normal_and_released_once(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(AnalysisResult(AnalysisStatus.SNAPSHOT_TOO_SHORT))
        session, source, _analyzer, renderer = await self._session(
            handle, analyzer
        )

        artifact = await session.create_artifact(self.request)

        self.assertIsNone(artifact)
        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(len(source.open_calls), 1)
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(renderer.calls, [])

    async def test_no_selection_is_normal_and_released_once(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(AnalysisResult(AnalysisStatus.NO_SELECTION))
        session, source, _analyzer, renderer = await self._session(
            handle, analyzer
        )

        artifact = await session.create_artifact(self.request)

        self.assertIsNone(artifact)
        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(len(source.open_calls), 1)
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(renderer.calls, [])

    async def test_non_first_preview_does_not_request_fallback(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(AnalysisResult(AnalysisStatus.NO_SELECTION))
        session, _source, analyzer, renderer = await self._session(
            handle, analyzer
        )
        request = _request(self.key, is_first_preview=False)

        artifact = await session.create_artifact(request)

        self.assertIsNone(artifact)
        self.assertEqual(analyzer.include_fallback_calls, [False])
        self.assertEqual(renderer.calls, [])

    async def test_first_preview_requests_fallback_and_no_fallback_found_returns_none(
        self,
    ) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(AnalysisResult(AnalysisStatus.NO_SELECTION))
        session, _source, analyzer, renderer = await self._session(
            handle, analyzer
        )
        request = _request(self.key, is_first_preview=True)

        artifact = await session.create_artifact(request)

        self.assertIsNone(artifact)
        self.assertEqual(analyzer.include_fallback_calls, [True])
        self.assertEqual(renderer.calls, [])
        self.assertEqual(snapshot.release_calls, 1)

    async def test_first_preview_no_selection_with_fallback_renders_via_p6(self) -> None:
        from bot.preview_analysis import HighlightWindow

        snapshot = FakeSnapshot()
        fallback = HighlightWindow(12.0, 5.0, 0.0)
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(
            AnalysisResult(AnalysisStatus.NO_SELECTION, fallback=fallback)
        )
        renderer = FakeRenderer(
            RenderResult(RenderStatus.SUCCESS, artifact=rendered)
        )
        session, _source, analyzer, _renderer = await self._session(
            handle, analyzer, renderer
        )
        request = _request(self.key, is_first_preview=True)

        artifact = await session.create_artifact(request)

        self.assertIsInstance(artifact, LocalVideo)
        self.assertEqual(artifact.path, rendered.path)
        self.assertEqual(analyzer.include_fallback_calls, [True])
        self.assertEqual(len(renderer.calls), 1)
        rendered_snapshot, rendered_selection = renderer.calls[0]
        self.assertIs(rendered_snapshot, snapshot)
        self.assertEqual(rendered_selection.windows, (fallback,))
        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(rendered.release_calls, 0)

    async def test_success_runs_pipeline_once_and_transfers_render_lease(self) -> None:
        snapshot = FakeSnapshot()
        selection = _selection()
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        analyzer = FakeAnalyzer(
            AnalysisResult(AnalysisStatus.SUCCESS, selection=selection)
        )
        renderer = FakeRenderer(
            RenderResult(RenderStatus.SUCCESS, artifact=rendered)
        )
        session, source, _analyzer, _renderer = await self._session(
            handle, analyzer, renderer
        )

        artifact = await session.create_artifact(self.request)

        self.assertIsInstance(artifact, LocalVideo)
        self.assertEqual(artifact.path, rendered.path)
        self.assertEqual(artifact.filename, "preview.mp4")
        self.assertEqual(handle.acquire_calls, [90.0])
        self.assertEqual(analyzer.calls, [snapshot])
        self.assertEqual(renderer.calls, [(snapshot, selection)])
        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(rendered.release_calls, 0)
        self.assertEqual(len(source.open_calls), 1)


class ArtifactOwnershipTests(ProviderTestCase):
    async def _owned_artifact(self):
        snapshot = FakeSnapshot()
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )
        artifact = await session.create_artifact(self.request)
        return session, handle, snapshot, rendered, artifact

    async def test_release_artifact_releases_owner_exactly_once(self) -> None:
        session, _handle, _snapshot, rendered, artifact = (
            await self._owned_artifact()
        )

        await session.release_artifact(artifact)
        await session.release_artifact(artifact)

        self.assertEqual(rendered.release_calls, 1)

    async def test_equal_valued_local_video_cannot_release_owned_lease(self) -> None:
        session, _handle, _snapshot, rendered, artifact = (
            await self._owned_artifact()
        )
        equal_but_foreign = LocalVideo(artifact.path, artifact.filename)
        self.assertEqual(equal_but_foreign, artifact)
        self.assertIsNot(equal_but_foreign, artifact)

        await session.release_artifact(equal_but_foreign)

        self.assertEqual(rendered.release_calls, 0)
        await session.release_artifact(artifact)
        self.assertEqual(rendered.release_calls, 1)

    async def test_foreign_local_and_telegram_artifacts_are_safe_noops(self) -> None:
        handle = FakeCaptureHandle()
        session, _source, _analyzer, _renderer = await self._session(handle)

        await session.release_artifact(LocalVideo("C:/foreign/video.mp4"))
        await session.release_artifact(TelegramVideo("private-file-id"))

        self.assertEqual(handle.close_calls, 0)

    async def test_failure_before_lease_transfer_releases_rendered_owner(self) -> None:
        snapshot = FakeSnapshot()
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )

        with patch(
            "bot.live_preview_provider.LocalVideo",
            side_effect=RuntimeError("C:/private/orphan.mp4"),
        ), self.assertRaises(LivePreviewProviderError) as raised:
            await session.create_artifact(self.request)

        self.assertEqual(rendered.release_calls, 1)
        self.assertEqual(snapshot.release_calls, 1)
        self.assertNotIn("orphan.mp4", str(raised.exception))


class SnapshotCleanupTests(ProviderTestCase):
    async def test_analyzer_failure_releases_snapshot_once_without_reset(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(
                    AnalysisStatus.TIMEOUT, diagnostic_code="analysis_timeout"
                )
            ),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(len(source.open_calls), 1)

    async def test_render_failure_releases_snapshot_once_without_reset(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(
                RenderResult(
                    RenderStatus.TIMEOUT, diagnostic_code="render_timeout"
                )
            ),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(len(source.open_calls), 1)

    async def test_unexpected_analyzer_exception_releases_and_is_sanitized(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(RuntimeError("https://secret/path/private.ts")),
        )

        with self.assertRaises(LivePreviewProviderError) as raised:
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private.ts", str(raised.exception))

    async def test_unexpected_renderer_exception_releases_and_is_sanitized(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RuntimeError("C:/secret/render.mp4")),
        )

        with self.assertRaises(LivePreviewProviderError) as raised:
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("render.mp4", str(raised.exception))

    async def test_analyzer_cancellation_releases_once_and_is_reraised(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle, FakeAnalyzer(asyncio.CancelledError())
        )

        with self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)

    async def test_renderer_cancellation_releases_once_and_is_reraised(self) -> None:
        snapshot = FakeSnapshot()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(asyncio.CancelledError()),
        )

        with self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)

    async def test_transfer_cancellation_releases_render_and_snapshot(self) -> None:
        snapshot = FakeSnapshot()
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )

        with patch(
            "bot.live_preview_provider.LocalVideo",
            side_effect=asyncio.CancelledError(),
        ), self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(rendered.release_calls, 1)
        self.assertEqual(snapshot.release_calls, 1)

    async def test_snapshot_cleanup_failure_does_not_mask_cancellation(self) -> None:
        snapshot = FakeSnapshot(RuntimeError("C:/private/snapshot.ts"))
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle, FakeAnalyzer(asyncio.CancelledError())
        )

        with self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)

    async def test_render_cleanup_failure_does_not_mask_cancellation(self) -> None:
        snapshot = FakeSnapshot()
        rendered = FakeRenderedPreview(
            release_error=RuntimeError("C:/private/orphan.mp4")
        )
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )

        with patch(
            "bot.live_preview_provider.LocalVideo",
            side_effect=asyncio.CancelledError(),
        ), self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(rendered.release_calls, 1)
        self.assertEqual(snapshot.release_calls, 1)

    async def test_snapshot_failure_after_render_releases_rendered_owner(self) -> None:
        snapshot = FakeSnapshot(RuntimeError("C:/private/snapshot.ts"))
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(rendered.release_calls, 1)

    async def test_snapshot_cancellation_after_render_releases_rendered_owner(self) -> None:
        snapshot = FakeSnapshot(asyncio.CancelledError())
        rendered = FakeRenderedPreview()
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        session, _source, _analyzer, _renderer = await self._session(
            handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(RenderResult(RenderStatus.SUCCESS, artifact=rendered)),
        )

        with self.assertRaises(asyncio.CancelledError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(rendered.release_calls, 1)


class FailureMappingTests(ProviderTestCase):
    async def test_recovery_releases_snapshot_before_closing_capture(self) -> None:
        snapshot = FakeSnapshot()
        old_handle = ReleaseAwareCaptureHandle(snapshot)
        new_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, _analyzer, _renderer = await self._session(
            old_handle,
            FakeAnalyzer(
                AnalysisResult(
                    AnalysisStatus.SNAPSHOT_INVALIDATED,
                    diagnostic_code="invalid_snapshot",
                )
            ),
            FakeRenderer(),
            _started(new_handle),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(old_handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 2)

    async def _analysis_recovery(self, result: AnalysisResult) -> None:
        snapshot = FakeSnapshot()
        old_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        new_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, _analyzer, _renderer = await self._session(
            old_handle,
            FakeAnalyzer(result),
            FakeRenderer(),
            _started(new_handle),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(old_handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 2)
        self.assertIsNone(await session.create_artifact(self.request))
        self.assertEqual(new_handle.acquire_calls, [90.0])
        self.assertEqual(len(source.open_calls), 2)

    async def _render_recovery(self, result: RenderResult) -> None:
        snapshot = FakeSnapshot()
        old_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
        )
        new_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, _analyzer, _renderer = await self._session(
            old_handle,
            FakeAnalyzer(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
            ),
            FakeRenderer(result),
            _started(new_handle),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(snapshot.release_calls, 1)
        self.assertEqual(old_handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 2)
        self.assertIsNone(await session.create_artifact(self.request))
        self.assertEqual(new_handle.acquire_calls, [90.0])

    async def test_analysis_snapshot_invalidated_recovers_capture(self) -> None:
        await self._analysis_recovery(
            AnalysisResult(
                AnalysisStatus.SNAPSHOT_INVALIDATED,
                diagnostic_code="invalid_snapshot",
            )
        )

    async def test_analysis_missing_segment_recovers_capture(self) -> None:
        await self._analysis_recovery(
            AnalysisResult(
                AnalysisStatus.PROCESS_FAILED,
                diagnostic_code="missing_segment",
            )
        )

    async def test_analysis_empty_snapshot_recovers_capture(self) -> None:
        await self._analysis_recovery(
            AnalysisResult(
                AnalysisStatus.PROCESS_FAILED,
                diagnostic_code="empty_snapshot",
            )
        )

    async def test_other_analysis_failures_preserve_capture(self) -> None:
        statuses = (
            AnalysisResult(
                AnalysisStatus.PROCESS_FAILED, diagnostic_code="ffmpeg_exit"
            ),
            AnalysisResult(
                AnalysisStatus.TIMEOUT, diagnostic_code="missing_segment"
            ),
            AnalysisResult(
                AnalysisStatus.MALFORMED_METRICS,
                diagnostic_code="invalid_metadata",
            ),
            AnalysisResult(
                AnalysisStatus.INTERNAL_ERROR,
                diagnostic_code="unexpected_error",
            ),
        )
        snapshots = tuple(FakeSnapshot() for _ in statuses)
        handle = FakeCaptureHandle(
            *(
                SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
                for snapshot in snapshots
            )
        )
        session, source, _analyzer, _renderer = await self._session(
            handle, FakeAnalyzer(*statuses)
        )

        for _ in statuses:
            with self.assertRaises(LivePreviewProviderError):
                await session.create_artifact(self.request)

        self.assertTrue(all(snapshot.release_calls == 1 for snapshot in snapshots))
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(len(source.open_calls), 1)

    async def test_render_snapshot_invalidated_recovers_capture(self) -> None:
        await self._render_recovery(
            RenderResult(
                RenderStatus.SNAPSHOT_INVALIDATED,
                diagnostic_code="invalid_snapshot",
            )
        )

    async def test_render_source_file_missing_recovers_capture(self) -> None:
        await self._render_recovery(
            RenderResult(
                RenderStatus.PROCESS_FAILED,
                diagnostic_code="source_file_missing",
            )
        )

    async def test_other_render_failures_preserve_capture(self) -> None:
        statuses = (
            RenderResult(
                RenderStatus.INVALID_SELECTION,
                diagnostic_code="invalid_selection",
            ),
            RenderResult(
                RenderStatus.CAPABILITY_UNAVAILABLE,
                diagnostic_code="capability_unavailable",
            ),
            RenderResult(
                RenderStatus.TIMEOUT,
                diagnostic_code="source_file_missing",
            ),
            RenderResult(
                RenderStatus.OUTPUT_TOO_LARGE,
                diagnostic_code="output_too_large",
            ),
            RenderResult(
                RenderStatus.VALIDATION_FAILED,
                diagnostic_code="invalid_output",
            ),
            RenderResult(
                RenderStatus.INTERNAL_ERROR,
                diagnostic_code="unexpected_error",
            ),
        )
        snapshots = tuple(FakeSnapshot() for _ in statuses)
        handle = FakeCaptureHandle(
            *(
                SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
                for snapshot in snapshots
            )
        )
        analyzer = FakeAnalyzer(
            *(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
                for _ in statuses
            )
        )
        session, source, _analyzer, _renderer = await self._session(
            handle, analyzer, FakeRenderer(*statuses)
        )

        for _ in statuses:
            with self.assertRaises(LivePreviewProviderError):
                await session.create_artifact(self.request)

        self.assertTrue(all(snapshot.release_calls == 1 for snapshot in snapshots))
        self.assertEqual(handle.close_calls, 0)
        self.assertEqual(len(source.open_calls), 1)


class CaptureRecoveryTests(ProviderTestCase):
    async def test_stalled_and_process_exit_each_fresh_resolve_once(self) -> None:
        for reason in (CaptureEndReason.STALLED, CaptureEndReason.PROCESS_EXIT):
            with self.subTest(reason=reason):
                old_handle = FakeCaptureHandle(
                    outcome=CaptureOutcome(reason)
                )
                new_handle = FakeCaptureHandle(
                    SnapshotAcquireResult(SnapshotStatus.EMPTY)
                )
                session, source, _analyzer, _renderer = await self._session(
                    old_handle,
                    None,
                    None,
                    _started(new_handle),
                )

                with self.assertRaises(LivePreviewProviderError):
                    await session.create_artifact(self.request)

                self.assertEqual(old_handle.acquire_calls, [])
                self.assertEqual(old_handle.close_calls, 1)
                self.assertEqual(len(source.open_calls), 2)
                self.assertIsNone(await session.create_artifact(self.request))
                self.assertEqual(new_handle.acquire_calls, [90.0])

    async def test_fresh_resolve_failure_does_not_loop_inside_attempt(self) -> None:
        old_handle = FakeCaptureHandle(
            outcome=CaptureOutcome(CaptureEndReason.PROCESS_EXIT)
        )
        source = FakeSource(
            _started(old_handle),
            _not_started(CaptureOutcome(CaptureEndReason.START_FAILED)),
            _started(FakeCaptureHandle()),
        )
        provider = LivePreviewArtifactProvider(
            source, FakeAnalyzer(), FakeRenderer()
        )
        session = await provider.open_session(self.key)

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(len(source.open_calls), 2)

    async def test_wait_for_resource_defers_open_until_next_p3_attempt(self) -> None:
        old_handle = FakeCaptureHandle(
            outcome=CaptureOutcome(CaptureEndReason.DISK_PRESSURE)
        )
        new_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, _analyzer, _renderer = await self._session(
            old_handle,
            None,
            None,
            _started(new_handle),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(old_handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 1)
        self.assertIsNone(await session.create_artifact(self.request))
        self.assertEqual(len(source.open_calls), 2)
        self.assertEqual(new_handle.acquire_calls, [90.0])

    async def test_closing_snapshot_waits_for_outcome_then_applies_retry_action(self) -> None:
        old_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.CLOSING),
            close_outcome=CaptureOutcome(CaptureEndReason.DISK_PRESSURE),
        )
        new_handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, _analyzer, _renderer = await self._session(
            old_handle,
            None,
            None,
            _started(new_handle),
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)

        self.assertEqual(old_handle.wait_calls, 1)
        self.assertEqual(old_handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 1)

    async def test_stop_outcome_makes_session_dormant(self) -> None:
        handle = FakeCaptureHandle(
            outcome=CaptureOutcome(CaptureEndReason.INTERNAL_ERROR)
        )
        session, source, _analyzer, _renderer = await self._session(handle)

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(self.request)
        later = await session.create_artifact(self.request)

        self.assertIsNone(later)
        self.assertEqual(handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 1)


class SessionBoundaryTests(ProviderTestCase):
    async def test_mismatched_generation_is_rejected_before_capture_use(self) -> None:
        handle = FakeCaptureHandle()
        session, _source, _analyzer, _renderer = await self._session(handle)
        mismatch = PreviewArtifactRequest(
            generation=PreviewGeneration(
                "other_login", "other_physical_id", 9
            ),
            observation=PreviewObservation(
                "other_login",
                True,
                "other_physical_id",
                "title",
                "game",
                1,
                None,
            ),
        )

        with self.assertRaises(LivePreviewProviderError) as raised:
            await session.create_artifact(mismatch)

        self.assertEqual(handle.acquire_calls, [])
        self.assertNotIn("other_login", str(raised.exception))
        self.assertNotIn("other_physical_id", str(raised.exception))

    async def test_mismatched_observation_is_rejected_before_capture_use(self) -> None:
        handle = FakeCaptureHandle()
        session, _source, _analyzer, _renderer = await self._session(handle)
        mismatch = _request(
            self.key,
            observation_login="other_login",
            observation_physical_id="other_physical_id",
        )

        with self.assertRaises(LivePreviewProviderError):
            await session.create_artifact(mismatch)

        self.assertEqual(handle.acquire_calls, [])

    async def test_close_releases_all_leases_and_closes_handle_once(self) -> None:
        snapshots = (FakeSnapshot(), FakeSnapshot())
        rendered = (FakeRenderedPreview(), FakeRenderedPreview())
        handle = FakeCaptureHandle(
            *(
                SnapshotAcquireResult(SnapshotStatus.READY, snapshot)
                for snapshot in snapshots
            )
        )
        analyzer = FakeAnalyzer(
            *(
                AnalysisResult(AnalysisStatus.SUCCESS, selection=_selection())
                for _ in snapshots
            )
        )
        renderer = FakeRenderer(
            *(RenderResult(RenderStatus.SUCCESS, artifact=item) for item in rendered)
        )
        session, source, _analyzer, _renderer = await self._session(
            handle, analyzer, renderer
        )
        await session.create_artifact(self.request)
        await session.create_artifact(self.request)

        await session.close()
        await session.close()

        self.assertEqual(tuple(item.release_calls for item in rendered), (1, 1))
        self.assertEqual(tuple(item.release_calls for item in snapshots), (1, 1))
        self.assertEqual(handle.close_calls, 1)
        self.assertEqual(len(source.open_calls), 1)

    async def test_closed_session_produces_no_more_useful_artifacts(self) -> None:
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, source, analyzer, renderer = await self._session(handle)

        await session.close()
        artifact = await session.create_artifact(self.request)

        self.assertIsNone(artifact)
        self.assertEqual(handle.acquire_calls, [])
        self.assertEqual(analyzer.calls, [])
        self.assertEqual(renderer.calls, [])
        self.assertEqual(len(source.open_calls), 1)

    async def test_provider_creates_no_scheduler_or_background_task(self) -> None:
        current = asyncio.current_task()
        before = {task for task in asyncio.all_tasks() if task is not current}
        handle = FakeCaptureHandle(
            SnapshotAcquireResult(SnapshotStatus.EMPTY)
        )
        session, _source, _analyzer, _renderer = await self._session(handle)

        await session.create_artifact(self.request)
        await asyncio.sleep(0)
        after = {task for task in asyncio.all_tasks() if task is not current}

        self.assertEqual(after, before)

    def test_provider_has_no_internal_timer_or_retry_loop(self) -> None:
        import bot.live_preview_provider as module

        source = inspect.getsource(module)

        self.assertNotIn("asyncio.sleep(", source)
        self.assertNotIn("create_task(", source)
        self.assertNotIn("while ", source)

    def test_provider_does_not_touch_database_or_telegram_updater(self) -> None:
        import bot.live_preview_provider as module

        self.assertFalse(hasattr(module, "Database"))
        self.assertFalse(hasattr(module, "LivePostUpdater"))

    def test_provider_module_exposes_no_new_result_or_status_type(self) -> None:
        import bot.live_preview_provider as module

        public_types = {
            name
            for name, value in vars(module).items()
            if not name.startswith("_")
            and inspect.isclass(value)
            and value.__module__ == module.__name__
        }

        self.assertEqual(
            public_types,
            {"LivePreviewArtifactProvider", "LivePreviewProviderError"},
        )


if __name__ == "__main__":
    unittest.main()
