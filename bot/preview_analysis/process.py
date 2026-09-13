from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bot.preview_capture import ProcessController


class AnalysisProcessError(RuntimeError):
    pass


class AnalysisProcessFailure(AnalysisProcessError):
    def __init__(self, diagnostic_code: str = "ffmpeg_exit") -> None:
        super().__init__(diagnostic_code)
        self.diagnostic_code = diagnostic_code


class AnalysisProcessTimeout(AnalysisProcessError):
    pass


class AnalysisSnapshotInvalidated(AnalysisProcessError):
    pass


class AnalysisOutputLimitError(AnalysisProcessError):
    pass


class StreamingParser(Protocol):
    def feed_line(self, line: str) -> None: ...

    def finish(self) -> object: ...


@dataclass(frozen=True)
class ProcessRunResult:
    exit_code: int
    parsed: object


class _ProcessExitState:
    def __init__(self, eof_timeout: float) -> None:
        self.event = asyncio.Event()
        self.eof_timeout = eof_timeout
        self.deadline: float | None = None

    def observe(self) -> None:
        if self.deadline is None:
            self.deadline = asyncio.get_running_loop().time() + self.eof_timeout
            self.event.set()


class _ExitAwareReader:
    def __init__(
        self,
        reader: Any,
        exit_state: _ProcessExitState,
        *,
        timeout_is_error: bool,
    ) -> None:
        self._reader = reader
        self._exit_state = exit_state
        self._timeout_is_error = timeout_is_error

    def _deadline_expired(self) -> bool:
        deadline = self._exit_state.deadline
        return (
            self._exit_state.event.is_set()
            and deadline is not None
            and asyncio.get_running_loop().time() >= deadline
        )

    def _deadline_result(self) -> bytes:
        if self._timeout_is_error:
            raise AnalysisOutputLimitError("metadata pipe did not close")
        return b""

    async def read(self, size: int = -1) -> bytes:
        if self._deadline_expired():
            at_eof = getattr(self._reader, "at_eof", None)
            if callable(at_eof) and at_eof():
                return b""
            return self._deadline_result()
        read_task = asyncio.create_task(self._reader.read(size))
        exit_task = asyncio.create_task(self._exit_state.event.wait())
        try:
            done, _ = await asyncio.wait(
                (read_task, exit_task), return_when=asyncio.FIRST_COMPLETED
            )
            if read_task in done:
                chunk = read_task.result()
                if not chunk:
                    return chunk
                if self._deadline_expired():
                    return self._deadline_result()
                return chunk
            deadline = self._exit_state.deadline
            assert deadline is not None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining > 0:
                try:
                    chunk = await asyncio.wait_for(read_task, remaining)
                    if not chunk:
                        return chunk
                    if self._deadline_expired():
                        return self._deadline_result()
                    return chunk
                except TimeoutError:
                    pass
            return self._deadline_result()
        finally:
            for task in (read_task, exit_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read_task, exit_task, return_exceptions=True)


class _ObservedProcess:
    def __init__(self, process: Any, *, pipe_eof_timeout: float) -> None:
        self._process = process
        self._exit_state = _ProcessExitState(pipe_eof_timeout)
        self.stdout = _ExitAwareReader(
            process.stdout,
            self._exit_state,
            timeout_is_error=True,
        )
        self.stderr = _ExitAwareReader(
            process.stderr,
            self._exit_state,
            timeout_is_error=False,
        )

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        code = await self._process.wait()
        self._exit_state.observe()
        return code

    def send_signal(self, value: int) -> None:
        self._process.send_signal(value)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


class AnalysisProcessRunner:
    async def spawn(
        self, argv: Sequence[str], *, cwd: Path
    ) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return await asyncio.create_subprocess_exec(*argv, **kwargs)


