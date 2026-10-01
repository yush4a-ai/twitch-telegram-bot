"""Isolated synthetic SQLite baseline for the growth roadmap.

Never opens an existing database or a database outside the OS temp directory.
The workload contains generated identifiers only and sends no network requests.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sqlite3
import sys
import tempfile
import time

from bot.database import Database
from bot.live_post import LivePostContent, LivePostMediaResult, LivePostMediaStatus, LocalAnimation
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationWorker
from bot.preview_runtime import PreviewManager, PreviewObservation


def _latency_summary(samples: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples)

    def percentile(percent: float) -> float:
        index = min(len(ordered) - 1, max(0, int((len(ordered) * percent + 99) // 100) - 1))
        return round(ordered[index], 4)

    return {
        "count": len(ordered),
        "p50_ms": percentile(50),
        "p95_ms": percentile(95),
        "p99_ms": percentile(99),
        "max_ms": round(ordered[-1], 4),
    }


def _peak_rss_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            memory_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            memory_info.restype = ctypes.c_int
            if memory_info(
                kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            ):
                return int(counters.PeakWorkingSetSize)
        except (AttributeError, OSError, ValueError):
            pass
        return None
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


async def run_profile(
    destinations: int, *, seed: int, db_path: str, rounds: int,
    sample_mode: str = "legacy",
) -> dict:
    """Run a selected sample writer against a new temporary DB."""
    path = Path(db_path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not path.is_relative_to(temp_root) or path.exists() or not path.parent.is_dir():
        raise ValueError("Load harness requires a new DB path inside the OS temp directory")
    if not 1 <= destinations <= 40000 or not 1 <= rounds <= 10 or sample_mode not in {"legacy", "shared"}:
        raise ValueError("Unsupported synthetic workload size")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("Load harness refuses an existing database") from error
    else:
        os.close(descriptor)

    rng = random.Random(seed)
    login_count = max(1, destinations // 100)
    logins = [f"synthetic_channel_{index:05d}" for index in range(login_count)]
    targets = [(1_000_000_000 + index, rng.choice(logins)) for index in range(destinations)]
    targets_by_login: dict[str, list[int]] = {}
    for chat_id, login in targets:
        targets_by_login.setdefault(login, []).append(chat_id)
    active_login_count = len({login for _, login in targets})
    fingerprint = hashlib.sha256(
        "\n".join(f"{chat}:{login}" for chat, login in targets).encode()
    ).hexdigest()
    durations: dict[str, list[float]] = {
        "track_insert": [], "tracked_snapshot": [], "sample_insert": [], "sample_read": [],
    }
    database = Database(str(path))
    started = time.perf_counter()
    await database.connect()
    try:
        for chat_id, login in targets:
            mark = time.perf_counter_ns()
            created = await database.add_channel(chat_id, login)
            durations["track_insert"].append((time.perf_counter_ns() - mark) / 1_000_000)
            if not created:
                raise RuntimeError("Synthetic target collision")

        for round_index in range(rounds):
            mark = time.perf_counter_ns()
            await database.snapshot_tracked_state()
            durations["tracked_snapshot"].append((time.perf_counter_ns() - mark) / 1_000_000)
            sampled_at = 1_700_000_000.0 + round_index * 60
            if sample_mode == "legacy":
                for chat_id, login in targets:
                    mark = time.perf_counter_ns()
                    await database.add_stream_sample(
                        chat_id, login, f"stream-{login}", sampled_at,
                        100 + round_index, "Synthetic stream", "Synthetic category",
                    )
                    durations["sample_insert"].append((time.perf_counter_ns() - mark) / 1_000_000)
            else:
                for login, chat_ids in targets_by_login.items():
                    mark = time.perf_counter_ns()
                    await database.record_stream_observation(
                        login, f"stream-{login}", sampled_at,
                        100 + round_index, "Synthetic stream", "Synthetic category",
                        chat_ids,
                    )
                    durations["sample_insert"].append((time.perf_counter_ns() - mark) / 1_000_000)

        for chat_id, login in targets[: min(100, destinations)]:
            mark = time.perf_counter_ns()
            await database.get_stream_samples(chat_id, login, f"stream-{login}")
            durations["sample_read"].append((time.perf_counter_ns() - mark) / 1_000_000)

        sample_table = "stream_samples" if sample_mode == "legacy" else "stream_observations"
        cursor = await database.conn.execute(f"SELECT COUNT(*) FROM {sample_table}")
        sample_rows = int((await cursor.fetchone())[0])
        cursor = await database.conn.execute("SELECT COUNT(*) FROM stream_observation_memberships")
        membership_rows = int((await cursor.fetchone())[0])
        elapsed = time.perf_counter() - started
        db_bytes = path.stat().st_size
        wal_path = Path(str(path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        return {
            "synthetic": True,
            "seed": seed,
            "destinations": destinations,
            "distinct_logins": active_login_count,
            "rounds": rounds,
            "sample_mode": sample_mode,
            "workload_sha256": fingerprint,
            "sample_rows": sample_rows,
            "membership_rows": membership_rows,
            "sample_duplication_factor": round(sample_rows / (active_login_count * rounds), 2),
            "elapsed_seconds": round(elapsed, 4),
            "profile_sample_rows_per_second": round(sample_rows / elapsed, 2),
            "sample_insert_rows_per_second": round(
                destinations * rounds * 1000 / sum(durations["sample_insert"]), 2
            ),
            "db_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "peak_process_rss_bytes": _peak_rss_bytes(),
            "operations": {name: _latency_summary(values) for name, values in durations.items()},
        }
    finally:
        await database.close()


async def run_preview_profile(
    concurrency: int, *, stream_count: int = 4,
    capture_delay: float = 0.2, send_delay: float = 0.005,
) -> dict:
    """Exercise the real preview coordinator with fake capture and Telegram I/O."""
    if (
        concurrency not in (1, 2, 4) or not 1 <= stream_count <= 16
        or not 0 < capture_delay <= 0.5 or not 0 <= send_delay <= 0.5
    ):
        raise ValueError("Unsupported synthetic preview profile")

    completed = asyncio.Event()
    active_captures = 0
    max_active_captures = 0
    completion_ms: list[float] = []
    started = time.perf_counter()

    class Session:
        async def create_artifact(self, _request):
            nonlocal active_captures, max_active_captures
            active_captures += 1
            max_active_captures = max(max_active_captures, active_captures)
            try:
                await asyncio.sleep(capture_delay)
                return LocalAnimation("synthetic-not-created.mp4", duration_seconds=6.0)
            finally:
                active_captures -= 1

        async def release_artifact(self, _artifact):
            return None

        async def close(self):
            return None

    class Provider:
        async def open_session(self, _key):
            return Session()

    class Updater:
        async def apply_animation(self, **_kwargs):
            await asyncio.sleep(send_delay)
            completion_ms.append((time.perf_counter() - started) * 1000)
            if len(completion_ms) == stream_count:
                completed.set()
            return LivePostMediaResult(LivePostMediaStatus.APPLIED, "synthetic-file-id")

    with tempfile.TemporaryDirectory(prefix="twitchsignal-preview-load-") as directory:
        database = Database(str(Path(directory) / "synthetic.db"))
        await database.connect()
        manager = PreviewManager(
            database, Updater(), Provider(), enabled=True,
            initial_delay_seconds=0, interval_seconds=3600,
            max_concurrent_jobs=concurrency, job_timeout_seconds=10,
            max_active_sessions=stream_count,
            poll_interval_seconds=60,
            build_content=lambda *_: LivePostContent("synthetic", None),
        )
        monitor: asyncio.Task | None = None
        max_pending = 0

        async def observe_pending():
            nonlocal max_pending
            while not completed.is_set():
                active_jobs = int(manager.health_snapshot()["active_jobs"])
                max_pending = max(max_pending, max(0, active_jobs - active_captures))
                await asyncio.sleep(0.001)

        try:
            observations = []
            for index in range(stream_count):
                login = f"synthetic_preview_{index}"
                viewer_id = 1_000_000 + index
                broadcaster_id = str(3_000_000 + index)
                await database.add_channel(viewer_id, login)
                await database.set_preview_enabled(viewer_id, login, True)
                granted_at = time.time()
                await database.issue_test_viewer_plus(
                    viewer_id, f"preview-load-{index}", starts_at=granted_at - 5,
                    expires_at=granted_at + 3600, issued_by=425785231, now=granted_at,
                )
                await database.replace_video_selection(
                    viewer_id, [(broadcaster_id, login)], expected_version=0,
                )
                await database.set_live_state(
                    viewer_id, login, True, f"synthetic-{index}",
                    2_000_000 + index, "Synthetic", last_seen_live_at=time.time(),
                    broadcaster_id=broadcaster_id,
                )
                if not (await database.get_preview_destination_state(viewer_id, login)).preview_enabled:
                    raise RuntimeError("synthetic preview grant did not become effective")
                observations.append(PreviewObservation(
                    login, True, f"synthetic-{index}", "Synthetic", "Synthetic", 42,
                    "2026-01-01T00:00:00Z",
                ))
            started = time.perf_counter()
            monitor = asyncio.create_task(observe_pending())
            manager.start()
            manager.observe_cycle(observations)
            await asyncio.wait_for(completed.wait(), timeout=10.0)
            return {
                "synthetic": True,
                "profile": "preview",
                "streams": stream_count,
                "capture_concurrency": concurrency,
                "fake_capture_delay_ms": round(capture_delay * 1000, 2),
                "fake_send_delay_ms": round(send_delay * 1000, 2),
                "completed_previews": len(completion_ms),
                "max_active_captures": max_active_captures,
                "max_pending_capture_jobs": max_pending,
                "latency_ms": _latency_summary(completion_ms),
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "peak_process_rss_bytes": _peak_rss_bytes(),
            }
        finally:
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            await manager.shutdown()
            await database.close()


async def run_queue_profile(jobs: int) -> dict:
    """Drain synthetic durable jobs with a fake sender and no rate wait/network."""
    if not isinstance(jobs, int) or isinstance(jobs, bool) or not 1 <= jobs <= 40000:
        raise ValueError("Unsupported synthetic queue profile")
    with tempfile.TemporaryDirectory(prefix="twitchsignal-queue-load-") as directory:
        path = Path(directory) / "synthetic.db"
        database = Database(str(path))
        await database.connect()
        queue = NotificationQueue(database)
        try:
            enqueue_started = time.perf_counter()
            for index in range(jobs):
                await queue.enqueue(
                    "synthetic", 1_000_000 + index, "synthetic_streamer",
                    "synthetic-stream", 1, due_at=1.0, now=0.0,
                )
            enqueue_seconds = time.perf_counter() - enqueue_started
            initial_depth = await queue.depth_snapshot(1.0)
            completed_at_ms: list[float] = []
            drain_started = time.perf_counter()

            async def fake_send(_job):
                await asyncio.sleep(0)
                completed_at_ms.append((time.perf_counter() - drain_started) * 1000)
                return NotificationOutcome.SENT

            worker = NotificationWorker(
                queue, fake_send, max_concurrency=4,
                per_chat_interval=0, group_chat_interval=0,
                global_interval=0, clock=lambda: 1.0,
            )
            while await worker.run_once():
                pass
            drain_seconds = time.perf_counter() - drain_started
            final_depth = await queue.depth_snapshot(1.0)
            cursor = await database.conn.execute(
                "SELECT COUNT(*) FROM notification_jobs WHERE status = 'done'"
            )
            completed_jobs = int((await cursor.fetchone())[0])
            if completed_jobs != jobs or final_depth["pending_jobs"] or final_depth["leased_jobs"]:
                raise RuntimeError("Synthetic queue delivery gap")
            wal_path = Path(str(path) + "-wal")
            return {
                "synthetic": True,
                "profile": "queue",
                "network_requests": 0,
                "enqueued_jobs": jobs,
                "completed_jobs": completed_jobs,
                "remaining_jobs": int(final_depth["pending_jobs"] + final_depth["leased_jobs"]),
                "initial_due_jobs": initial_depth["due_jobs"],
                "enqueue_seconds": round(enqueue_seconds, 4),
                "drain_seconds": round(drain_seconds, 4),
                "latency_ms": _latency_summary(completed_at_ms),
                "db_bytes": path.stat().st_size,
                "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
                "peak_process_rss_bytes": _peak_rss_bytes(),
            }
        finally:
            await database.close()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic local SQLite growth baseline")
    profiles_group = parser.add_mutually_exclusive_group(required=True)
    profiles_group.add_argument("--destinations", type=int, nargs="+")
    profiles_group.add_argument("--preview-concurrency", type=int, nargs="+")
    profiles_group.add_argument("--queue-jobs", type=int, nargs="+")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--sample-mode", choices=("legacy", "shared"), default="legacy")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("--output must be a new file in an existing directory")
    profiles = []
    if args.destinations:
        for destinations in args.destinations:
            with tempfile.TemporaryDirectory(prefix="twitchsignal-load-") as directory:
                profiles.append(await run_profile(
                    destinations, seed=args.seed, rounds=args.rounds,
                    db_path=str(Path(directory) / "synthetic.db"), sample_mode=args.sample_mode,
                ))
    elif args.preview_concurrency:
        for concurrency in args.preview_concurrency:
            profiles.append(await run_preview_profile(concurrency))
    else:
        for jobs in args.queue_jobs:
            profiles.append(await run_queue_profile(jobs))
    with args.output.open("x", encoding="utf-8") as output:
        json.dump({
            "synthetic": True,
            "runtime": {
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "os": platform.system(),
            },
            "profiles": profiles,
        }, output, ensure_ascii=False, indent=2)
        output.write("\n")
    for profile in profiles:
        if profile.get("profile") == "preview":
            print(
                f"preview concurrency={profile['capture_concurrency']}: "
                f"p95={profile['latency_ms']['p95_ms']} ms, "
                f"max_active={profile['max_active_captures']}"
            )
        elif profile.get("profile") == "queue":
            print(
                f"queue jobs={profile['enqueued_jobs']}: "
                f"completed={profile['completed_jobs']}, "
                f"p95={profile['latency_ms']['p95_ms']} ms"
            )
        else:
            print(
                f"{profile['destinations']} destinations: sample p95="
                f"{profile['operations']['sample_insert']['p95_ms']} ms, "
                f"duplication={profile['sample_duplication_factor']}x"
            )


if __name__ == "__main__":
    asyncio.run(_main())
