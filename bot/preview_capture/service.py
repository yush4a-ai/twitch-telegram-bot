from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .buffer import (
    FREE_DISK_RESERVE_BYTES,
    PARTIAL_FILE_MAX_BYTES,
    ROOT_HARD_MAX_BYTES,
    ManifestWatcher,
    RollingSegmentBuffer,
    build_hls_argv,
)
from .models import (
    CapabilityReason,
    CaptureCapability,
    CaptureEndReason,
    CaptureOutcome,
    CaptureSettings,
    CaptureStartResult,
    CaptureState,
    FileCaptureInput,
    SnapshotAcquireResult,
    UrlCaptureInput,
)
from .process import AsyncioProcessRunner, BoundedStderrTail, ProcessController


LOGGER = logging.getLogger("bot.preview_capture")
ROOT_MARKER_NAME = ".signalbot-preview-root-v1"
SESSION_MARKER_NAME = ".session.json"
ORPHAN_TTL_SECONDS = 30 * 60
MAX_CONCURRENT_CAPTURES = 2
DEFAULT_ROOT = Path(tempfile.gettempdir()) / "signalbot-preview"
_APPLICATION = "twitch-signalbot-preview"
_SCHEMA = 1
_CAPTURE_NAME = re.compile(r"capture-([0-9a-f]{32})\Z")
_DELETING_NAME = re.compile(r"\.deleting-([0-9a-f]{32})\Z")
_REPARSE_POINT = 0x400


def _safe_directory(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not bool(
                getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
            )
            and path.parent.resolve() == parent.resolve()
        )
    except (OSError, RuntimeError):
        return False


def _safe_regular(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not bool(
                getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
            )
            and path.parent.resolve() == parent.resolve()
        )
    except (OSError, RuntimeError):
        return False


