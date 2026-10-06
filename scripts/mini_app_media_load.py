"""Bounded local media load probe. No bot client, Twitch network, or live DB."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from bot.database import PreviewDestinationState
from bot.live_post import LivePostContent, LivePostMediaResult, LivePostMediaStatus, LocalAnimation, TelegramAnimation
from bot.preview_runtime import PreviewManager, PreviewObservation
from bot.telegram_send_budget import TelegramSendBudget


PROFILES = {"1x1000": (1, 1000, 1000, 1), "100x10": (100, 10, 1000, 1), "1000x5": (5000, 1, 1000, 5)}


@dataclass(frozen=True)
class LoadLimits:
    max_seconds: float = 25.0
    max_tasks: int = 96
    max_rss_growth_mb: int = 256
    max_cpu_seconds: float = 20.0
    max_temp_disk_mb: int = 64
    max_pending_signals: int = 80

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 30
            or not 0 < self.max_tasks <= 256
            or not 0 < self.max_rss_growth_mb <= 512
            or not math.isfinite(self.max_cpu_seconds) or not 0 < self.max_cpu_seconds <= 60
            or not 0 < self.max_temp_disk_mb <= 256
            or not 0 < self.max_pending_signals <= 256
        ):
            raise ValueError("invalid load limits")


class ResourceStop(RuntimeError):
    def __init__(self, reason: str, db_path: str, metrics: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.db_path = db_path
        self.metrics = metrics or {}


def _rss_bytes() -> int:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t), ("paged_pool", ctypes.c_size_t),
                ("peak_nonpaged_pool", ctypes.c_size_t), ("nonpaged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
            ]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        current_process = ctypes.windll.kernel32.GetCurrentProcess
        current_process.restype = ctypes.c_void_p
        memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        memory_info.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        memory_info.restype = ctypes.c_int
        if not memory_info(
            current_process(), ctypes.byref(counters), counters.cb
        ):
            raise OSError("RSS unavailable")
        return int(counters.working_set)
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak * (1 if os.uname().sysname == "Darwin" else 1024))


def _open_child_handle(pid: int) -> int:
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x0410, 0, pid)
    if not handle:
        raise OSError("encoder process handle unavailable")
    return handle


def _child_usage(pid: int, retained_handle: int | None = None, *, exited: bool = False) -> tuple[int, float]:
    """Current working set and user+kernel CPU for a local encoder process."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t), ("paged_pool", ctypes.c_size_t),
                ("peak_nonpaged_pool", ctypes.c_size_t), ("nonpaged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
            ]
        class FileTime(ctypes.Structure):
            _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]
        kernel = ctypes.windll.kernel32
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = retained_handle or _open_child_handle(pid)
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            memory_info.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
            memory_info.restype = ctypes.c_int
            if not memory_info(handle, ctypes.byref(counters), counters.cb) and not exited:
                raise OSError("encoder RSS unavailable")
            created, exited, kernel_time, user_time = (FileTime() for _ in range(4))
            kernel.GetProcessTimes.argtypes = tuple(ctypes.c_void_p for _ in range(5))
            kernel.GetProcessTimes.restype = ctypes.c_int
            if not kernel.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            ):
                raise OSError("encoder CPU unavailable")
            ticks = lambda value: (value.high << 32) + value.low
            return int(counters.working_set), (ticks(kernel_time) + ticks(user_time)) / 10_000_000
        finally:
            if retained_handle is None:
                kernel.CloseHandle(handle)
    if os.path.exists(f"/proc/{pid}/statm"):
        with open(f"/proc/{pid}/statm", encoding="ascii") as handle:
            rss_pages = int(handle.read().split()[1])
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])
        return rss_pages * os.sysconf("SC_PAGE_SIZE"), ticks / os.sysconf("SC_CLK_TCK")
    raise OSError("encoder process measurement unavailable")


def _finished_children_cpu() -> float:
    if os.name == "nt":
        return 0.0
    import resource
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


