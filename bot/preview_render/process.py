from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from bot.preview_capture import ProcessController


class RenderProcessError(RuntimeError):
    pass


class RenderProcessFailure(RenderProcessError):
    def __init__(self, diagnostic_code: str = "ffmpeg_exit") -> None:
        super().__init__(diagnostic_code)
        self.diagnostic_code = diagnostic_code


class RenderProcessTimeout(RenderProcessError):
    pass


class RenderSnapshotInvalidated(RenderProcessError):
    pass


class RenderOutputTooLarge(RenderProcessError):
    pass


class RenderCommandOutputError(RenderProcessError):
    pass


class RenderProcessRunner:
    async def spawn(
        self, argv: Sequence[str], *, cwd: Path
    ) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.DEVNULL,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return await asyncio.create_subprocess_exec(*argv, **kwargs)

    async def run_command(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output: int,
    ) -> tuple[int, bytes]:
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
        controller = ProcessController(process)

        async def collect() -> tuple[int, bytes]:
            output = bytearray()
            while True:
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_output:
                    raise RenderCommandOutputError("command output exceeded limit")
            code = await controller.wait()
            await controller.finish_io()
            return code, bytes(output)

        try:
            return await asyncio.wait_for(collect(), timeout)
        finally:
            if process.returncode is None:
                await asyncio.shield(controller.close())
            else:
                await asyncio.shield(controller.wait_closed())


class RenderProcessExecutor:
    def __init__(
        self,
        *,
        runner: Any | None = None,
        poll_seconds: float = 0.1,
    ) -> None:
        self._runner = runner or RenderProcessRunner()
        self._poll_seconds = poll_seconds
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

    @staticmethod
    def _check_output(path: Path, maximum: int) -> None:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RenderProcessFailure("ffmpeg_exit") from exc
        if size > maximum:
            raise RenderOutputTooLarge("render output exceeded limit")

    async def _monitor(
        self,
        output_path: Path,
        maximum: int,
        is_valid: Callable[[], bool],
    ) -> None:
        while True:
            if not is_valid():
                raise RenderSnapshotInvalidated("snapshot invalidated")
            self._check_output(output_path, maximum)
            await asyncio.sleep(self._poll_seconds)

    async def _run_started(
        self,
        process: Any,
        *,
        output_path: Path,
        maximum: int,
        is_valid: Callable[[], bool],
    ) -> int:
        controller = ProcessController(process)
        wait_task = self._track(asyncio.create_task(controller.wait()))
        monitor_task = self._track(
            asyncio.create_task(
                self._monitor(output_path, maximum, is_valid)
            )
        )
        try:
            done, _ = await asyncio.wait(
                (wait_task, monitor_task), return_when=asyncio.FIRST_COMPLETED
            )
            if monitor_task in done:
                await monitor_task
            code = await wait_task
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await controller.finish_io()
            if not is_valid():
                raise RenderSnapshotInvalidated("snapshot invalidated")
            self._check_output(output_path, maximum)
            if code != 0:
                raise RenderProcessFailure("ffmpeg_exit")
            return code
        finally:
            if process.returncode is None:
                await asyncio.shield(controller.close())
            else:
                await asyncio.shield(controller.wait_closed())
            for task in (wait_task, monitor_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(wait_task, monitor_task, return_exceptions=True)
            self._tasks.difference_update((wait_task, monitor_task))

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        output_path: Path,
        max_output_bytes: int,
        is_valid: Callable[[], bool],
        timeout: float,
    ) -> int:
        async def execute() -> int:
            if not is_valid():
                raise RenderSnapshotInvalidated("snapshot invalidated")
            try:
                process = await self._runner.spawn(argv, cwd=cwd)
            except FileNotFoundError as exc:
                raise RenderProcessFailure("ffmpeg_missing") from exc
            key = id(process)
            self._active_processes.add(key)
            try:
                return await self._run_started(
                    process,
                    output_path=output_path,
                    maximum=max_output_bytes,
                    is_valid=is_valid,
                )
            finally:
                self._active_processes.discard(key)

        try:
            return await asyncio.wait_for(execute(), timeout)
        except TimeoutError as exc:
            raise RenderProcessTimeout("render process timed out") from exc
