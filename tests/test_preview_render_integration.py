from __future__ import annotations

import asyncio
import importlib
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from bot.preview_analysis import HighlightSelection, HighlightWindow
from bot.preview_capture import SegmentRecord


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _package():
    try:
        return importlib.import_module("bot.preview_render")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 preview renderer must exist") from exc


class RealSnapshot:
    def __init__(self, records) -> None:
        self.records = tuple(records)
        self.actual_duration_seconds = sum(x.duration_seconds for x in records)
        self.valid = True
        self.release_calls = 0

    def ensure_valid(self) -> None:
        if not self.valid:
            raise RuntimeError("snapshot invalidated")

    def release(self) -> None:
        self.release_calls += 1


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg and ffprobe are required")
class RealPreviewRenderTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.rate_snapshots = {}
        for index, rate in enumerate(
            ("24/1", "30/1", "30000/1001", "60/1", "60000/1001", "120/1")
        ):
            parent = cls.root / f"rate-{index}"
            parent.mkdir()
            record = cls._make_segment(parent, 0, rate, "red")
            cls.rate_snapshots[rate] = RealSnapshot((record,))

        scene_parent = cls.root / "scenes"
        scene_parent.mkdir()
        cls.scene_snapshot = RealSnapshot(
            tuple(
                cls._make_segment(scene_parent, index, "30/1", color)
                for index, color in enumerate(("red", "green", "blue"))
            )
        )
        odd_parent = cls.root / "odd-aspect"
        odd_parent.mkdir()
        cls.odd_snapshot = RealSnapshot(
            (cls._make_segment(odd_parent, 0, "30/1", "red", size="1000x720"),)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @classmethod
    def _make_segment(
        cls,
        parent: Path,
        sequence: int,
        rate: str,
        color: str,
        *,
        size: str = "320x240",
    ) -> SegmentRecord:
        assert FFMPEG is not None
        path = parent / f"segment-{sequence:09d}.ts"
        frames_per_gop = max(1, round(float(Fraction(rate)) * 3))
        command = (
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:size={size}:rate={rate}:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:d=3",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-g",
            str(frames_per_gop),
            "-keyint_min",
            str(frames_per_gop),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-f",
            "mpegts",
            str(path),
        )
        completed = subprocess.run(command, capture_output=True, timeout=30)
        if completed.returncode:
            raise AssertionError(completed.stderr.decode(errors="replace"))
        return SegmentRecord(sequence, path, 3.0, path.stat().st_size, float(sequence))

    @staticmethod
    def _atom_order(path: Path) -> tuple[bytes, ...]:
        data = path.read_bytes()
        atoms = []
        offset = 0
        while offset + 8 <= len(data):
            size = int.from_bytes(data[offset : offset + 4], "big")
            kind = data[offset + 4 : offset + 8]
            if size == 1 and offset + 16 <= len(data):
                size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            elif size == 0:
                size = len(data) - offset
            if size < 8 or offset + size > len(data):
                break
            atoms.append(kind)
            offset += size
        return tuple(atoms)

    @staticmethod
    def _pixel(path: Path, timestamp: float, x: int, y: int) -> tuple[int, int, int]:
        assert FFMPEG is not None
        completed = subprocess.run(
            (
                FFMPEG,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(timestamp),
                "-i",
                str(path),
                "-vf",
                f"crop=1:1:{x}:{y},format=rgb24",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-",
            ),
            capture_output=True,
            timeout=15,
        )
        if completed.returncode or len(completed.stdout) != 3:
            raise AssertionError(completed.stderr.decode(errors="replace"))
        return tuple(completed.stdout)

    @staticmethod
    def _content_bounds(path: Path, timestamp: float) -> tuple[int, int]:
        assert FFMPEG is not None
        completed = subprocess.run(
            (
                FFMPEG,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(timestamp),
                "-i",
                str(path),
                "-vf",
                "crop=854:2:0:238,format=rgb24",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-",
            ),
            capture_output=True,
            timeout=15,
        )
        expected = 854 * 2 * 3
        if completed.returncode or len(completed.stdout) != expected:
            raise AssertionError(completed.stderr.decode(errors="replace"))
        row = completed.stdout[: 854 * 3]
        content = [
            x for x in range(854) if max(row[x * 3 : x * 3 + 3]) > 50
        ]
        if not content:
            raise AssertionError("rendered row has no visible content")
        return content[0], content[-1]

    async def test_real_cadence_preserves_low_and_fractional_rates_and_caps_high(self) -> None:
        package = _package()
        expected = {
            "24/1": Fraction(24, 1),
            "30/1": Fraction(30, 1),
            "30000/1001": Fraction(30000, 1001),
            "60/1": Fraction(60, 1),
            "60000/1001": Fraction(60000, 1001),
            "120/1": Fraction(60, 1),
        }
        renderer = package.PreviewRenderer.create(
            ffmpeg_executable=FFMPEG,
            ffprobe_executable=FFPROBE,
            temp_root=self.root / "cadence-renders",
        )
        capability = await renderer.capability()
        self.assertTrue(capability.available)
        for source_rate, output_rate in expected.items():
            snapshot = self.rate_snapshots[source_rate]
            result = await renderer.render(
                snapshot,
                HighlightSelection((HighlightWindow(0, 3, 0.9),)),
            )
            with self.subTest(source_rate=source_rate):
                self.assertEqual(result.status, package.RenderStatus.SUCCESS)
                self.assertEqual(result.artifact.fps, output_rate)
                self.assertLessEqual(result.artifact.size_bytes, 16 * 1024 * 1024)
                self.assertEqual(snapshot.release_calls, 0)
                self.assertTrue(result.artifact.release())
        self.assertEqual(renderer.active_process_count, 0)
        self.assertEqual(renderer.background_task_count, 0)

    async def test_real_one_two_three_windows_prove_duration_scene_order_padding_and_mp4_contract(self) -> None:
        package = _package()
        renderer = package.PreviewRenderer.create(
            ffmpeg_executable=FFMPEG,
            ffprobe_executable=FFPROBE,
            temp_root=self.root / "scene-renders",
        )
        before = tuple(record.path.read_bytes() for record in self.scene_snapshot.records)
        for count in (1, 2, 3):
            selection = HighlightSelection(
                tuple(HighlightWindow(index * 3, 3, 0.9) for index in range(count))
            )
            result = await renderer.render(self.scene_snapshot, selection)
            with self.subTest(count=count):
                self.assertEqual(result.status, package.RenderStatus.SUCCESS)
                artifact = result.artifact
                self.assertLessEqual(abs(artifact.duration_seconds - count * 3), 0.05 + 2 * count / 30)
                self.assertEqual((artifact.width, artifact.height), (854, 480))
                self.assertEqual(artifact.fps, Fraction(30))
                self.assertLessEqual(artifact.size_bytes, 16 * 1024 * 1024)
                atoms = self._atom_order(artifact.path)
                self.assertLess(atoms.index(b"moov"), atoms.index(b"mdat"))
                left = self._pixel(artifact.path, 1.5, 10, 240)
                center = self._pixel(artifact.path, 1.5, 427, 240)
                self.assertLess(max(left), 24)
                self.assertGreater(max(center), 80)
                dominant = []
                for index in range(count):
                    pixel = self._pixel(artifact.path, index * 3 + 1.5, 427, 240)
                    dominant.append(max(range(3), key=pixel.__getitem__))
                self.assertEqual(tuple(dominant), (0, 1, 2)[:count])
                self.assertTrue(artifact.release())
        self.assertEqual(
            tuple(record.path.read_bytes() for record in self.scene_snapshot.records),
            before,
        )
        self.assertEqual(self.scene_snapshot.release_calls, 0)

    async def test_real_mid_segment_cross_segment_trim_is_exact(self) -> None:
        package = _package()
        renderer = package.PreviewRenderer.create(
            ffmpeg_executable=FFMPEG,
            ffprobe_executable=FFPROBE,
            temp_root=self.root / "exact-trim-renders",
        )
        result = await renderer.render(
            self.scene_snapshot,
            HighlightSelection((HighlightWindow(1.25, 3.5, 0.9),)),
        )
        self.assertEqual(result.status, package.RenderStatus.SUCCESS)
        artifact = result.artifact
        self.assertLessEqual(abs(artifact.duration_seconds - 3.5), 0.05 + 2 / 30)
        early = self._pixel(artifact.path, 0.5, 427, 240)
        late = self._pixel(artifact.path, 2.5, 427, 240)
        self.assertEqual(max(range(3), key=early.__getitem__), 0)
        self.assertEqual(max(range(3), key=late.__getitem__), 1)
        self.assertTrue(artifact.release())
        self.assertEqual(self.scene_snapshot.release_calls, 0)

    async def test_real_timeout_cancel_invalidation_and_output_cap_leave_no_jobs(self) -> None:
        package = _package()
        process_module = importlib.import_module("bot.preview_render.process")

        class RealtimeRunner(process_module.RenderProcessRunner):
            async def spawn(self, argv, *, cwd: Path):
                command = list(argv)
                command.insert(command.index("-f"), "-re")
                return await super().spawn(tuple(command), cwd=cwd)

        selection = HighlightSelection((HighlightWindow(0, 3, 0.9),))
        cases = ("timeout", "cancel", "invalid", "oversize")
        for mode in cases:
            snapshot = self.rate_snapshots["30/1"]
            snapshot.valid = True
            config = package.RenderConfig(
                encode_timeout=0.05 if mode == "timeout" else 60.0,
                max_output_bytes=1024 if mode == "oversize" else 16 * 1024 * 1024,
            )
            temp_root = self.root / f"cleanup-{mode}"
            renderer = package.PreviewRenderer.create(
                ffmpeg_executable=FFMPEG,
                ffprobe_executable=FFPROBE,
                config=config,
                temp_root=temp_root,
                runner=RealtimeRunner(),
            )
            task = asyncio.create_task(renderer.render(snapshot, selection))
            if mode in {"cancel", "invalid"}:
                for _ in range(200):
                    if renderer.active_process_count:
                        break
                    await asyncio.sleep(0.01)
                self.assertGreater(renderer.active_process_count, 0)
                if mode == "cancel":
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    snapshot.valid = False
                    result = await task
                    self.assertEqual(result.status, package.RenderStatus.SNAPSHOT_INVALIDATED)
            else:
                result = await task
                expected = package.RenderStatus.TIMEOUT if mode == "timeout" else package.RenderStatus.OUTPUT_TOO_LARGE
                self.assertEqual(result.status, expected)
            self.assertEqual(renderer.active_process_count, 0)
            self.assertEqual(renderer.background_task_count, 0)
            self.assertFalse(tuple(temp_root.glob("render-*")))
            self.assertEqual(snapshot.release_calls, 0)

    async def test_real_odd_aspect_source_is_even_scaled_and_strictly_centered(self) -> None:
        package = _package()
        renderer = package.PreviewRenderer.create(
            ffmpeg_executable=FFMPEG,
            ffprobe_executable=FFPROBE,
            temp_root=self.root / "odd-aspect-renders",
        )
        result = await renderer.render(
            self.odd_snapshot,
            HighlightSelection((HighlightWindow(0, 3, 0.9),)),
        )
        self.assertEqual(result.status, package.RenderStatus.SUCCESS)
        left, right = self._content_bounds(result.artifact.path, 1.5)
        self.assertGreater(left, 0)
        self.assertLess(right, 853)
        self.assertLessEqual(abs(left - (853 - right)), 1)
        self.assertTrue(result.artifact.release())
        self.assertEqual(self.odd_snapshot.release_calls, 0)


if __name__ == "__main__":
    unittest.main()