class _TempDestinations:
    def __init__(self, path: Path, streams: int, recipients_per_stream: int, selections: int):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE destinations(chat_id INTEGER, login TEXT, enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(chat_id,login))")
        self.conn.execute("CREATE INDEX destinations_login ON destinations(login)")
        rows = (
            (100000 + stream * recipients_per_stream + recipient if selections == 1 else 100000 + stream // selections,
             f"s{stream:05d}")
            for stream in range(streams) for recipient in range(recipients_per_stream)
        )
        self.conn.executemany("INSERT INTO destinations(chat_id,login) VALUES (?,?)", rows)
        self.conn.commit()
        self.queries = 0

    def _state(self, chat_id: int, login: str, enabled: bool) -> PreviewDestinationState:
        return PreviewDestinationState(
            chat_id, login, True, enabled, True, "live-1", chat_id + 700000,
            "photo", False, None,
        )

    async def list_preview_destination_states(self, login: str) -> list[PreviewDestinationState]:
        self.queries += 1
        if self.queries % 128 == 0:
            await asyncio.sleep(0)
        rows = self.conn.execute("SELECT chat_id,enabled FROM destinations WHERE login=?", (login,)).fetchall()
        return [self._state(row[0], login, bool(row[1])) for row in rows]

    async def get_preview_destination_state(self, chat_id: int, login: str) -> PreviewDestinationState | None:
        row = self.conn.execute(
            "SELECT enabled FROM destinations WHERE chat_id=? AND login=?", (chat_id, login)
        ).fetchone()
        return self._state(chat_id, login, bool(row[0])) if row else None

    def expire_selection(self, login: str) -> None:
        self.conn.execute("UPDATE destinations SET enabled=0 WHERE login=?", (login,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class _FakeProvider:
    def __init__(self, root: Path, encoder: str):
        self.root = root
        self.encoder = encoder
        self.active = 0
        self.peak_capture = 0
        self.encoding = 0
        self.peak_encode = 0
        self.capture_count = 0
        self.opened: list[str] = []
        self.closed: set[str] = set()
        self.codec_verified = 0
        self.children: dict[int, asyncio.subprocess.Process] = {}
        self.child_handles: dict[int, int] = {}
        self.child_cpu_seen: dict[int, float] = {}
        self.child_cpu_baseline = _finished_children_cpu()
        self.child_rss_peak = 0

    async def open_session(self, key, *, max_height: int | None = None):
        self.active += 1
        self.peak_capture = max(self.peak_capture, self.active)
        self.opened.append(key.twitch_login)
        return _FakeSession(self, key.twitch_login)

    def child_usage(self) -> tuple[int, float]:
        current_rss = 0
        for pid, process in tuple(self.children.items()):
            try:
                rss, cpu = _child_usage(
                    pid, self.child_handles.get(pid), exited=process.returncode is not None,
                )
            except OSError:
                if process.returncode is None:
                    raise
                continue
            current_rss += rss
            self.child_cpu_seen[pid] = max(self.child_cpu_seen.get(pid, 0), cpu)
        self.child_rss_peak = max(self.child_rss_peak, current_rss)
        sampled_cpu = sum(self.child_cpu_seen.values())
        finished_cpu = max(0.0, _finished_children_cpu() - self.child_cpu_baseline)
        return current_rss, max(sampled_cpu, finished_cpu)

    async def run_tool(self, executable: str, *arguments: str, timeout: float) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            executable, *arguments,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        if os.name == "nt":
            try:
                self.child_handles[process.pid] = _open_child_handle(process.pid)
            except BaseException:
                process.kill()
                await process.communicate()
                raise
        self.children[process.pid] = process
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            self.child_usage()
            return process.returncode, stdout, stderr
        except BaseException:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(process.communicate(), timeout=2)
            self.child_usage()
            raise
        finally:
            try:
                if os.name == "nt":
                    handle = self.child_handles.pop(process.pid)
                    try:
                        _rss, cpu = _child_usage(process.pid, handle, exited=True)
                        self.child_cpu_seen[process.pid] = max(self.child_cpu_seen.get(process.pid, 0), cpu)
                    finally:
                        ctypes.windll.kernel32.CloseHandle(handle)
            finally:
                self.children.pop(process.pid, None)


class _FakeSession:
    def __init__(self, provider: _FakeProvider, login: str):
        self.provider = provider
        self.login = login

    async def create_artifact(self, request):
        provider = self.provider
        provider.capture_count += 1
        provider.encoding += 1
        provider.peak_encode = max(provider.peak_encode, provider.encoding)
        path = provider.root / f"{self.login}-{request.generation.generation}.mp4"
        try:
            if provider.encoder == "synthetic":
                path.write_bytes(b"synthetic-only")
                await asyncio.sleep(0.01)
            else:
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    raise RuntimeError("ffmpeg unavailable")
                returncode, _stdout, stderr = await provider.run_tool(
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=blue:s=320x180:r=15", "-t", "24", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
                    "-an", "-movflags", "+faststart", "-y", str(path),
                    timeout=6,
                )
                if returncode != 0 or not path.is_file():
                    raise RuntimeError(f"ffmpeg exited {returncode}: {stderr[:120]!r}")
                ffprobe = shutil.which("ffprobe")
                if not ffprobe:
                    raise RuntimeError("ffprobe unavailable")
                probe_code, stdout, _stderr = await provider.run_tool(
                    ffprobe, "-v", "error", "-show_entries", "stream=codec_name,codec_type",
                    "-of", "json", str(path),
                    timeout=3,
                )
                streams = json.loads(stdout).get("streams", []) if probe_code == 0 else []
                if len(streams) != 1 or streams[0] != {"codec_name": "h264", "codec_type": "video"}:
                    raise RuntimeError("artifact is not silent H.264")
                provider.codec_verified += 1
            return LocalAnimation(path, 24)
        finally:
            provider.encoding -= 1

    async def release_artifact(self, artifact):
        Path(artifact.path).unlink(missing_ok=True)

    async def close(self):
        self.provider.active -= 1
        self.provider.closed.add(self.login)


class _FakeTelegram:
    def __init__(self):
        self.local_uploads = 0
        self.file_id_reuses = 0
        self.edits = 0
        self.ordinary = 0
        self.photos = 0

    async def apply_animation(self, *, target, animation, **_kwargs):
        self.edits += 1
        if isinstance(animation, LocalAnimation):
            self.local_uploads += 1
        elif isinstance(animation, TelegramAnimation):
            self.file_id_reuses += 1
        else:
            raise TypeError("unexpected artifact")
        await asyncio.sleep(0)
        return LivePostMediaResult(LivePostMediaStatus.APPLIED, f"fake-file-{target.twitch_login}")

    async def send_ordinary(self, kind: str) -> None:
        await asyncio.sleep(0)
        if kind == "photo":
            self.photos += 1
        else:
            self.ordinary += 1


def _p95(samples: list[float]) -> float:
    return round(sorted(samples)[math.ceil(len(samples) * 0.95) - 1] * 1000, 3)


async def run_profile(
    profile: str, *, encoder: str = "h264", limits: LoadLimits = LoadLimits(),
) -> dict[str, object]:
    if profile not in PROFILES or encoder not in {"h264", "synthetic"}:
        raise ValueError("unapproved load profile or encoder")
    streams, recipients_per_stream, viewers, selections = PROFILES[profile]
    started = time.monotonic()
    cpu_started = time.process_time()
    baseline_rss = _rss_bytes()
    with tempfile.TemporaryDirectory(prefix="twitchsignal-media-load-") as temporary:
        root = Path(temporary)
        db_path = root / "load.db"
        destinations = _TempDestinations(db_path, streams, recipients_per_stream, selections)
        provider = _FakeProvider(root, encoder)
        sender = _FakeTelegram()
        budget = TelegramSendBudget(global_interval=0.0001 if encoder == "synthetic" else 0.04)
        manager = PreviewManager(
            destinations, sender, provider, enabled=True,
            initial_delay_seconds=0, interval_seconds=300, max_concurrent_jobs=2,
            max_active_sessions=2, job_timeout_seconds=7, poll_interval_seconds=60,
            send_budget=budget,
            build_content=lambda _observation, _destination: LivePostContent("Live", None),
        )
        observations = tuple(
            PreviewObservation(f"s{stream:05d}", True, f"physical-{stream}", "Title", "Game", 1, None)
            for stream in range(streams)
        )
        peak_rss = baseline_rss
        peak_disk = 0
        peak_tasks = 0
        queue_peak = 0
        deferred_peak = 0
        active_job_peak = 0
        normal_pending = 0
        signal_latencies: list[float] = []
        photo_latencies: list[float] = []
        photo_count = 0
        normal_count = 0

        def sample() -> None:
            nonlocal peak_rss, peak_disk, peak_tasks, queue_peak, deferred_peak, active_job_peak
            elapsed = time.monotonic() - started
            child_rss, child_cpu = provider.child_usage()
            rss = _rss_bytes() + child_rss
            cpu_seconds = time.process_time() - cpu_started + child_cpu
            disk = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
            tasks = sum(not task.done() for task in asyncio.all_tasks())
            health = manager.health_snapshot()
            peak_rss = max(peak_rss, rss)
            peak_disk = max(peak_disk, disk)
            peak_tasks = max(peak_tasks, tasks)
            queue_peak = max(queue_peak, normal_pending)
            deferred_peak = max(deferred_peak, health["deferred_sessions"])
            active_job_peak = max(active_job_peak, health["active_jobs"])
            partial = {
                "profile": profile, "encoder": encoder,
                "capture_count": provider.capture_count,
                "peak_capture": provider.peak_capture,
                "peak_encode": provider.peak_encode,
                "codec_verified": provider.codec_verified,
                "video_edits_started": sender.edits,
                "local_uploads": sender.local_uploads,
                "file_id_reuses": sender.file_id_reuses,
                "deferred_peak": deferred_peak,
                "normal_signals": normal_count,
                "photo_signals": photo_count,
                "normal_signal_p95_ms": _p95(signal_latencies) if signal_latencies else None,
                "photo_signal_p95_ms": _p95(photo_latencies) if photo_latencies else None,
                "queue_peak": queue_peak, "task_peak": peak_tasks,
                "rss_peak_mb": round(peak_rss / 1048576, 2),
                "rss_growth_mb": round((peak_rss - baseline_rss) / 1048576, 2),
                "cpu_python_and_children_seconds": round(cpu_seconds, 3),
                "child_rss_peak_mb": round(provider.child_rss_peak / 1048576, 2),
                "temp_disk_peak_mb": round(peak_disk / 1048576, 3),
                "elapsed_seconds": round(elapsed, 3),
                "external_sends": 0,
            }
            if elapsed > limits.max_seconds:
                raise ResourceStop("elapsed time", str(db_path), partial)
            if tasks > limits.max_tasks:
                raise ResourceStop("task count", str(db_path), partial)
            if rss - baseline_rss > limits.max_rss_growth_mb * 1024 * 1024:
                raise ResourceStop("RSS growth", str(db_path), partial)
            if cpu_seconds > limits.max_cpu_seconds:
                raise ResourceStop("CPU time", str(db_path), partial)
            if disk > limits.max_temp_disk_mb * 1024 * 1024:
                raise ResourceStop("temporary disk", str(db_path), partial)
            if normal_pending > limits.max_pending_signals:
                raise ResourceStop("signal queue", str(db_path), partial)
            if not health["manager_running"]:
                raise ResourceStop("preview manager stopped", str(db_path), partial)

        async def ordinary_signal(kind: str) -> None:
            nonlocal normal_pending, photo_count, normal_count
            queued = time.monotonic()
            normal_pending += 1
            try:
                await budget.wait_turn(normal=True)
                await sender.send_ordinary(kind)
                if kind == "photo":
                    photo_latencies.append(time.monotonic() - queued)
                    photo_count += 1
                else:
                    signal_latencies.append(time.monotonic() - queued)
                    normal_count += 1
            finally:
                normal_pending -= 1

        signal_tasks: list[asyncio.Task] = []
        try:
            manager.start()
            manager.observe_cycle(observations)
            await asyncio.sleep(0.01)
            sample()
            signal_tasks = [asyncio.create_task(ordinary_signal("photo" if i % 2 else "normal")) for i in range(40)]
            expected = min(streams, 2) * recipients_per_stream
            while provider.capture_count < min(streams, 2) or sender.edits < expected or any(not t.done() for t in signal_tasks):
                sample()
                await asyncio.sleep(0.005)
            sample()
            initial_capture_count = provider.capture_count
            first_admitted = provider.opened[0]
            destinations.expire_selection(first_admitted)
            manager.observe_cycle(observations)
            while first_admitted not in provider.closed:
                sample()
                await asyncio.sleep(0.005)
            sample()
            result = {
                "profile": profile, "encoder": encoder, "streams": streams,
                "recipients": streams * recipients_per_stream,
                "viewers": viewers, "selected_per_viewer": selections,
                "capture_count": initial_capture_count,
                "peak_capture": provider.peak_capture, "peak_encode": provider.peak_encode,
                "codec_verified": provider.codec_verified,
                "active_job_peak": active_job_peak, "deferred_peak": deferred_peak,
                "local_uploads": sender.local_uploads,
                "file_id_reuses": sender.file_id_reuses,
                "photo_signals": photo_count, "normal_signals": normal_count,
                "normal_signal_p95_ms": _p95(signal_latencies),
                "photo_signal_p95_ms": _p95(photo_latencies),
                "queue_peak": queue_peak, "task_peak": peak_tasks,
                "rss_peak_mb": round(peak_rss / 1048576, 2),
                "rss_growth_mb": round((peak_rss - baseline_rss) / 1048576, 2),
                "cpu_python_and_children_seconds": round(time.process_time() - cpu_started + provider.child_usage()[1], 3),
                "child_rss_peak_mb": round(provider.child_rss_peak / 1048576, 2),
                "temp_disk_peak_mb": round(peak_disk / 1048576, 3),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "expired_sessions_closed": int(first_admitted in provider.closed),
                "expired_selection_disabled": not any(state.preview_enabled for state in await destinations.list_preview_destination_states(first_admitted)),
                "external_sends": 0,
            }
        finally:
            for task in signal_tasks:
                if not task.done():
                    task.cancel()
            if signal_tasks:
                await asyncio.gather(*signal_tasks, return_exceptions=True)
            await manager.shutdown()
            destinations.close()
    result["temp_db_removed"] = not db_path.exists()
    return result


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Local bounded Mini App media load probe")
    parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    parser.add_argument("--encoder", choices=("h264", "synthetic"), default="h264")
    args = parser.parse_args()
    try:
        result = await run_profile(args.profile, encoder=args.encoder)
    except ResourceStop as stop:
        result = {"status": "RESOURCE_STOP", "reason": str(stop), **stop.metrics,
                  "temp_db_removed": not os.path.exists(stop.db_path)}
        exit_code = 2
    else:
        result["status"] = "PASS"
        exit_code = 0
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    asyncio.run(_main())
