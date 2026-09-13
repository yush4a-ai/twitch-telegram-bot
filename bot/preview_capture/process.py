from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("bot.preview_capture")
SIGTERM = signal.SIGTERM
SIGKILL = getattr(signal, "SIGKILL", 9)


def _unsupported_killpg(_pid: int, _signal_number: int) -> None:
    raise OSError("process groups are unavailable on this platform")


class BoundedStderrTail:
    def __init__(self, max_bytes: int = 65536) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._data = bytearray()

    @property
    def bytes(self) -> bytes:
        return bytes(self._data)

    def append(self, chunk: bytes) -> None:
        self._data.extend(chunk)
        overflow = len(self._data) - self._max_bytes
        if overflow > 0:
            del self._data[:overflow]


async def drain_stderr(reader: Any, tail: BoundedStderrTail) -> None:
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return
        tail.append(chunk)


class AsyncioProcessRunner:
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


class ProcessController:
    def __init__(
        self,
        process: Any,
        *,
        platform: str | None = None,
        terminate_timeout: float = 3.0,
        kill_timeout: float = 2.0,
        windows_break_timeout: float = 0.25,
        killpg: Callable[[int, int], None] | None = None,
    ) -> None:
        self.process = process
        self.platform = platform or os.name
        self.terminate_timeout = terminate_timeout
        self.kill_timeout = kill_timeout
        self.windows_break_timeout = windows_break_timeout
        self._killpg = killpg or getattr(os, "killpg", _unsupported_killpg)
        self.stderr_tail = BoundedStderrTail()
        self._wait_task = asyncio.create_task(process.wait())
        self._stderr_task = asyncio.create_task(
            drain_stderr(process.stderr, self.stderr_tail)
        )
        self._close_task: asyncio.Task[int] | None = None

    @property
    def background_task_count(self) -> int:
        tasks = (self._wait_task, self._stderr_task, self._close_task)
        return sum(task is not None and not task.done() for task in tasks)

    async def wait(self) -> int:
        return await asyncio.shield(self._wait_task)

    async def finish_io(self) -> None:
        await asyncio.shield(self._stderr_task)

    async def _wait_for_exit(self, timeout: float) -> bool:
        if self.process.returncode is not None:
            await self.wait()
            return True
        try:
            await asyncio.wait_for(asyncio.shield(self._wait_task), timeout)
        except TimeoutError:
            return False
        return True

    async def _close_impl(self) -> int:
        if self.process.returncode is None:
            if self.platform == "nt":
                try:
                    self.process.send_signal(
                        getattr(signal, "CTRL_BREAK_EVENT", 1)
                    )
                except (AttributeError, OSError, ProcessLookupError):
                    pass
                exited = await self._wait_for_exit(self.windows_break_timeout)
                if not exited:
                    try:
                        self.process.terminate()
                    except OSError:
                        pass
                    exited = await self._wait_for_exit(self.terminate_timeout)
                if not exited:
                    try:
                        self.process.kill()
                    except OSError:
                        pass
                    await self._wait_for_exit(self.kill_timeout)
            else:
                try:
                    self._killpg(self.process.pid, SIGTERM)
                except OSError:
                    pass
                exited = await self._wait_for_exit(self.terminate_timeout)
                if not exited:
                    try:
                        self._killpg(self.process.pid, SIGKILL)
                    except OSError:
                        pass
                    await self._wait_for_exit(self.kill_timeout)

        code = await self.wait()
        await self.finish_io()
        return code

    async def close(self) -> int:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_impl())
        try:
            return await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def wait_closed(self) -> int:
        if self._close_task is None:
            return await self.close()
        return await asyncio.shield(self._close_task)

    def log_sanitized_failure(self, reason: str) -> None:
        LOGGER.warning("Preview capture process failed: reason=%s", reason)
