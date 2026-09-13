from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


def _process():
    try:
        return importlib.import_module("bot.preview_analysis.process")
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 analysis process boundary must exist") from exc


class FakeReader:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = deque(chunks)
        self._eof_seen = False

    async def read(self, _size: int = -1) -> bytes:
        await asyncio.sleep(0)
        chunk = self.chunks.popleft() if self.chunks else b""
        if not chunk:
            self._eof_seen = True
        return chunk

    async def readline(self) -> bytes:
        await asyncio.sleep(0)
        return self.chunks.popleft() if self.chunks else b""

    def at_eof(self) -> bool:
        return self._eof_seen or bool(self.chunks) and not self.chunks[0]


class FakeProcess:
    def __init__(self, stdout: FakeReader, stderr: FakeReader | None = None) -> None:
        self.pid = 4321
        self.stdout = stdout
        self.stderr = stderr or FakeReader()
        self.returncode: int | None = None
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return int(self.returncode)

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()

    def send_signal(self, _value: int) -> None:
        self.finish(-2)

    def terminate(self) -> None:
        self.finish(-15)

    def kill(self) -> None:
        self.finish(-9)


class LineParser:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def feed_line(self, line: str) -> None:
        self.lines.append(line)

    def finish(self):
        return tuple(self.lines)


class AnalysisProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_uses_exec_fixed_argv_and_separate_bounded_pipes(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader())
        fake.finish()
        exec_mock = AsyncMock(return_value=fake)
        argv = ("ffmpeg", "-f", "concat", "manifest.ffconcat")
        with patch("asyncio.create_subprocess_exec", exec_mock), patch(
            "asyncio.create_subprocess_shell", new_callable=AsyncMock
        ) as shell_mock:
            returned = await process.AnalysisProcessRunner().spawn(
                argv, cwd=Path("job")
            )
        self.assertIs(returned, fake)
        shell_mock.assert_not_awaited()
        call = exec_mock.await_args
        self.assertEqual(call.args, argv)
        self.assertEqual(call.kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(call.kwargs["stdout"], asyncio.subprocess.PIPE)
        self.assertEqual(call.kwargs["stderr"], asyncio.subprocess.PIPE)
        self.assertNotIn("shell", call.kwargs)
        if os.name == "nt":
            self.assertEqual(
                call.kwargs["creationflags"], subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            self.assertTrue(call.kwargs["start_new_session"])

    async def test_executor_streams_metadata_and_returns_parsed_result(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader(b"a=1\n", b"b=2\n", b""))
        fake.finish(0)
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        result = await executor.run(
            ("ffmpeg",),
            cwd=Path("."),
            parser=LineParser(),
            is_valid=lambda: True,
            timeout=1.0,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.parsed, ("a=1", "b=2"))
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

    async def test_line_and_total_output_bounds_are_enforced(self) -> None:
        process = _process()
        for chunks in ((b"x" * 4097 + b"\n", b""), (b"x" * 1024,) * 2049 + (b"",)):
            fake = FakeProcess(FakeReader(*chunks))
            fake.finish(0)
            executor = process.AnalysisProcessExecutor(
                runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
            )
            with self.subTest(size=sum(map(len, chunks))), self.assertRaises(
                process.AnalysisOutputLimitError
            ):
                await executor.run(
                    ("ffmpeg",),
                    cwd=Path("."),
                    parser=LineParser(),
                    is_valid=lambda: True,
                    timeout=1.0,
                )
            self.assertEqual(executor.active_process_count, 0)
            self.assertEqual(executor.background_task_count, 0)

    async def test_oversized_stdout_stops_a_still_running_process_immediately(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader(b"x" * 4097 + b"\n", b""))
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        with self.assertRaises(process.AnalysisOutputLimitError):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=1.0,
            )
        self.assertIsNotNone(fake.returncode)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

        with tempfile.TemporaryDirectory() as raw:
            fingerprint = Path(raw) / "fingerprints.gray"
            fingerprint.write_bytes(b"")
            fake = FakeProcess(FakeReader())
            executor = process.AnalysisProcessExecutor(
                runner=type(
                    "Runner", (), {"spawn": AsyncMock(return_value=fake)}
                )(),
                validity_poll_seconds=0.005,
            )
            task = asyncio.create_task(
                executor.run(
                    ("ffmpeg",),
                    cwd=Path(raw),
                    parser=LineParser(),
                    is_valid=lambda: True,
                    output_guard=lambda: fingerprint.stat().st_size <= 160,
                    timeout=1.0,
                )
            )
            await asyncio.sleep(0.01)
            fingerprint.write_bytes(b"x" * 161)
            with self.assertRaises(process.AnalysisOutputLimitError):
                await task
            self.assertIsNotNone(fake.returncode)
            self.assertEqual(executor.active_process_count, 0)
            self.assertEqual(executor.background_task_count, 0)

        fake = FakeProcess(FakeReader(b""))
        fake.finish(0)
        final_guard = Mock(side_effect=(True, False))
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        with self.assertRaises(process.AnalysisOutputLimitError):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                output_guard=final_guard,
                timeout=1.0,
            )
        self.assertEqual(final_guard.call_count, 2)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

    async def test_timeout_closes_reaps_and_drains_process(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader())
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        with self.assertRaises(process.AnalysisProcessTimeout):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=0.01,
            )
        self.assertIsNotNone(fake.returncode)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

        slow_spawn_process = FakeProcess(FakeReader())
        slow_spawn_process.finish(0)

        async def slow_spawn(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            return slow_spawn_process

        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": slow_spawn})()
        )
        with self.assertRaises(process.AnalysisProcessTimeout):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=0.01,
            )

    async def test_nonzero_exit_is_a_typed_process_failure(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader(b""))
        fake.finish(7)
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        with self.assertRaises(process.AnalysisProcessFailure) as caught:
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=1.0,
            )
        self.assertEqual(caught.exception.diagnostic_code, "ffmpeg_exit")

    async def test_post_exit_pipe_that_never_closes_is_bounded(self) -> None:
        process = _process()

        class BlockingReader(FakeReader):
            async def read(self, _size: int = -1) -> bytes:
                await asyncio.Event().wait()

        fake = FakeProcess(BlockingReader())
        fake.finish(0)
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})(),
            post_exit_stdout_timeout=0.01,
        )
        with self.assertRaises(process.AnalysisOutputLimitError):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=1.0,
            )
        self.assertEqual(executor.background_task_count, 0)

        class ControlledBlockingReader(FakeReader):
            def __init__(self) -> None:
                super().__init__()
                self.unblock = asyncio.Event()

            async def read(self, _size: int = -1) -> bytes:
                await self.unblock.wait()
                return b""

        inherited_stderr = ControlledBlockingReader()
        fake = FakeProcess(FakeReader(b""), inherited_stderr)
        fake.finish(0)
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})(),
            post_exit_stdout_timeout=0.01,
        )
        task = asyncio.create_task(
            executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=1.0,
            )
        )
        try:
            result = await asyncio.wait_for(asyncio.shield(task), 0.2)
            self.assertEqual(result.exit_code, 0)
        finally:
            inherited_stderr.unblock.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

        class StoppableEndlessReader(FakeReader):
            def __init__(self) -> None:
                super().__init__()
                self.stop = False

            async def read(self, _size: int = -1) -> bytes:
                await asyncio.sleep(0)
                return b"" if self.stop else b"x" * 1024

        streaming_stderr = StoppableEndlessReader()
        fake = FakeProcess(FakeReader(b""), streaming_stderr)
        fake.finish(0)
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})(),
            post_exit_stdout_timeout=0.01,
        )
        task = asyncio.create_task(
            executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=1.0,
            )
        )
        try:
            result = await asyncio.wait_for(asyncio.shield(task), 0.2)
            self.assertEqual(result.exit_code, 0)
        finally:
            streaming_stderr.stop = True
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)

    async def test_invalidation_closes_process_with_typed_error(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader())
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        with self.assertRaises(process.AnalysisSnapshotInvalidated):
            await executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: False,
                timeout=1.0,
            )
        self.assertEqual(executor.active_process_count, 0)

    async def test_cancellation_cleans_up_then_reraises(self) -> None:
        process = _process()
        fake = FakeProcess(FakeReader())
        executor = process.AnalysisProcessExecutor(
            runner=type("Runner", (), {"spawn": AsyncMock(return_value=fake)})()
        )
        task = asyncio.create_task(
            executor.run(
                ("ffmpeg",),
                cwd=Path("."),
                parser=LineParser(),
                is_valid=lambda: True,
                timeout=30.0,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(fake.returncode)
        self.assertEqual(executor.active_process_count, 0)
        self.assertEqual(executor.background_task_count, 0)


if __name__ == "__main__":
    unittest.main()
