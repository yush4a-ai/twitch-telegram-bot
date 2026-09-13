from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path

from bot.preview_capture import SegmentRecord


def _modules():
    try:
        return (
            importlib.import_module("bot.preview_analysis"),
            importlib.import_module("bot.preview_analysis.metrics"),
            importlib.import_module("bot.preview_analysis.process"),
        )
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 analyzer service must exist") from exc


class FakeSnapshot:
    def __init__(self, records, duration: float | None = None) -> None:
        self.records = tuple(records)
        self.actual_duration_seconds = duration if duration is not None else sum(r.duration_seconds for r in records)
        self.valid = True
        self.ensure_calls = 0
        self.release_calls = 0

    def ensure_valid(self) -> None:
        self.ensure_calls += 1
        if not self.valid:
            from bot.preview_capture import SnapshotInvalidated
            raise SnapshotInvalidated("invalid")

    def release(self) -> None:
        self.release_calls += 1
        self.valid = False


def _snapshot(parent: Path, seconds: int = 30) -> FakeSnapshot:
    records = []
    for sequence in range(seconds // 2):
        path = parent / f"segment-{sequence:09d}.ts"
        path.write_bytes(b"\x47" + b"x" * 187)
        records.append(SegmentRecord(sequence, path, 2.0, 188, float(sequence)))
    return FakeSnapshot(records, float(seconds))


class FakeExecutor:
    def __init__(self, results, *, fingerprint_seconds: int = 30) -> None:
        self.results = list(results)
        self.calls = []
        self.fingerprint_seconds = fingerprint_seconds
        self.active_process_count = 0
        self.background_task_count = 0

    async def run(
        self,
        argv,
        *,
        cwd,
        parser,
        is_valid,
        timeout,
        output_guard=None,
    ):
        process = importlib.import_module("bot.preview_analysis.process")
        self.calls.append(tuple(argv))
        if not is_valid():
            raise process.AnalysisSnapshotInvalidated
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        for value in argv:
            if str(value).endswith("fingerprints.gray"):
                Path(value).write_bytes(
                    b"".join(bytes([index % 256]) * 144 for index in range(self.fingerprint_seconds))
                )
        return process.ProcessRunResult(0, result)


def _visual(metrics, seconds: int = 30):
    samples = []
    for sec in range(seconds):
        activity = 12.0 if 10 <= sec < 13 else 1.0
        samples.extend(
            metrics.VisualSample(sec + frame / 10, activity, 0.0, 0.0)
            for frame in range(10)
        )
    return metrics.VisualMetrics(tuple(samples))


class HighlightAnalyzerTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_snapshot_returns_typed_status_before_spawn(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            snapshot = _snapshot(Path(raw), 28)
            executor = FakeExecutor(())
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=Path(raw) / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.SNAPSHOT_TOO_SHORT)
        self.assertEqual(executor.calls, [])
        self.assertEqual(snapshot.release_calls, 0)

    async def test_audio_less_success_is_success_and_uses_two_separate_passes(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = FakeExecutor((_visual(metrics), metrics.AudioMetrics(())))
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
            self.assertEqual(result.status, analysis.AnalysisStatus.SUCCESS)
            self.assertEqual(len(executor.calls), 2)
            visual, audio = executor.calls
            self.assertIn("rawvideo", visual)
            self.assertTrue(any(str(value).endswith("fingerprints.gray") for value in visual))
            self.assertFalse(any(str(value).endswith("fingerprints.gray") for value in audio))
            self.assertTrue(all("fingerprint" not in str(value).lower() for value in audio))
            self.assertEqual(list((root / "jobs").glob("analysis-*")), [])
        self.assertEqual(snapshot.release_calls, 0)

    async def test_audio_process_failure_is_not_treated_as_audio_less(self) -> None:
        analysis, metrics, process = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = FakeExecutor((_visual(metrics), process.AnalysisProcessFailure("audio")))
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.PROCESS_FAILED)
        self.assertEqual(result.selection.windows, ())
        self.assertEqual(snapshot.release_calls, 0)

    async def test_malformed_fingerprint_is_typed_and_temp_is_cleaned(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = FakeExecutor((_visual(metrics),), fingerprint_seconds=29)
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
            self.assertEqual(result.status, analysis.AnalysisStatus.MALFORMED_METRICS)
            self.assertEqual(list((root / "jobs").glob("analysis-*")), [])
            self.assertTrue(all(record.path.exists() for record in snapshot.records))
        self.assertEqual(snapshot.release_calls, 0)

    async def test_nonzero_exit_is_process_failed_with_allowlisted_diagnostic(self) -> None:
        analysis, metrics, process = _modules()
        executor = FakeExecutor(())

        async def fail(*args, **kwargs):
            raise process.AnalysisProcessFailure("ffmpeg_exit")

        executor.run = fail
        with tempfile.TemporaryDirectory() as raw:
            snapshot = _snapshot(Path(raw), 30)
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=Path(raw) / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.PROCESS_FAILED)
        self.assertEqual(result.diagnostic_code, "ffmpeg_exit")
        self.assertEqual(snapshot.release_calls, 0)

    async def test_timeout_is_typed_and_never_releases_snapshot(self) -> None:
        analysis, _, process = _modules()
        executor = FakeExecutor((process.AnalysisProcessTimeout(),))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
            self.assertEqual(result.status, analysis.AnalysisStatus.TIMEOUT)
            self.assertEqual(list((root / "jobs").glob("analysis-*")), [])
        self.assertEqual(snapshot.release_calls, 0)

    async def test_invalidation_between_passes_is_typed(self) -> None:
        analysis, metrics, process = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)

            class InvalidatingExecutor(FakeExecutor):
                async def run(self, *args, **kwargs):
                    result = await super().run(*args, **kwargs)
                    snapshot.valid = False
                    return result

            executor = InvalidatingExecutor((_visual(metrics),))
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.SNAPSHOT_INVALIDATED)
        self.assertEqual(snapshot.release_calls, 0)

        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "segment-000000000.ts"
            record = SegmentRecord(0, missing, 30.0, 188, 0.0)

            class InvalidatedDuringValidation:
                records = (record,)
                actual_duration_seconds = 30.0
                valid = False
                release_calls = 0

                def ensure_valid(self):
                    return None

                def release(self):
                    self.release_calls += 1

            raced = InvalidatedDuringValidation()
            result = await analysis.HighlightAnalyzer(
                temp_root=Path(raw) / "jobs"
            ).analyze(raced)
        self.assertEqual(result.status, analysis.AnalysisStatus.SNAPSHOT_INVALIDATED)
        self.assertEqual(raced.release_calls, 0)

    async def test_cancellation_reraises_after_cleanup_without_release(self) -> None:
        analysis, _, _ = _modules()
        started = asyncio.Event()
        blocker = asyncio.Event()

        class BlockingExecutor(FakeExecutor):
            async def run(self, *args, **kwargs):
                started.set()
                await blocker.wait()

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = BlockingExecutor(())
            analyzer = analysis.HighlightAnalyzer(executor=executor, temp_root=root / "jobs")
            task = asyncio.create_task(analyzer.analyze(snapshot))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(list((root / "jobs").glob("analysis-*")), [])
        self.assertEqual(snapshot.release_calls, 0)

    async def test_unexpected_exception_is_sanitized_internal_error(self) -> None:
        analysis, _, _ = _modules()
        executor = FakeExecutor((RuntimeError("C:\\secret\\raw stderr"),))
        with tempfile.TemporaryDirectory() as raw:
            snapshot = _snapshot(Path(raw), 30)
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=Path(raw) / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.INTERNAL_ERROR)
        self.assertEqual(result.diagnostic_code, "unexpected_error")
        self.assertNotIn("secret", repr(result))
        self.assertEqual(snapshot.release_calls, 0)

    async def test_fixed_argv_contains_safe_concat_and_approved_filters(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = FakeExecutor((_visual(metrics), metrics.AudioMetrics(())))
            await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
        visual, audio = executor.calls
        for argv in (visual, audio):
            self.assertEqual(argv[argv.index("-safe") + 1], "0")
            self.assertEqual(argv[argv.index("-protocol_whitelist") + 1], "file")
            self.assertNotIn("-filter_script", argv)
        graph = visual[visual.index("-filter_complex") + 1]
        for token in ("setpts=PTS-STARTPTS", "fps=10", "scale=320:180:flags=area", "signalstats", "scdet=t=10", "blackframe=amount=0:threshold=32", "freezedetect", "pipe\\:1"):
            self.assertIn(token, graph)
        self.assertIn("0:a:0?", audio)
        self.assertIn("0:v:0", audio)
        self.assertIn("asetpts=PTS-STARTPTS", audio[audio.index("-af") + 1])

    async def test_flat_valid_stream_returns_no_selection_without_fallback(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            flat = metrics.VisualMetrics(
                tuple(
                    metrics.VisualSample(sec + frame / 10, 1.0, 0.0, 0.0)
                    for sec in range(30)
                    for frame in range(10)
                )
            )
            executor = FakeExecutor((flat, metrics.AudioMetrics(())))
            result = await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
        self.assertEqual(result.status, analysis.AnalysisStatus.NO_SELECTION)
        self.assertEqual(result.selection.windows, ())

    async def test_validity_is_checked_at_every_phase_boundary(self) -> None:
        analysis, metrics, _ = _modules()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = _snapshot(root, 30)
            executor = FakeExecutor((_visual(metrics), metrics.AudioMetrics(())))
            await analysis.HighlightAnalyzer(
                executor=executor, temp_root=root / "jobs"
            ).analyze(snapshot)
        self.assertGreaterEqual(snapshot.ensure_calls, 7)
        self.assertEqual(snapshot.release_calls, 0)


if __name__ == "__main__":
    unittest.main()