class CaptureRootManager:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self._active: set[str] = set()
        self.available = self._initialize()

    def _initialize(self) -> bool:
        if self.root.exists() or self.root.is_symlink():
            if not _safe_directory(self.root, self.root.parent):
                return False
            return self.has_valid_root_marker()
        try:
            self.root.mkdir(parents=True, exist_ok=False)
            marker = self.root / ROOT_MARKER_NAME
            payload = {"application": _APPLICATION, "schema": _SCHEMA}
            with marker.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            return True
        except OSError:
            LOGGER.warning("Preview capture root initialization failed")
            return False

    def has_valid_root_marker(self) -> bool:
        marker = self.root / ROOT_MARKER_NAME
        if not _safe_regular(marker, self.root):
            return False
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        return payload == {"application": _APPLICATION, "schema": _SCHEMA}

    def create_session(self, *, now_unix: float | None = None) -> Path:
        if not self.available or not self.has_valid_root_marker():
            raise PermissionError("preview capture root is unavailable")
        session_id = uuid.uuid4().hex
        session = self.root / f"capture-{session_id}"
        session.mkdir(mode=0o700)
        try:
            self.write_session_marker(session, now_unix=now_unix)
        except BaseException:
            try:
                session.rmdir()
            except OSError:
                pass
            raise
        return session

    def write_session_marker(
        self, session: Path, *, now_unix: float | None = None
    ) -> None:
        match = _CAPTURE_NAME.fullmatch(session.name)
        if not match or not _safe_directory(session, self.root):
            raise PermissionError("session ownership validation failed")
        payload = {
            "application": _APPLICATION,
            "schema": _SCHEMA,
            "session_id": match.group(1),
            "updated_unix": time.time() if now_unix is None else now_unix,
        }
        temporary = session / f"{SESSION_MARKER_NAME}.tmp-{uuid.uuid4().hex}"
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, session / SESSION_MARKER_NAME)

    def _read_session_marker(
        self, session: Path, *, expected_session_id: str | None
    ) -> dict[str, Any] | None:
        marker = session / SESSION_MARKER_NAME
        if not _safe_regular(marker, session):
            return None
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            updated = float(payload["updated_unix"])
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            return None
        session_id = payload.get("session_id")
        if (
            payload.get("application") != _APPLICATION
            or payload.get("schema") != _SCHEMA
            or not isinstance(session_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", session_id) is None
            or (
                expected_session_id is not None
                and session_id != expected_session_id
            )
            or not math.isfinite(updated)
        ):
            return None
        return payload

    def register_active(self, session: Path) -> None:
        match = _CAPTURE_NAME.fullmatch(session.name)
        if match and _safe_directory(session, self.root):
            self._active.add(match.group(1))

    def unregister_active(self, session: Path) -> None:
        match = _CAPTURE_NAME.fullmatch(session.name)
        if match:
            self._active.discard(match.group(1))

    def _rename_for_deletion(self, session: Path, session_id: str) -> Path | None:
        if not _safe_directory(session, self.root):
            return None
        deleting = self.root / f".deleting-{uuid.uuid4().hex}"
        try:
            session.rename(deleting)
        except OSError:
            return None
        if (
            not _safe_directory(deleting, self.root)
            or not _DELETING_NAME.fullmatch(deleting.name)
            or self._read_session_marker(
                deleting, expected_session_id=session_id
            )
            is None
        ):
            return None
        return deleting

    def _remove_verified(self, session: Path, session_id: str) -> bool:
        deleting = self._rename_for_deletion(session, session_id)
        if deleting is None:
            return False
        try:
            shutil.rmtree(deleting)
        except OSError:
            LOGGER.warning("Preview capture session cleanup deferred")
            return False
        return True

    def remove_session(self, session: Path) -> bool:
        match = _CAPTURE_NAME.fullmatch(session.name)
        if (
            not match
            or not _safe_directory(session, self.root)
            or self._read_session_marker(
                session, expected_session_id=match.group(1)
            )
            is None
        ):
            return True
        self.unregister_active(session)
        return not self._remove_verified(session, match.group(1))

    def cleanup_orphans(self, *, now_unix: float | None = None) -> int:
        if not self.available or not self.has_valid_root_marker():
            return 0
        current = time.time() if now_unix is None else now_unix
        removed = 0
        try:
            children = tuple(self.root.iterdir())
        except OSError:
            LOGGER.warning("Preview capture orphan scan failed")
            return 0
        for child in children:
            match = _CAPTURE_NAME.fullmatch(child.name)
            deleting_match = _DELETING_NAME.fullmatch(child.name)
            if deleting_match and _safe_directory(child, self.root):
                payload = self._read_session_marker(
                    child, expected_session_id=None
                )
                if (
                    payload is not None
                    and current - float(payload["updated_unix"])
                    >= ORPHAN_TTL_SECONDS
                    and _safe_directory(child, self.root)
                    and _DELETING_NAME.fullmatch(child.name)
                ):
                    try:
                        shutil.rmtree(child)
                    except OSError:
                        LOGGER.warning(
                            "Preview capture deferred orphan cleanup failed"
                        )
                    else:
                        removed += 1
                continue
            if (
                not match
                or match.group(1) in self._active
                or not _safe_directory(child, self.root)
            ):
                continue
            payload = self._read_session_marker(
                child, expected_session_id=match.group(1)
            )
            if payload is None:
                continue
            if current - float(payload["updated_unix"]) < ORPHAN_TTL_SECONDS:
                continue
            if self._remove_verified(child, match.group(1)):
                removed += 1
        return removed

    def total_owned_bytes(self) -> int:
        if not self.available:
            return 0
        total = 0
        try:
            children = tuple(self.root.iterdir())
        except OSError:
            return ROOT_HARD_MAX_BYTES + 1
        for session in children:
            match = _CAPTURE_NAME.fullmatch(session.name)
            deleting_match = _DELETING_NAME.fullmatch(session.name)
            if not (match or deleting_match) or not _safe_directory(
                session, self.root
            ):
                continue
            if self._read_session_marker(
                session,
                expected_session_id=match.group(1) if match else None,
            ) is None:
                continue
            try:
                files = tuple(session.iterdir())
            except OSError:
                return ROOT_HARD_MAX_BYTES + 1
            for path in files:
                if _safe_regular(path, session):
                    try:
                        total += path.stat().st_size
                    except OSError:
                        return ROOT_HARD_MAX_BYTES + 1
        return total


ProbeCommand = Callable[..., Awaitable[tuple[int, bytes]]]


async def _bounded_probe_command(
    argv: Sequence[str], *, timeout: float = 5.0, max_output: int = 65536
) -> tuple[int, bytes]:
    kwargs: dict[str, Any] = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.STDOUT,
    }
    if os.name == "nt":
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(*argv, **kwargs)
    tail = BoundedStderrTail(max_output)

    async def read_output() -> None:
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                return
            tail.append(chunk)

    read_task = asyncio.create_task(read_output())
    wait_task = asyncio.create_task(process.wait())
    try:
        code = await asyncio.wait_for(asyncio.shield(wait_task), timeout)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await wait_task
        raise
    finally:
        await read_task
    return code, tail.bytes


