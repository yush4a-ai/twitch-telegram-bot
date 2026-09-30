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


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic local SQLite growth baseline")
    parser.add_argument("--destinations", type=int, nargs="+", required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--sample-mode", choices=("legacy", "shared"), default="legacy")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("--output must be a new file in an existing directory")
    profiles = []
    for destinations in args.destinations:
        with tempfile.TemporaryDirectory(prefix="twitchsignal-load-") as directory:
            profiles.append(await run_profile(
                destinations, seed=args.seed, rounds=args.rounds,
                db_path=str(Path(directory) / "synthetic.db"), sample_mode=args.sample_mode,
            ))
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
        print(
            f"{profile['destinations']} destinations: sample p95="
            f"{profile['operations']['sample_insert']['p95_ms']} ms, "
            f"duplication={profile['sample_duplication_factor']}x"
        )


if __name__ == "__main__":
    asyncio.run(_main())
