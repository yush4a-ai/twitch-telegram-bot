from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bot.preview_capture import ProcessController


STDOUT_MAX_BYTES = 16 * 1024
PIPE_EOF_TIMEOUT_SECONDS = 0.25


def _classify_streamlink_failure(stderr_bytes: bytes) -> str:
    """Reduce bounded stderr to a fixed, non-secret diagnostic category."""
    text = stderr_bytes.lower()
    checks = (
        ("client_integrity", (b"client-integrity", b"client integrity", b"integrity token", b"integrity check")),
        ("webbrowser_unavailable", (b"webbrowser", b"web browser", b"chromium")),
        ("no_playable_streams", (b"no playable streams found", b"no streams found")),
        ("stream_selection", (b"specified stream(s)", b"could not be found")),
        ("http_429", (b" 429", b"429 ", b"too many requests")),
        ("http_403", (b" 403", b"403 ", b"forbidden")),
        ("http_401", (b" 401", b"401 ", b"unauthorized")),
        ("http_500", (b" 500", b"500 ", b"internal server error")),
        ("http_502", (b" 502", b"502 ", b"bad gateway")),
        ("http_503", (b" 503", b"503 ", b"service unavailable")),
        ("http_504", (b" 504", b"504 ", b"gateway timeout")),
        ("read_timeout", (b"read timed out", b"readtimeout")),
        ("connect_timeout", (b"connect timeout", b"connecttimeout")),
        ("remote_closed", (b"remote end closed connection", b"remotedisconnected")),
        ("proxy_error", (b"proxyerror", b"proxy error")),
        ("dns_error", (b"name resolution", b"name or service not known", b"nodename nor servname", b"getaddrinfo failed")),
        ("tls_error", (b"certificate verify failed", b"ssl error", b"tls error")),
        ("connection_reset", (b"connection reset",)),
        ("connection_refused", (b"connection refused",)),
        ("network_unreachable", (b"network is unreachable",)),
        ("max_retries", (b"max retries exceeded",)),
        ("url_open_gql", (b"unable to open url: https://gql.twitch.tv/",)),
        ("url_open_usher", (b"unable to open url: https://usher.ttvnw.net/",)),
        ("url_open_playlist", (b"playlist.ttvnw.net",)),
        ("url_open_twitch", (b"unable to open url: https://www.twitch.tv/",)),
        ("url_open_failed", (b"unable to open url",)),
        ("no_plugin", (b"no plugin can handle url",)),
        ("twitch_api_error", (b"streaming access token", b"access token", b"gql.twitch.tv")),
    )
    for code, needles in checks:
        if any(needle in text for needle in needles):
            return code
    return "process_failed"


class ResolverProcessTimeout(TimeoutError):
    pass


class StreamlinkProcessRunner:
    async def spawn(self, argv: Sequence[str]) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return await asyncio.create_subprocess_exec(*argv, **kwargs)


class _ExitAwareReader:
    def __init__(
        self,
        reader: Any,
        process_exited: asyncio.Event,
        eof_timeout: float,
    ) -> None:
        self._reader = reader
        self._process_exited = process_exited
        self._eof_timeout = eof_timeout

    async def read(self, size: int = -1) -> bytes:
        read_task = asyncio.create_task(
            self._reader.read(size), name="preview-source-pipe-read"
        )
        exit_task = asyncio.create_task(
            self._process_exited.wait(), name="preview-source-process-exit"
        )
        try:
            done, _pending = await asyncio.wait(
                (read_task, exit_task), return_when=asyncio.FIRST_COMPLETED
            )
            if read_task in done:
                return read_task.result()
            try:
                return await asyncio.wait_for(read_task, self._eof_timeout)
            except TimeoutError:
                return b""
        finally:
            for task in (read_task, exit_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read_task, exit_task, return_exceptions=True)