async def probe_ffmpeg(
    *,
    executable: str | None = None,
    command_runner: ProbeCommand = _bounded_probe_command,
) -> CaptureCapability:
    resolved = executable or shutil.which("ffmpeg")
    if not resolved:
        return CaptureCapability(
            available=False, reason=CapabilityReason.MISSING_EXECUTABLE
        )
    try:
        version_code, version_output = await command_runner(
            (resolved, "-version"), timeout=5.0, max_output=65536
        )
        hls_code, hls_output = await command_runner(
            (resolved, "-hide_banner", "-h", "muxer=hls"),
            timeout=5.0,
            max_output=65536,
        )
    except Exception:
        return CaptureCapability(
            available=False, reason=CapabilityReason.PROBE_FAILED
        )
    if version_code != 0 or hls_code != 0:
        return CaptureCapability(
            available=False, reason=CapabilityReason.PROBE_FAILED
        )
    hls_text = hls_output.decode("utf-8", errors="replace")
    if "hls" not in hls_text.lower() or "temp_file" not in hls_text:
        return CaptureCapability(
            available=False, reason=CapabilityReason.UNSUPPORTED_HLS
        )
    version = version_output.decode("utf-8", errors="replace").splitlines()
    return CaptureCapability(
        available=True,
        ffmpeg_executable=resolved,
        ffmpeg_version=version[0][:256] if version else None,
    )


class ProgressWatchdog:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        first_grace: float = 45.0,
        stall_after: float = 30.0,
    ) -> None:
        self._monotonic = monotonic
        self._started = monotonic()
        self._last_complete: float | None = None
        self.first_grace = first_grace
        self.stall_after = stall_after

    def register_complete_segment(self) -> None:
        self._last_complete = self._monotonic()

    def observe_partial_size(self, _size_bytes: int) -> None:
        return

    @property
    def expired(self) -> bool:
        now = self._monotonic()
        if self._last_complete is None:
            return now - self._started > self.first_grace
        return now - self._last_complete > self.stall_after