class AnalysisProcessExecutor:
    def __init__(
        self,
        *,
        runner: AnalysisProcessRunner | None = None,
        line_max_bytes: int = 4 * 1024,
        total_max_bytes: int = 2 * 1024 * 1024,
        validity_poll_seconds: float = 0.05,
        post_exit_stdout_timeout: float = 0.5,
    ) -> None:
        self._runner = runner or AnalysisProcessRunner()
        self._line_max_bytes = line_max_bytes
        self._total_max_bytes = total_max_bytes
        self._validity_poll_seconds = validity_poll_seconds
        self._post_exit_stdout_timeout = post_exit_stdout_timeout
        self._active_processes: set[int] = set()
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def active_process_count(self) -> int:
        return len(self._active_processes)

    @property
    def background_task_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    def _track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        self._tasks.add(task)
        return task

    async def _read_stdout(self, reader: Any, parser: StreamingParser) -> object:
        total = 0
        pending = bytearray()
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            total += len(chunk)
            if total > self._total_max_bytes:
                raise AnalysisOutputLimitError("metadata output exceeded limit")
            pending.extend(chunk)
            while b"\n" in pending:
                raw_line, _, remainder = pending.partition(b"\n")
                pending = bytearray(remainder)
                if len(raw_line) + 1 > self._line_max_bytes:
                    raise AnalysisOutputLimitError("metadata line exceeded limit")
                self._feed_bytes(parser, raw_line)
            if len(pending) > self._line_max_bytes:
                raise AnalysisOutputLimitError("metadata line exceeded limit")
        if pending:
            self._feed_bytes(parser, bytes(pending))
        return parser.finish()

    @staticmethod
    def _feed_bytes(parser: StreamingParser, line: bytes) -> None:
        try:
            decoded = line.decode("utf-8", errors="strict").rstrip("\r")
        except UnicodeDecodeError as exc:
            raise AnalysisOutputLimitError("metadata output is not UTF-8") from exc
        parser.feed_line(decoded)

    async def _monitor_guards(
        self,
        is_valid: Callable[[], bool],
        output_guard: Callable[[], bool] | None,
    ) -> None:
        while True:
            if not is_valid():
                raise AnalysisSnapshotInvalidated("snapshot invalidated")
            if output_guard is not None and not output_guard():
                raise AnalysisOutputLimitError("analysis output exceeded limit")
            await asyncio.sleep(self._validity_poll_seconds)

    async def _run_started(
        self,
        process: Any,
        parser: StreamingParser,
        is_valid: Callable[[], bool],
        output_guard: Callable[[], bool] | None,
    ) -> ProcessRunResult:
        controller = ProcessController(process)
        stdout_task = self._track(
            asyncio.create_task(self._read_stdout(process.stdout, parser))
        )
        wait_task = self._track(asyncio.create_task(controller.wait()))
        validity_task = self._track(
            asyncio.create_task(self._monitor_guards(is_valid, output_guard))
        )
        try:
            pending: set[asyncio.Task[Any]] = {wait_task, stdout_task}
            parsed: object | None = None
            exit_code: int | None = None
            while pending:
                done, _ = await asyncio.wait(
                    pending | {validity_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if validity_task in done:
                    await validity_task
                if wait_task in done:
                    exit_code = await wait_task
                    pending.discard(wait_task)
                if stdout_task in done:
                    parsed = await stdout_task
                    pending.discard(stdout_task)
            validity_task.cancel()
            await asyncio.gather(validity_task, return_exceptions=True)
            await controller.finish_io()
            if output_guard is not None and not output_guard():
                raise AnalysisOutputLimitError("analysis output exceeded limit")
            assert exit_code is not None
            if exit_code != 0:
                raise AnalysisProcessFailure("ffmpeg_exit")
            return ProcessRunResult(exit_code, parsed)
        finally:
            if process.returncode is None:
                await asyncio.shield(controller.close())
            else:
                await asyncio.shield(controller.wait_closed())
            for task in (stdout_task, wait_task, validity_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                stdout_task, wait_task, validity_task, return_exceptions=True
            )
            self._tasks.difference_update(
                (stdout_task, wait_task, validity_task)
            )

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        parser: StreamingParser,
        is_valid: Callable[[], bool],
        timeout: float,
        output_guard: Callable[[], bool] | None = None,
    ) -> ProcessRunResult:
        async def spawn_and_run() -> ProcessRunResult:
            if not is_valid():
                raise AnalysisSnapshotInvalidated("snapshot invalidated")
            try:
                spawned_process = await self._runner.spawn(argv, cwd=cwd)
            except FileNotFoundError as exc:
                raise AnalysisProcessFailure("ffmpeg_missing") from exc
            process = _ObservedProcess(
                spawned_process,
                pipe_eof_timeout=self._post_exit_stdout_timeout,
            )
            key = id(spawned_process)
            self._active_processes.add(key)
            try:
                return await self._run_started(
                    process, parser, is_valid, output_guard
                )
            finally:
                self._active_processes.discard(key)

        try:
            return await asyncio.wait_for(spawn_and_run(), timeout)
        except TimeoutError as exc:
            raise AnalysisProcessTimeout("analysis pass timed out") from exc
