from __future__ import annotations

import asyncio
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot import preview_capture as capture


class PreviewCaptureBoundaryTests(unittest.TestCase):
    def test_package_has_no_forbidden_runtime_dependencies(self) -> None:
        package = Path(__file__).parents[1] / "bot" / "preview_capture"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        ).lower()

        for forbidden in (
            "streamlink",
            "aiogram",
            "bot.live_post",
            "bot.preview_runtime",
            "inputmediavideo",
            "edit_message_media",
        ):
            self.assertNotIn(forbidden, source)

    def test_p4a_is_not_wired_into_main_poller_or_preview_runtime(self) -> None:
        project = Path(__file__).parents[1]
        for relative in ("main.py", "bot/poller.py", "bot/preview_runtime.py"):
            source = (project / relative).read_text(encoding="utf-8").lower()
            self.assertNotIn("preview_capture", source)


@unittest.skipUnless(shutil.which("ffmpeg"), "optional: ffmpeg is unavailable")
class RealFfmpegCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def _run_exec(self, *argv: str) -> tuple[int, bytes]:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return process.returncode, output

    async def _make_fixture(self, path: Path, fps: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        assert ffmpeg is not None
        code, output = await self._run_exec(
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x90:rate={fps}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-t",
            "5",
            "-c:v",
            "mpeg2video",
            "-g",
            str(fps),
            "-c:a",
            "mp2",
            "-f",
            "mpegts",
            str(path),
        )
        self.assertEqual(code, 0, output.decode(errors="replace")[-1000:])

    async def _assert_capture(self, fps: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        assert ffmpeg is not None
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixture = root / f"fixture-{fps}.ts"
            await self._make_fixture(fixture, fps)
            service = await capture.CaptureService.create(root=root / "capture-root")
            self.assertTrue(service.capability.available)

            started = await service.start(capture.FileCaptureInput(fixture))
            self.assertTrue(started.started)
            handle = started.handle
            outcome = await asyncio.wait_for(handle.wait(), 10)
            self.assertEqual(outcome.reason, capture.CaptureEndReason.CLEAN_EOF)
            acquired = handle.acquire_snapshot()
            self.assertEqual(acquired.status, capture.SnapshotStatus.READY)
            snapshot = acquired.snapshot
            self.assertGreater(snapshot.actual_duration_seconds, 0)
            self.assertTrue(all(record.path.is_file() for record in snapshot.records))

            concatenated = root / f"snapshot-{fps}.ts"
            with concatenated.open("wb") as output:
                for record in snapshot.records:
                    output.write(record.path.read_bytes())
            self.assertGreater(concatenated.stat().st_size, 0)

            ffprobe = shutil.which("ffprobe")
            if ffprobe is not None:
                code, metadata = await self._run_exec(
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(concatenated),
                )
                self.assertEqual(code, 0, metadata.decode(errors="replace"))
                self.assertEqual(metadata.decode().strip(), f"{fps}/1")

            snapshot.release()
            await handle.close()
            self.assertEqual(handle.background_task_count, 0)
            self.assertEqual(handle.active_pin_count, 0)
            await service.close()

    async def test_copy_capture_preserves_30_fps_and_snapshot_is_readable(self) -> None:
        await self._assert_capture(30)

    async def test_copy_capture_preserves_60_fps_and_snapshot_is_readable(self) -> None:
        await self._assert_capture(60)


if __name__ == "__main__":
    unittest.main()