class CaptureHandle:
    def __init__(
        self,
        *,
        service: CaptureService,
        session_dir: Path,
        process_controller: ProcessController,
        settings: CaptureSettings,
        sample_interval: float,
        snapshot_release_grace: float,
        first_segment_grace: float,
        stall_after: float,
    ) -> None:
        self._service = service
        self.session_dir = session_dir
        self._controller = process_controller
        self.buffer = RollingSegmentBuffer(
            session_dir, settings.buffer_seconds, settings.buffer_max_bytes
        )
        self._manifest = ManifestWatcher(session_dir)
        self._watchdog = ProgressWatchdog(
            first_grace=first_segment_grace, stall_after=stall_after
        )
        self._sample_interval = sample_interval
        self._snapshot_release_grace = snapshot_release_grace
        self.state = CaptureState.RUNNING
        self.outcome: CaptureOutcome | None = None
        self._done = asyncio.Event()
        self._close_task: asyncio.Task[CaptureOutcome] | None = None
        self._watcher_task = asyncio.create_task(self._watcher_loop())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        self._lifecycle_task = asyncio.create_task(self._observe_process())

    @property
    def active_pin_count(self) -> int:
        return self.buffer.active_pin_count

    @property
    def background_task_count(self) -> int:
        own = (self._watcher_task, self._watchdog_task, self._lifecycle_task)
        count = sum(not task.done() for task in own)
        if self._close_task is not None and not self._close_task.done():
            count += 1
        return count + self._controller.background_task_count

    def acquire_snapshot(
        self, window_seconds: float = 90
    ) -> SnapshotAcquireResult:
        return self.buffer.acquire_snapshot(window_seconds)

    def scan_manifest_now(self) -> int:
        result = self._manifest.scan()
        registered = 0
        for segment in result.segments:
            if self.buffer.register(segment):
                registered += 1
                self._watchdog.register_complete_segment()
        if registered:
            try:
                self._service.root_manager.write_session_marker(self.session_dir)
            except OSError:
                LOGGER.warning("Preview capture session marker update failed")
        return registered

    def _partial_bytes(self) -> int:
        total = 0
        try:
            paths = tuple(self.session_dir.iterdir())
        except OSError:
            return PARTIAL_FILE_MAX_BYTES + 1
        for path in paths:
            if not path.name.endswith(".tmp") or not _safe_regular(
                path, self.session_dir
            ):
                continue
            try:
                total += path.stat().st_size
            except OSError:
                return PARTIAL_FILE_MAX_BYTES + 1
        return total

    def _disk_pressure(self) -> bool:
        partial = self._partial_bytes()
        self._watchdog.observe_partial_size(partial)
        root_bytes = self._service.root_manager.total_owned_bytes()
        try:
            free = self._service.disk_usage(self._service.root_manager.root).free
        except OSError:
            return True
        return self.buffer.has_unrecoverable_disk_pressure(
            partial_bytes=partial, root_bytes=root_bytes, free_bytes=free
        )

    async def _watcher_loop(self) -> None:
        try:
            while self.state == CaptureState.RUNNING:
                self.scan_manifest_now()
                if self._disk_pressure():
                    self._schedule_close(CaptureEndReason.DISK_PRESSURE)
                    return
                await asyncio.sleep(self._sample_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("Preview capture watcher failed")
            self._schedule_close(CaptureEndReason.INTERNAL_ERROR)

    async def _watchdog_loop(self) -> None:
        try:
            while self.state == CaptureState.RUNNING:
                if self._watchdog.expired:
                    self._schedule_close(CaptureEndReason.STALLED)
                    return
                await asyncio.sleep(self._sample_interval)
        except asyncio.CancelledError:
            raise

    async def _observe_process(self) -> None:
        try:
            code = await self._controller.wait()
            await self._controller.finish_io()
        except asyncio.CancelledError:
            raise
        if self.state != CaptureState.RUNNING:
            return
        self.scan_manifest_now()
        self.outcome = CaptureOutcome(
            CaptureEndReason.CLEAN_EOF if code == 0 else CaptureEndReason.PROCESS_EXIT,
            exit_code=code,
        )
        self.state = CaptureState.ENDED
        for task in (self._watcher_task, self._watchdog_task):
            task.cancel()
        self._done.set()

    def _schedule_close(self, reason: CaptureEndReason) -> None:
        if self._close_task is None and self.state == CaptureState.RUNNING:
            self._close_task = asyncio.create_task(self._close_impl(reason))

    async def wait(self) -> CaptureOutcome:
        await self._done.wait()
        assert self.outcome is not None
        return self.outcome

    async def _close_impl(self, reason: CaptureEndReason) -> CaptureOutcome:
        prior = self.outcome
        self.state = CaptureState.STOPPING
        self.buffer.begin_close()
        code = await self._controller.close()
        self.scan_manifest_now()

        current = asyncio.current_task()
        tasks = (self._watcher_task, self._watchdog_task, self._lifecycle_task)
        for task in tasks:
            if task is not current and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not current), return_exceptions=True
        )

        released = await self.buffer.wait_for_snapshot_release(
            self._snapshot_release_grace
        )
        if not released:
            self.buffer.force_invalidate_snapshots()
        cleanup_deferred = self._service.root_manager.remove_session(
            self.session_dir
        )
        self._service._discard(self)
        self.outcome = prior or CaptureOutcome(
            reason=reason,
            exit_code=code,
            cleanup_deferred=cleanup_deferred,
        )
        if prior is not None and cleanup_deferred:
            self.outcome = CaptureOutcome(
                reason=prior.reason,
                exit_code=prior.exit_code,
                diagnostic_code=prior.diagnostic_code,
                cleanup_deferred=True,
            )
        self.state = CaptureState.CLOSED
        self._done.set()
        return self.outcome

    async def close(self) -> CaptureOutcome:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_impl(CaptureEndReason.SHUTDOWN)
            )
        try:
            return await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def wait_closed(self) -> CaptureOutcome:
        if self._close_task is None:
            return await self.close()
        return await asyncio.shield(self._close_task)


