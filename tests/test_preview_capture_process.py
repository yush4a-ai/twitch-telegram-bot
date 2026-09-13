from __future__ import annotations

import asyncio
import importlib
import logging
import os
import subprocess
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch


def _capture_module():
    try:
        return importlib.import_module("bot.preview_capture")
    except ModuleNotFoundError:
        return None


class FakeStderr:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = deque(chunks)

    async def read(self, _size: int = -1) -> bytes:
        await asyncio.sleep(0)
        return self.chunks.popleft() if self.chunks else b""


class FakeProcess:
    def __init__(
        self,
        *,
        exit_on_terminate: bool = True,
        stderr: FakeStderr | None = None,
    ) -> None:
        self.pid = 4321
        self.returncode: int | None = None
        self.stderr = stderr or FakeStderr()
        self.exit_on_terminate = exit_on_terminate
        self.events: list[object] = []
        self.wait_calls = 0
        self._exited = asyncio.Event()

    def finish(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
            self._exited.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._exited.wait()
        return int(self.returncode)

    def send_signal(self, value: int) -> None:
        self.events.append(("signal", value))

    def terminate(self) -> None:
        self.events.append("terminate")
        if self.exit_on_terminate:
            self.finish(0)

    def kill(self) -> None:
        self.events.append("kill")
        self.finish(-9)


class FaultyWindowsProcess(FakeProcess):
    def send_signal(self, value: int) -> None:
        self.events.append(("signal", value))
        raise OSError("break unavailable")

    def terminate(self) -> None:
        self.events.append("terminate")
        raise OSError("terminate unavailable")


class CaptureInputTests(unittest.TestCase):
    def test_public_process_contract_exists(self) -> None:
        self.assertIsNotNone(
            _capture_module(), "P4A preview_capture package must exist"
        )

    def test_url_input_repr_never_contains_signed_url(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        url = "https://video.example/live.m3u8?token=super-secret"

        source = capture.UrlCaptureInput(url)

        self.assertEqual(source.url, url)
        self.assertEqual(repr(source), "UrlCaptureInput(<redacted>)")
        self.assertNotIn("super-secret", repr(source))

    def test_file_input_normalizes_to_path_without_accepting_command_args(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)

        source = capture.FileCaptureInput("fixture.ts")

        self.assertEqual(source.path, Path("fixture.ts"))
        self.assertEqual(set(source.__dataclass_fields__), {"path"})
        self.assertEqual(set(capture.UrlCaptureInput.__dataclass_fields__), {"url"})


class AsyncioProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_uses_exec_api_with_separate_argv_and_no_shell(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess()
        process.finish(0)
        exec_mock = AsyncMock(return_value=process)
        argv = (
            "ffmpeg",
            "-i",
            "https://video.example/live.m3u8?token=a&next=b",
            "index.m3u8",
        )

        with patch("asyncio.create_subprocess_exec", exec_mock), patch(
            "asyncio.create_subprocess_shell", new_callable=AsyncMock
        ) as shell_mock:
            result = await capture.AsyncioProcessRunner().spawn(
                argv, cwd=Path("capture-dir")
            )

        self.assertIs(result, process)
        shell_mock.assert_not_awaited()
        call = exec_mock.await_args
        self.assertEqual(call.args, argv)
        self.assertEqual(call.kwargs["stdin"], asyncio.subprocess.DEVNULL)
        self.assertEqual(call.kwargs["stdout"], asyncio.subprocess.DEVNULL)
        self.assertEqual(call.kwargs["stderr"], asyncio.subprocess.PIPE)
        self.assertNotIn("shell", call.kwargs)
        if os.name == "nt":
            self.assertEqual(
                call.kwargs["creationflags"], subprocess.CREATE_NEW_PROCESS_GROUP
            )
            self.assertNotIn("start_new_session", call.kwargs)
        else:
            self.assertTrue(call.kwargs["start_new_session"])
            self.assertNotIn("creationflags", call.kwargs)


class BoundedStderrTailTests(unittest.IsolatedAsyncioTestCase):
    async def test_tail_keeps_only_last_64_kib_while_reader_is_fully_drained(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        chunks = (b"a" * 40000, b"b" * 40000, b"c" * 40000)
        reader = FakeStderr(*chunks)
        tail = capture.BoundedStderrTail(65536)

        await capture.drain_stderr(reader, tail)

        self.assertEqual(len(tail.bytes), 65536)
        self.assertEqual(tail.bytes, (b"b" * 25536) + (b"c" * 40000))
        self.assertEqual(len(reader.chunks), 0)


class ProcessControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_exit_is_awaited_and_reaped(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess()
        controller = capture.ProcessController(
            process,
            platform="posix",
            terminate_timeout=0.02,
            kill_timeout=0.02,
            killpg=lambda *_args: None,
        )
        process.finish(0)

        code = await controller.wait()
        await controller.finish_io()

        self.assertEqual(code, 0)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertEqual(controller.background_task_count, 0)

    async def test_posix_close_terminates_process_group_then_reaps(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess(exit_on_terminate=False)
        events: list[tuple[int, int]] = []

        def killpg(pid: int, signal_number: int) -> None:
            events.append((pid, signal_number))
            process.finish(0)

        controller = capture.ProcessController(
            process,
            platform="posix",
            terminate_timeout=0.02,
            kill_timeout=0.02,
            killpg=killpg,
        )

        code = await controller.close()

        self.assertEqual(code, 0)
        self.assertEqual(events[0][0], process.pid)
        self.assertEqual(events[0][1], capture.SIGTERM)
        self.assertNotIn("kill", process.events)
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_terminate_timeout_escalates_to_kill_then_wait(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess(exit_on_terminate=False)
        signals: list[int] = []

        def killpg(_pid: int, signal_number: int) -> None:
            signals.append(signal_number)
            if signal_number == capture.SIGKILL:
                process.finish(-9)

        controller = capture.ProcessController(
            process,
            platform="posix",
            terminate_timeout=0.001,
            kill_timeout=0.02,
            killpg=killpg,
        )

        code = await controller.close()

        self.assertEqual(code, -9)
        self.assertEqual(signals, [capture.SIGTERM, capture.SIGKILL])
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_windows_break_then_terminate_then_kill_fallback(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess(exit_on_terminate=False)
        controller = capture.ProcessController(
            process,
            platform="nt",
            terminate_timeout=0.002,
            kill_timeout=0.02,
            windows_break_timeout=0,
        )

        code = await controller.close()

        self.assertEqual(code, -9)
        self.assertEqual(process.events[0][0], "signal")
        self.assertEqual(process.events[1:], ["terminate", "kill"])
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_windows_signal_api_errors_still_reach_kill_and_reap(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FaultyWindowsProcess(exit_on_terminate=False)
        controller = capture.ProcessController(
            process,
            platform="nt",
            terminate_timeout=0.001,
            kill_timeout=0.02,
            windows_break_timeout=0,
        )

        code = await controller.close()

        self.assertEqual(code, -9)
        self.assertEqual(process.events[1:], ["terminate", "kill"])
        self.assertEqual(controller.background_task_count, 0)

    async def test_posix_term_api_error_still_reaches_kill_and_reap(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess(exit_on_terminate=False)
        signals: list[int] = []

        def killpg(_pid: int, signal_number: int) -> None:
            signals.append(signal_number)
            if signal_number == capture.SIGTERM:
                raise OSError("term unavailable")
            process.finish(-9)

        controller = capture.ProcessController(
            process,
            platform="posix",
            terminate_timeout=0.001,
            kill_timeout=0.02,
            killpg=killpg,
        )

        code = await controller.close()

        self.assertEqual(code, -9)
        self.assertEqual(signals, [capture.SIGTERM, capture.SIGKILL])
        self.assertEqual(controller.background_task_count, 0)

    async def test_repeated_close_waits_single_internal_close_task(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess()
        controller = capture.ProcessController(
            process,
            platform="nt",
            terminate_timeout=0.02,
            kill_timeout=0.02,
            windows_break_timeout=0,
        )

        first, second = await asyncio.gather(controller.close(), controller.close())

        self.assertEqual((first, second), (0, 0))
        self.assertEqual(process.events.count("terminate"), 1)
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_caller_cancellation_does_not_cancel_internal_cleanup(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        process = FakeProcess(exit_on_terminate=False)
        controller = capture.ProcessController(
            process,
            platform="nt",
            terminate_timeout=0.02,
            kill_timeout=0.02,
            windows_break_timeout=0.001,
        )
        caller = asyncio.create_task(controller.close())
        await asyncio.sleep(0)
        caller.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await caller
        await controller.wait_closed()

        self.assertIn("kill", process.events)
        self.assertGreaterEqual(process.wait_calls, 1)
        self.assertEqual(controller.background_task_count, 0)

    async def test_stderr_drain_finishes_without_logging_sensitive_bytes(self) -> None:
        capture = _capture_module()
        self.assertIsNotNone(capture)
        secret = b"https://video.example/a.m3u8?token=never-log-me"
        process = FakeProcess(stderr=FakeStderr(secret))
        process.finish(1)
        controller = capture.ProcessController(
            process,
            platform="nt",
            terminate_timeout=0.02,
            kill_timeout=0.02,
            windows_break_timeout=0,
        )

        with self.assertLogs("bot.preview_capture", logging.WARNING) as captured_logs:
            await controller.wait()
            controller.log_sanitized_failure("process_exit")
        await controller.finish_io()

        rendered = "\n".join(captured_logs.output)
        self.assertNotIn("never-log-me", rendered)
        self.assertNotIn("video.example", rendered)
        self.assertEqual(controller.background_task_count, 0)


if __name__ == "__main__":
    unittest.main()