class _ObservedProcess:
    def __init__(self, process: Any, *, pipe_eof_timeout: float) -> None:
        self._process = process
        self._exited = asyncio.Event()
        self.stdout = _ExitAwareReader(
            process.stdout, self._exited, pipe_eof_timeout
        )
        self.stderr = _ExitAwareReader(
            process.stderr, self._exited, pipe_eof_timeout
        )

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        code = await self._process.wait()
        self._exited.set()
        return code

    def send_signal(self, value: int) -> None:
        self._process.send_signal(value)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


@dataclass(frozen=True, repr=False)
class ResolverExecutionResult:
    exit_code: int
    stdout_bytes: bytes
    stdout_overflowed: bool
    failure_code: str | None = None

    def __repr__(self) -> str:
        return (
            "ResolverExecutionResult("
            f"exit_code={self.exit_code}, "
            f"stdout_overflowed={self.stdout_overflowed}, "
            f"failure_code={self.failure_code!r})"
        )


async def _drain_bounded(reader: Any, max_bytes: int) -> tuple[bytes, bool]:
    kept = bytearray()
    overflowed = False
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return bytes(kept), overflowed
        remaining = max_bytes - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
        if len(chunk) > remaining:
            overflowed = True


class ResolverProcessExecutor:
    def __init__(
        self,
        *,
        runner: Any | None = None,
        timeout: float = 20.0,
        stdout_max_bytes: int = STDOUT_MAX_BYTES,
        terminate_timeout: float = 3.0,
        kill_timeout: float = 2.0,
        windows_break_timeout: float = 0.25,
        platform: str | None = None,
        pipe_eof_timeout: float = PIPE_EOF_TIMEOUT_SECONDS,
    ) -> None:
        if timeout <= 0 or stdout_max_bytes <= 0 or pipe_eof_timeout <= 0:
            raise ValueError(
                "timeout, stdout_max_bytes and pipe_eof_timeout must be positive"
            )
        self.runner = runner or StreamlinkProcessRunner()
        self.timeout = timeout
        self.stdout_max_bytes = stdout_max_bytes
        self.terminate_timeout = terminate_timeout
        self.kill_timeout = kill_timeout
        self.windows_break_timeout = windows_break_timeout
        self.platform = platform
        self.pipe_eof_timeout = pipe_eof_timeout

    async def run(self, argv: Sequence[str]) -> ResolverExecutionResult:
        spawned_process = await self.runner.spawn(tuple(argv))
        process = _ObservedProcess(
            spawned_process, pipe_eof_timeout=self.pipe_eof_timeout
        )
        controller = ProcessController(
            process,
            platform=self.platform,
            terminate_timeout=self.terminate_timeout,
            kill_timeout=self.kill_timeout,
            windows_break_timeout=self.windows_break_timeout,
        )
        stdout_task = asyncio.create_task(
            _drain_bounded(process.stdout, self.stdout_max_bytes),
            name="preview-source-stdout-drain",
        )

        async def cleanup() -> None:
            await controller.close()
            await stdout_task

        try:
            try:
                exit_code = await asyncio.wait_for(
                    controller.wait(), self.timeout
                )
            except TimeoutError as error:
                cleanup_task = asyncio.create_task(
                    cleanup(), name="preview-source-timeout-cleanup"
                )
                await asyncio.shield(cleanup_task)
                raise ResolverProcessTimeout() from error
            await controller.finish_io()
            stdout_bytes, stdout_overflowed = await stdout_task
            failure_code = (
                _classify_streamlink_failure(
                    stdout_bytes + b"\n" + controller.stderr_tail.bytes
                )
                if exit_code != 0
                else None
            )
            return ResolverExecutionResult(
                exit_code=exit_code,
                stdout_bytes=stdout_bytes,
                stdout_overflowed=stdout_overflowed,
                failure_code=failure_code,
            )
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                cleanup(), name="preview-source-cancel-cleanup"
            )
            await asyncio.shield(cleanup_task)
            raise
        except ResolverProcessTimeout:
            raise
        except BaseException:
            cleanup_task = asyncio.create_task(
                cleanup(), name="preview-source-error-cleanup"
            )
            await asyncio.shield(cleanup_task)
            raise