class CaptureService:
    def __init__(
        self,
        *,
        root: Path = DEFAULT_ROOT,
        capability: CaptureCapability,
        settings: CaptureSettings | None = None,
        process_runner: Any | None = None,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
        sample_interval: float = 1.0,
        first_segment_grace: float = 45.0,
        stall_after: float = 30.0,
        snapshot_release_grace: float = 2.0,
        terminate_timeout: float = 3.0,
        kill_timeout: float = 2.0,
    ) -> None:
        self.root_manager = CaptureRootManager(root)
        if not self.root_manager.available:
            capability = CaptureCapability(
                available=False, reason=CapabilityReason.ROOT_UNOWNED
            )
        self.settings = settings or CaptureSettings()
        if not (
            type(self.settings.buffer_seconds) is int
            and 30 <= self.settings.buffer_seconds <= 240
            and type(self.settings.buffer_max_bytes) is int
            and 32 * 1024 * 1024
            <= self.settings.buffer_max_bytes
            <= 150 * 1024 * 1024
        ):
            capability = CaptureCapability(
                available=False, reason=CapabilityReason.CONFIG_ERROR
            )
        self.capability = capability
        self.process_runner = process_runner or AsyncioProcessRunner()
        self.disk_usage = disk_usage
        self.sample_interval = sample_interval
        self.first_segment_grace = first_segment_grace
        self.stall_after = stall_after
        self.snapshot_release_grace = snapshot_release_grace
        self.terminate_timeout = terminate_timeout
        self.kill_timeout = kill_timeout
        self._handles: set[CaptureHandle] = set()
        self._starting = 0
        self._start_lock = asyncio.Lock()
        self._starts_idle = asyncio.Event()
        self._starts_idle.set()
        self._closing = False
        if self.root_manager.available:
            self.root_manager.cleanup_orphans()

    @classmethod
    async def create(
        cls,
        *,
        root: Path = DEFAULT_ROOT,
        settings: CaptureSettings | None = None,
    ) -> CaptureService:
        return cls(
            root=root, capability=await probe_ffmpeg(), settings=settings
        )

    @property
    def active_handle_count(self) -> int:
        return sum(
            handle.state
            in {
                CaptureState.STARTING,
                CaptureState.RUNNING,
                CaptureState.STOPPING,
            }
            for handle in self._handles
        )

    def _validate_input(
        self, source: UrlCaptureInput | FileCaptureInput
    ) -> bool:
        if isinstance(source, UrlCaptureInput):
            try:
                parsed = urlsplit(source.url)
            except ValueError:
                return False
            return (
                parsed.scheme in {"http", "https"}
                and bool(parsed.netloc)
                and not any(char in source.url for char in "\r\n\0")
            )
        if isinstance(source, FileCaptureInput):
            path = source.path
            try:
                info = path.lstat()
            except OSError:
                return False
            return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        return False

    async def start(
        self, source: UrlCaptureInput | FileCaptureInput
    ) -> CaptureStartResult:
        if not self.capability.available:
            return CaptureStartResult(
                outcome=CaptureOutcome(
                    CaptureEndReason.CAPABILITY_UNAVAILABLE,
                    diagnostic_code=(
                        self.capability.reason.value
                        if self.capability.reason is not None
                        else None
                    ),
                )
            )
        if not self._validate_input(source):
            return CaptureStartResult(
                outcome=CaptureOutcome(CaptureEndReason.INVALID_INPUT)
            )
        async with self._start_lock:
            if (
                self._closing
                or self.active_handle_count + self._starting
                >= MAX_CONCURRENT_CAPTURES
            ):
                return CaptureStartResult(
                    outcome=CaptureOutcome(
                        CaptureEndReason.CAPABILITY_UNAVAILABLE,
                        diagnostic_code=(
                            "closing" if self._closing else "capacity"
                        ),
                    )
                )
            self._starting += 1
            self._starts_idle.clear()
        result: CaptureStartResult | None = None
        try:
            session = self.root_manager.create_session()
            self.root_manager.register_active(session)
            argv = build_hls_argv(
                self.capability.ffmpeg_executable or "ffmpeg", source, session
            )
            process = await self.process_runner.spawn(argv, cwd=session)
        except PermissionError:
            if "session" in locals():
                self.root_manager.remove_session(session)
            result = CaptureStartResult(
                outcome=CaptureOutcome(CaptureEndReason.PERMISSION_DENIED)
            )
        except OSError:
            if "session" in locals():
                self.root_manager.remove_session(session)
            result = CaptureStartResult(
                outcome=CaptureOutcome(CaptureEndReason.START_FAILED)
            )
        except Exception:
            if "session" in locals():
                self.root_manager.remove_session(session)
            result = CaptureStartResult(
                outcome=CaptureOutcome(CaptureEndReason.INTERNAL_ERROR)
            )
        except BaseException:
            if "session" in locals():
                self.root_manager.remove_session(session)
            raise
        else:
            controller = ProcessController(
                process,
                terminate_timeout=self.terminate_timeout,
                kill_timeout=self.kill_timeout,
            )
            handle = CaptureHandle(
                service=self,
                session_dir=session,
                process_controller=controller,
                settings=self.settings,
                sample_interval=self.sample_interval,
                snapshot_release_grace=self.snapshot_release_grace,
                first_segment_grace=self.first_segment_grace,
                stall_after=self.stall_after,
            )
            result = CaptureStartResult(handle=handle)
        finally:
            finalize_task = asyncio.create_task(
                self._finalize_start(result)
            )
            try:
                await asyncio.shield(finalize_task)
            except asyncio.CancelledError:
                await asyncio.shield(finalize_task)
                if result is not None and isinstance(
                    result.handle, CaptureHandle
                ):
                    await asyncio.shield(result.handle.close())
                raise
        assert result is not None
        return result

    async def _finalize_start(
        self, result: CaptureStartResult | None
    ) -> None:
        async with self._start_lock:
            self._starting -= 1
            if self._starting == 0:
                self._starts_idle.set()
            if result is not None and isinstance(result.handle, CaptureHandle):
                self._handles.add(result.handle)

    def _discard(self, handle: CaptureHandle) -> None:
        self._handles.discard(handle)

    async def close(self) -> None:
        async with self._start_lock:
            self._closing = True
        await self._starts_idle.wait()
        await asyncio.gather(
            *(handle.close() for handle in tuple(self._handles)),
            return_exceptions=False,
        )
