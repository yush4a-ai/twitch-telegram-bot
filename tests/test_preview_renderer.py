from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from bot.preview_analysis import HighlightSelection, HighlightWindow
from bot.preview_capture import SegmentRecord


def _renderer():
    try:
        return importlib.import_module("bot.preview_render.renderer")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 PreviewRenderer must exist") from exc


def _storage():
    try:
        return importlib.import_module("bot.preview_render.storage")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render storage must exist") from exc


def _models():
    try:
        return importlib.import_module("bot.preview_render.models")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render models must exist") from exc


class FakeSnapshot:
    def __init__(self, records) -> None:
        self.records = tuple(records)
        self.actual_duration_seconds = math.fsum(x.duration_seconds for x in records)
        self.valid = True
        self.release_calls = 0

    def ensure_valid(self) -> None:
        if not self.valid:
            raise RuntimeError("invalid")

    def release(self) -> None:
        self.release_calls += 1


class FakeReader:
    async def read(self, _size: int = -1) -> bytes:
        await asyncio.sleep(0)
        return b""


class FakeProcess:
    def __init__(self, code: int = 0) -> None:
        self.pid = 8123
        self.stderr = FakeReader()
        self.returncode = code

    async def wait(self) -> int:
        return self.returncode

    def send_signal(self, _signal: int) -> None:
        self.returncode = -2

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class BlockingProcess:
    def __init__(self) -> None:
        self.pid = 8124
        self.stderr = FakeReader()
        self.returncode = None
        self._finished = asyncio.Event()

    async def wait(self) -> int:
        await self._finished.wait()
        assert self.returncode is not None
        return self.returncode

    def _finish(self, code: int) -> None:
        self.returncode = code
        self._finished.set()

    def send_signal(self, _signal: int) -> None:
        self._finish(-2)

    def terminate(self) -> None:
        self._finish(-15)

    def kill(self) -> None:
        self._finish(-9)


def _source_json(rate: str = "30/1") -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "width": 640,
                    "height": 360,
                    "pix_fmt": "yuv420p",
                    "field_order": "progressive",
                    "sample_aspect_ratio": "1:1",
                    "avg_frame_rate": rate,
                    "r_frame_rate": rate,
                    "color_transfer": "bt709",
                }
            ]
        }
    ).encode()


def _output_json(rate: str = "30/1", duration: float = 3.0, size: int = 1024) -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 854,
                    "height": 480,
                    "pix_fmt": "yuv420p",
                    "sample_aspect_ratio": "1:1",
                    "avg_frame_rate": rate,
                    "r_frame_rate": rate,
                }
            ],
            "format": {"duration": str(duration), "size": str(size)},
        }
    ).encode()


