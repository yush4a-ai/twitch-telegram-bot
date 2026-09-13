from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _process():
    try:
        return importlib.import_module("bot.preview_render.process")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render process boundary must exist") from exc


class FakeReader:
    def __init__(self, chunks=()) -> None:
        self.chunks = list(chunks)

    async def read(self, _size: int = -1) -> bytes:
        await asyncio.sleep(0)
        return self.chunks.pop(0) if self.chunks else b""


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 7412
        self.stderr = FakeReader()
        self.returncode = None
        self._exit = asyncio.Event()

    async def wait(self) -> int:
        await self._exit.wait()
        return int(self.returncode)

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._exit.set()

    def send_signal(self, _signal: int) -> None:
        self.finish(-2)

    def terminate(self) -> None:
        self.finish(-15)

    def kill(self) -> None:
        self.finish(-9)


class FakeRunner:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls = []

    async def spawn(self, argv, *, cwd: Path):
        self.calls.append((tuple(argv), cwd))
        return self.process


class RenderProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_uses_exec_no_shell_devnull_stdout_and_bounded_stderr_pipe(self) -> None:
        process = _process()
        fake = FakeProcess()
        fake.finish()
        exec_mock = AsyncMock(return_value=fake)
        argv = ("ffmpeg", "-i", "window.ffconcat", "preview.tmp.mp4")
        with patch("asyncio.create_subprocess_exec", exec_mock), patch(
            "asyncio.create_subprocess_shell", new_callable=AsyncMock
        ) as shell_mock:
            returned = await process.RenderProcessRunner().spawn(
                argv, cwd=Path("job")
            )
        self.assertIs(returned, fake)
        self.assertEqual(exec_mock.await_args.args, argv)
        kwargs = exec_mock.await_args.kwargs
        self.assertEqual(kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], asyncio.subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], asyncio.subprocess.PIPE)
        shell_mock.assert_not_awaited()
        if os.name == "nt":
            self.assertEqual(
                kwargs["creationflags"], subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            self.assertTrue(kwargs["start_new_session"])


class RenderProcessExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_reaps_and_leaves_no_background_work(self) -> None:
        process = _process()
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "preview.tmp.mp4"
            output.write_bytes(b"mp4")
            fake = FakeProcess()
            fake.finish(0)
            executor = process.RenderProcessExecutor(
                runner=FakeRunner(fake), poll_seconds=0.005
            )
            code = await executor.run(
                ("ffmpeg",),
                cwd=Path(raw),
                output_path=output,
                max_output_bytes=16,
                is_valid=lambda: True,
                timeout=1.0,
            )
            self.assertEqual(code, 0)
            self.assertEqual(executor.active_process_count, 0)
            self.assertEqual(executor.background_task_count, 0)

    async def test_oversize_terminates_reaps_and_leaves_zero_processes(self) -> None:
        process = _process()
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "preview.tmp.mp4"
            output.write_bytes(b"x" * 17)
            fake = FakeProcess()
            executor = process.RenderProcessExecutor(
                runner=FakeRunner(fake), poll_seconds=0.005
            )
            with self.assertRaises(process.RenderOutputTooLarge):
                await executor.run(
                    ("ffmpeg",),
                    cwd=Path(raw),
                    output_path=output,
                    max_output_bytes=16,
                    is_valid=lambda: True,
                    timeout=1.0,
                )
            self.assertIsNotNone(fake.returncode)
            self.assertEqual(executor.active_process_count, 0)
            self.assertEqual(executor.background_task_count, 0)

    async def test_nonzero_exit_and_missing_binary_are_typed(self) -> None:
        process = _process()
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "preview.tmp.mp4"
            fake = FakeProcess()
            fake.finish(7)
            executor = process.RenderProcessExecutor(runner=FakeRunner(fake))
            with self.assertRaises(process.RenderProcessFailure) as caught:
                await executor.run(
                    ("ffmpeg",),
                    cwd=Path(raw),
                    output_path=output,
                    max_output_bytes=16,
                    is_valid=lambda: True,
                    timeout=1.0,
                )
            self.assertEqual(caught.exception.diagnostic_code, "ffmpeg_exit")

            class MissingRunner:
                async def spawn(self, *_args, **_kwargs):
                    raise FileNotFoundError("private binary path")

            executor = process.RenderProcessExecutor(runner=MissingRunner())
            with self.assertRaises(process.RenderProcessFailure) as caught:
                await executor.run(
                    ("ffmpeg",),
                    cwd=Path(raw),
                    output_path=output,
                    max_output_bytes=16,
                    is_valid=lambda: True,
                    timeout=1.0,
                )
            self.assertEqual(caught.exception.diagnostic_code, "ffmpeg_missing")

    async def test_timeout_invalidation_and_cancellation_all_reap(self) -> None:
        process = _process()
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "preview.tmp.mp4"
            for mode in ("timeout", "invalid", "cancel"):
                fake = FakeProcess()
                valid = [True]
                executor = process.RenderProcessExecutor(
                    runner=FakeRunner(fake), poll_seconds=0.005
                )
                task = asyncio.create_task(
                    executor.run(
                        ("ffmpeg",),
                        cwd=Path(raw),
                        output_path=output,
                        max_output_bytes=16,
                        is_valid=lambda valid=valid: valid[0],
                        timeout=0.02 if mode == "timeout" else 2.0,
                    )
                )
                if mode in {"invalid", "cancel"}:
                    for _ in range(100):
                        if executor.active_process_count:
                            break
                        await asyncio.sleep(0)
                    self.assertGreater(executor.active_process_count, 0)
                    if mode == "invalid":
                        valid[0] = False
                    else:
                        task.cancel()
                expected = {
                    "timeout": process.RenderProcessTimeout,
                    "invalid": process.RenderSnapshotInvalidated,
                    "cancel": asyncio.CancelledError,
                }[mode]
                with self.subTest(mode=mode), self.assertRaises(expected):
                    await task
                self.assertIsNotNone(fake.returncode)
                self.assertEqual(executor.active_process_count, 0)
                self.assertEqual(executor.background_task_count, 0)


if __name__ == "__main__":
    unittest.main()
