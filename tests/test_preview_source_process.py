from __future__ import annotations

import asyncio
import importlib
import os
import signal
import subprocess
import unittest
from collections import deque
from unittest.mock import AsyncMock, patch


SECRET = b"https://video.example/live.m3u8?token=process-secret"


def _process_module():
    try:
        return importlib.import_module("bot.preview_source.process")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B preview source process module must exist") from error


class FakeReader:
    def __init__(
        self,
        *chunks: bytes,
        eof_event: asyncio.Event | None = None,
        first_chunk_event: asyncio.Event | None = None,
    ) -> None:
        self.chunks = deque(chunks)
        self.eof_event = eof_event
        self.first_chunk_event = first_chunk_event
        self.read_calls = 0

    async def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        await asyncio.sleep(0)
        if self.chunks:
            chunk = self.chunks.popleft()
            if self.first_chunk_event is not None:
                self.first_chunk_event.set()
            return chunk
        if self.eof_event is not None:
            await self.eof_event.wait()
        return b""


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = None,
        ignore_break: bool = False,
        ignore_terminate: bool = False,
        delayed_stdout_eof: bool = False,
    ) -> None:
        self.pid = 8123
        self.returncode = returncode
        self.ignore_break = ignore_break
        self.ignore_terminate = ignore_terminate
        self.events: list[object] = []
        self.wait_calls = 0
        self.exited = asyncio.Event()
        self.stdout_started = asyncio.Event()
        if returncode is not None:
            self.exited.set()
        eof_event = self.exited if delayed_stdout_eof or returncode is None else None
        self.stdout = FakeReader(
            stdout,
            eof_event=eof_event,
            first_chunk_event=self.stdout_started,
        )
        self.stderr = FakeReader(stderr, eof_event=self.exited if returncode is None else None)

    def finish(self, code: int = 0) -> None:
        if self.returncode is None:
            self.returncode = code
            self.exited.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self.exited.wait()
        return int(self.returncode)

    def send_signal(self, value: int) -> None:
        self.events.append(("signal", value))
        if not self.ignore_break:
            self.finish(130)

    def terminate(self) -> None:
        self.events.append("terminate")
        if not self.ignore_terminate:
            self.finish(143)

    def kill(self) -> None:
        self.events.append("kill")
        self.finish(-9)


class QueueRunner:
    def __init__(self, *processes: FakeProcess) -> None:
        self.processes = deque(processes)
        self.calls: list[tuple[str, ...]] = []

    async def spawn(self, argv):
        self.calls.append(tuple(argv))
        if not self.processes:
            raise AssertionError("unexpected process spawn")
        return self.processes.popleft()


class GateReader:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.read_calls = 0

    async def read(self, _size: int = -1) -> bytes:
        self.read_calls += 1
        await self.release.wait()
        return b""


class ResolverProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_uses_exec_api_with_separate_argv_and_no_shell(self) -> None:
        process_module = _process_module()
        process = FakeProcess(returncode=0)
        exec_mock = AsyncMock(return_value=process)
        argv = (
            "streamlink",
            "--stream-sorting-excludes",
            ">720p60",
            "https://www.twitch.tv/valid_login",
        )

        with patch("asyncio.create_subprocess_exec", exec_mock), patch(
            "asyncio.create_subprocess_shell", new_callable=AsyncMock
        ) as shell_mock:
            result = await process_module.StreamlinkProcessRunner().spawn(argv)

        self.assertIs(result, process)
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


class BoundedExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reaped_process_with_inherited_open_pipes_finishes_bounded(self) -> None:
        process_module = _process_module()
        process = FakeProcess(returncode=0)
        stdout = GateReader()
        stderr = GateReader()
        process.stdout = stdout
        process.stderr = stderr
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process),
            timeout=0.1,
            pipe_eof_timeout=0.01,
        )
        task = asyncio.create_task(executor.run(("streamlink", "--version")))
        done, _pending = await asyncio.wait((task,), timeout=0.2)

        try:
            self.assertIn(
                task,
                done,
                "reaped child must not hang on a pipe inherited by another process",
            )
        finally:
            stdout.release.set()
            stderr.release.set()
            if not task.done():
                await task

        result = task.result()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b"")
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_normal_exit_is_reaped_and_both_streams_reach_eof(self) -> None:
        process_module = _process_module()
        process = FakeProcess(stdout=b"ok\n", stderr=b"notice", returncode=0)
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process), timeout=0.1
        )

        result = await executor.run(("streamlink", "--version"))

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout_bytes, b"ok\n")
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertGreaterEqual(process.stdout.read_calls, 2)
        self.assertGreaterEqual(process.stderr.read_calls, 2)
        self.assertFalse(
            any(
                not task.done() and task.get_name().startswith("preview-source-")
                for task in asyncio.all_tasks()
            )
        )

    async def test_stdout_overflow_is_bounded_but_reader_is_drained_to_eof(self) -> None:
        process_module = _process_module()
        process = FakeProcess(stdout=b"x" * 40, returncode=0)
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process), timeout=0.1, stdout_max_bytes=16
        )

        result = await executor.run(("streamlink", "--version"))

        self.assertTrue(result.stdout_overflowed)
        self.assertEqual(result.stdout_bytes, b"x" * 16)
        self.assertGreaterEqual(process.stdout.read_calls, 2)

    async def test_stderr_is_bounded_drained_and_absent_from_result_repr(self) -> None:
        process_module = _process_module()
        process = FakeProcess(stderr=SECRET * 4096, returncode=1)
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process), timeout=0.1
        )

        result = await executor.run(("streamlink", "bad"))

        rendered = repr(result)
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("process-secret", rendered)
        self.assertNotIn("video.example", rendered)
        self.assertNotIn("stderr", result.__dataclass_fields__)
        self.assertGreaterEqual(process.stderr.read_calls, 2)

    async def test_timeout_escalates_to_kill_and_reaps_process(self) -> None:
        process_module = _process_module()
        process = FakeProcess(ignore_break=True, ignore_terminate=True)
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process),
            timeout=0.001,
            terminate_timeout=0.001,
            kill_timeout=0.02,
            windows_break_timeout=0,
            platform="nt",
        )

        with self.assertRaises(process_module.ResolverProcessTimeout):
            await executor.run(("streamlink", "hang"))

        self.assertEqual(process.events[0][0], "signal")
        self.assertEqual(process.events[1:], ["terminate", "kill"])
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertTrue(process.exited.is_set())

    async def test_cancellation_before_exit_cleans_process_and_drain_tasks(self) -> None:
        process_module = _process_module()
        process = FakeProcess(ignore_break=True, ignore_terminate=True)
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process),
            timeout=10,
            terminate_timeout=0.001,
            kill_timeout=0.02,
            windows_break_timeout=0,
            platform="nt",
        )
        task = asyncio.create_task(executor.run(("streamlink", "hang")))
        await asyncio.sleep(0)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        self.assertIn("kill", process.events)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertFalse(
            any(
                not item.done()
                and item is not asyncio.current_task()
                and item.get_name().startswith("preview-source-")
                for item in asyncio.all_tasks()
            )
        )

    async def test_cancellation_after_stdout_before_reap_discards_secret(self) -> None:
        process_module = _process_module()
        process = FakeProcess(
            stdout=SECRET + b"\n",
            ignore_break=True,
            ignore_terminate=True,
            delayed_stdout_eof=True,
        )
        executor = process_module.ResolverProcessExecutor(
            runner=QueueRunner(process),
            timeout=10,
            terminate_timeout=0.001,
            kill_timeout=0.02,
            windows_break_timeout=0,
            platform="nt",
        )
        task = asyncio.create_task(executor.run(("streamlink", "resolve")))
        await process.stdout_started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError) as captured:
            await task
        await asyncio.sleep(0)

        self.assertNotIn("process-secret", repr(captured.exception))
        self.assertIn("kill", process.events)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertFalse(
            any(
                not item.done()
                and item is not asyncio.current_task()
                and item.get_name().startswith("preview-source-")
                for item in asyncio.all_tasks()
            )
        )

    async def test_two_executors_keep_outputs_and_processes_isolated(self) -> None:
        process_module = _process_module()
        first = FakeProcess(stdout=b"first", returncode=0)
        second = FakeProcess(stdout=b"second", returncode=0)
        executor_a = process_module.ResolverProcessExecutor(
            runner=QueueRunner(first), timeout=0.1
        )
        executor_b = process_module.ResolverProcessExecutor(
            runner=QueueRunner(second), timeout=0.1
        )

        result_a, result_b = await asyncio.gather(
            executor_a.run(("streamlink", "A")),
            executor_b.run(("streamlink", "B")),
        )

        self.assertEqual(result_a.stdout_bytes, b"first")
        self.assertEqual(result_b.stdout_bytes, b"second")
        self.assertGreaterEqual(first.wait_calls, 1)
        self.assertGreaterEqual(second.wait_calls, 1)


class StreamlinkFailureClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, stderr: bytes = b"", stdout: bytes = b""):
        process_module = _process_module()
        process = FakeProcess(stdout=stdout, stderr=stderr, returncode=1)
        result = await process_module.ResolverProcessExecutor(
            runner=QueueRunner(process), timeout=0.1
        ).run(("streamlink", "resolve"))
        self.assertNotIn("stderr", result.__dataclass_fields__)
        return result

    async def test_client_integrity_error_is_classified_without_raw_stderr(self) -> None:
        result = await self._run(
            b"error: Failed acquiring client-integrity token token=secret-value"
        )
        self.assertEqual(result.failure_code, "client_integrity")
        self.assertNotIn("secret-value", repr(result))

    async def test_no_playable_streams_is_classified_without_url(self) -> None:
        result = await self._run(
            stdout=b"error: No playable streams found on this URL: https://www.twitch.tv/private_login"
        )
        self.assertEqual(result.failure_code, "no_playable_streams")
        self.assertNotIn("private_login", repr(result))


    async def test_http_and_network_errors_are_fixed_categories(self) -> None:
        cases = (
            (b"error: HTTP 403 Forbidden", "http_403"),
            (b"error: HTTP 429 Too Many Requests", "http_429"),
            (b"error: Temporary failure in name resolution", "dns_error"),
            (b"error: certificate verify failed", "tls_error"),
            (b"error: connection reset by peer", "network_error"),
        )
        for stderr, expected in cases:
            with self.subTest(stderr=stderr):
                result = await self._run(stderr)
                self.assertEqual(result.failure_code, expected)

    async def test_unknown_error_falls_back_to_process_failed(self) -> None:
        result = await self._run(b"opaque internal failure token=secret-value")
        self.assertEqual(result.failure_code, "process_failed")
        self.assertNotIn("secret-value", repr(result))


if __name__ == "__main__":
    unittest.main()