class ScriptedRunner:
    def __init__(
        self,
        *,
        snapshot: FakeSnapshot | None = None,
        source_rate: str = "30/1",
        output_rate: str = "30/1",
        output_duration: float = 3.0,
        output_size: int = 1024,
        invalid_output: bool = False,
        process_code: int = 0,
        delete_source_on_spawn: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.source_rate = source_rate
        self.output_rate = output_rate
        self.output_duration = output_duration
        self.output_size = output_size
        self.invalid_output = invalid_output
        self.process_code = process_code
        self.delete_source_on_spawn = delete_source_on_spawn
        self.commands = []
        self.spawns = []

    async def run_command(self, argv, *, timeout: float, max_output: int):
        command = tuple(argv)
        self.commands.append(command)
        if "-version" in command:
            product = "ffprobe" if "ffprobe" in command[0].lower() else "ffmpeg"
            return 0, f"{product} version 9.0-test\n".encode()
        if "-encoders" in command:
            return 0, b" V..... libx264 H.264\n"
        if "-muxers" in command:
            return 0, b" E  mp4 MP4\n"
        if "-filters" in command:
            return 0, b"\n".join(
                f" ... {name} test".encode()
                for name in ("trim", "setpts", "concat", "scale", "pad", "setsar", "format", "fps")
            )
        target = Path(command[-1])
        if target.name == "preview.tmp.mp4":
            self.assert_final_absent(target)
            payload = json.loads(_output_json(self.output_rate, self.output_duration, self.output_size))
            if self.invalid_output:
                payload["streams"][0]["codec_name"] = "hevc"
            return 0, json.dumps(payload).encode()
        return 0, _source_json(self.source_rate)

    def assert_final_absent(self, temporary: Path) -> None:
        if (temporary.parent / "preview.mp4").exists():
            raise AssertionError("preview.mp4 was exposed before validation")

    async def spawn(self, argv, *, cwd: Path):
        command = tuple(argv)
        self.spawns.append(command)
        if self.delete_source_on_spawn and self.snapshot is not None:
            self.snapshot.records[0].path.unlink()
        Path(command[-1]).write_bytes(b"m" * self.output_size)
        if self.snapshot is not None and not self.snapshot.valid:
            return FakeProcess(self.process_code)
        return FakeProcess(self.process_code)


class BlockingRunner(ScriptedRunner):
    def __init__(self, *, snapshot: FakeSnapshot) -> None:
        super().__init__(snapshot=snapshot)
        self.process: BlockingProcess | None = None

    async def spawn(self, argv, *, cwd: Path):
        command = tuple(argv)
        self.spawns.append(command)
        Path(command[-1]).write_bytes(b"partial")
        self.process = BlockingProcess()
        return self.process


def _segment(parent: Path, sequence: int, duration: float = 3.0) -> SegmentRecord:
    path = parent / f"segment-{sequence:09d}.ts"
    path.write_bytes(b"\x47" + b"s" * 187)
    return SegmentRecord(sequence, path, duration, path.stat().st_size, float(sequence))


class FiltergraphTests(unittest.TestCase):
    def test_24_30_60_have_no_fps_filter_and_high_rate_has_exactly_one_cap(self) -> None:
        renderer = _renderer()
        models = _models()
        plans = (
            type("Plan", (), {"local_start_seconds": 0.25, "duration_seconds": 3.0})(),
        )
        for rate in (Fraction(24), Fraction(30), Fraction(60), Fraction(30000, 1001), Fraction(60000, 1001)):
            graph = renderer.build_filtergraph(plans, rate, models.RenderConfig())
            with self.subTest(rate=rate):
                self.assertNotIn("fps=", graph)
        graph = renderer.build_filtergraph(plans, Fraction(120), models.RenderConfig())
        self.assertEqual(graph.count("fps=60"), 1)

    def test_each_branch_has_exact_trim_geometry_and_hard_concat(self) -> None:
        renderer = _renderer()
        config = _models().RenderConfig()
        plans = tuple(
            type(
                "Plan",
                (),
                {"local_start_seconds": start, "duration_seconds": duration},
            )()
            for start, duration in ((0.25, 3.0), (1.5, 2.5), (0.0, 1.0))
        )
        graph = renderer.build_filtergraph(plans, Fraction(30), config)
        self.assertEqual(graph.count("settb=AVTB,setpts=PTS-STARTPTS,trim="), 3)
        self.assertIn("trim=start=0.25:duration=3", graph)
        self.assertIn("trim=start=1.5:duration=2.5", graph)
        self.assertEqual(graph.count("scale=854:480:force_original_aspect_ratio=decrease"), 3)
        self.assertEqual(graph.count("force_divisible_by=2"), 3)
        self.assertEqual(graph.count("pad=854:480:(ow-iw)/2:(oh-ih)/2"), 3)
        self.assertEqual(graph.count("setsar=1,format=yuv420p"), 3)
        self.assertIn("concat=n=3:v=1:a=0", graph)
        self.assertNotIn("fade", graph)

    def test_encode_argv_has_one_process_h264_limits_and_no_audio_metadata_or_rate_override(self) -> None:
        renderer = _renderer()
        config = _models().RenderConfig()
        plans = tuple(
            type("Plan", (), {"local_start_seconds": 0.0, "duration_seconds": 3.0})()
            for _ in range(2)
        )
        argv = renderer.build_render_argv(
            "ffmpeg",
            (Path("one.ffconcat"), Path("two.ffconcat")),
            plans,
            Fraction(30),
            Path("preview.tmp.mp4"),
            config,
        )
        joined = " ".join(argv)
        for token in (
            "-c:v libx264",
            "-preset veryfast",
            "-crf 22",
            "-maxrate 6M",
            "-bufsize 12M",
            "-pix_fmt yuv420p",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata -1",
            "-map_chapters -1",
            "-movflags +faststart",
        ):
            self.assertIn(token, joined)
        self.assertNotIn(" -r ", f" {joined} ")
        self.assertNotIn("minterpolate", joined)
        self.assertEqual(argv.count("-i"), 2)


class RendererLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_publishes_only_after_validation_and_release_preserves_source(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            segment = _segment(source, 1)
            before = segment.path.read_bytes()
            snapshot = FakeSnapshot((segment,))
            runner = ScriptedRunner(snapshot=snapshot)
            service = renderer_module.PreviewRenderer.create(
                ffmpeg_executable="ffmpeg",
                ffprobe_executable="ffprobe",
                temp_root=root / "render-root",
                runner=runner,
            )
            result = await service.render(
                snapshot,
                HighlightSelection((HighlightWindow(0, 3, 0.9),)),
            )
            self.assertEqual(result.status, models.RenderStatus.SUCCESS)
            artifact = result.artifact
            self.assertEqual(artifact.path.name, "preview.mp4")
            self.assertTrue(artifact.path.is_file())
            self.assertEqual(artifact.fps, Fraction(30))
            self.assertEqual(snapshot.release_calls, 0)
            self.assertTrue(artifact.release())
            self.assertFalse(artifact.release())
            self.assertFalse(artifact.path.exists())
            self.assertEqual(segment.path.read_bytes(), before)
            self.assertEqual(snapshot.release_calls, 0)

    async def test_invalid_selection_happens_before_temp_or_binary_calls(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            snapshot = FakeSnapshot((_segment(source, 1),))
            runner = ScriptedRunner()
            service = renderer_module.PreviewRenderer.create(
                ffmpeg_executable="ffmpeg",
                ffprobe_executable="ffprobe",
                temp_root=root / "render-root",
                runner=runner,
            )
            result = await service.render(snapshot, HighlightSelection())
            self.assertEqual(result.status, models.RenderStatus.INVALID_SELECTION)
            self.assertFalse((root / "render-root").exists())
            self.assertEqual(runner.commands, [])
            self.assertEqual(runner.spawns, [])
            self.assertEqual(snapshot.release_calls, 0)

    async def test_failed_validation_never_exposes_final_and_cleans_partial_temp(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            snapshot = FakeSnapshot((_segment(source, 1),))
            runner = ScriptedRunner(snapshot=snapshot, invalid_output=True)
            service = renderer_module.PreviewRenderer.create(
                ffmpeg_executable="ffmpeg",
                ffprobe_executable="ffprobe",
                temp_root=root / "render-root",
                runner=runner,
            )
            result = await service.render(
                snapshot, HighlightSelection((HighlightWindow(0, 3, 0.9),))
            )
            self.assertEqual(result.status, models.RenderStatus.VALIDATION_FAILED)
            self.assertIsNone(result.artifact)
            self.assertFalse(tuple((root / "render-root").glob("render-*")))
            self.assertEqual(snapshot.release_calls, 0)

    async def test_oversize_process_failure_and_invalidation_clean_jobs_without_releasing_snapshot(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            segment = _segment(source, 1)
            cases = (
                (ScriptedRunner(output_size=17), models.RenderConfig(max_output_bytes=16), models.RenderStatus.OUTPUT_TOO_LARGE),
                (ScriptedRunner(process_code=5), models.RenderConfig(), models.RenderStatus.PROCESS_FAILED),
            )
            for runner, config, status in cases:
                snapshot = FakeSnapshot((segment,))
                service = renderer_module.PreviewRenderer.create(
                    ffmpeg_executable="ffmpeg",
                    ffprobe_executable="ffprobe",
                    config=config,
                    temp_root=root / f"render-{status.value}",
                    runner=runner,
                )
                result = await service.render(
                    snapshot,
                    HighlightSelection((HighlightWindow(0, 3, 0.9),)),
                )
                with self.subTest(status=status):
                    self.assertEqual(result.status, status)
                    self.assertEqual(snapshot.release_calls, 0)
                    self.assertEqual(service.active_process_count, 0)
                    self.assertEqual(service.background_task_count, 0)
                    self.assertFalse(
                        tuple((root / f"render-{status.value}").glob("render-*"))
                    )

    async def test_source_disappearance_during_encode_is_typed_and_never_releases_snapshot(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            snapshot = FakeSnapshot((_segment(source, 1),))
            runner = ScriptedRunner(
                snapshot=snapshot,
                process_code=5,
                delete_source_on_spawn=True,
            )
            service = renderer_module.PreviewRenderer.create(
                ffmpeg_executable="ffmpeg",
                ffprobe_executable="ffprobe",
                temp_root=root / "render-root",
                runner=runner,
            )
            result = await service.render(
                snapshot, HighlightSelection((HighlightWindow(0, 3, 0.9),))
            )
            self.assertEqual(result.status, models.RenderStatus.PROCESS_FAILED)
            self.assertEqual(result.diagnostic_code, "source_file_missing")
            self.assertEqual(snapshot.release_calls, 0)
            self.assertEqual(service.active_process_count, 0)
            self.assertEqual(service.background_task_count, 0)
            self.assertFalse(tuple((root / "render-root").glob("render-*")))

    async def test_cleanup_failure_cannot_mask_cancellation_or_process_reaping(self) -> None:
        renderer_module = _renderer()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            snapshot = FakeSnapshot((_segment(source, 1),))
            runner = BlockingRunner(snapshot=snapshot)
            service = renderer_module.PreviewRenderer.create(
                ffmpeg_executable="ffmpeg",
                ffprobe_executable="ffprobe",
                config=models.RenderConfig(output_poll_seconds=0.1),
                temp_root=root / "render-root",
                runner=runner,
            )
            task = asyncio.create_task(
                service.render(
                    snapshot,
                    HighlightSelection((HighlightWindow(0, 3, 0.9),)),
                )
            )
            for _ in range(100):
                if service.active_process_count:
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(service.active_process_count, 1)
            with patch(
                "bot.preview_render.storage.shutil.rmtree",
                side_effect=PermissionError("busy"),
            ):
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(runner.process)
            self.assertIsNotNone(runner.process.returncode)
            self.assertEqual(service.active_process_count, 0)
            self.assertEqual(service.background_task_count, 0)
            self.assertEqual(snapshot.release_calls, 0)


class RenderStorageTests(unittest.TestCase):
    def test_orphan_cleanup_is_stale_marker_owned_and_cannot_escape_root(self) -> None:
        storage = _storage()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "renderer"
            now = [0.0]
            manager = storage.RenderStorage(root=root, clock=lambda: now[0])
            stale = manager.create_job()
            foreign = root / "foreign"
            foreign.mkdir()
            bad = root / ("render-" + "1" * 32)
            bad.mkdir()
            (bad / storage.JOB_MARKER).write_text("tampered", encoding="utf-8")
            outside = Path(raw) / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            link = root / ("render-" + "2" * 32)
            if hasattr(os, "symlink"):
                try:
                    link.symlink_to(outside, target_is_directory=True)
                except OSError:
                    pass

            now[0] = storage.ORPHAN_TTL_SECONDS + 1
            self.assertEqual(manager.cleanup_orphans(), 1)
            self.assertFalse(stale.path.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(bad.exists())
            self.assertTrue(sentinel.exists())

    def test_release_requires_verified_owned_job_and_is_idempotent(self) -> None:
        storage = _storage()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            manager = storage.RenderStorage(root=Path(raw) / "renderer")
            job = manager.create_job()
            job.temporary_path.write_bytes(b"mp4")
            os.replace(job.temporary_path, job.final_path)
            preview = storage.RenderedPreview.from_job(
                job,
                models.RenderedPreviewMetadata(
                    3.0, 854, 480, Fraction(30), 3
                ),
            )
            self.assertTrue(preview.release())
            self.assertFalse(preview.release())

    def test_delete_failure_is_contained_and_release_remains_retryable_bool(self) -> None:
        storage = _storage()
        models = _models()
        with tempfile.TemporaryDirectory() as raw:
            manager = storage.RenderStorage(root=Path(raw) / "renderer")
            job = manager.create_job()
            job.final_path.write_bytes(b"mp4")
            preview = storage.RenderedPreview.from_job(
                job,
                models.RenderedPreviewMetadata(
                    3.0, 854, 480, Fraction(30), 3
                ),
            )
            with patch(
                "bot.preview_render.storage.shutil.rmtree",
                side_effect=PermissionError("busy"),
            ):
                self.assertFalse(job.cleanup())
                self.assertFalse(preview.release())
            self.assertTrue(job.path.exists())
            self.assertTrue(preview.release())


if __name__ == "__main__":
    unittest.main()
