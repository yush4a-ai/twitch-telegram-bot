"""Bounded synthetic shared-observation and notification fan-out profile."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
import random
import shutil
import tempfile
import time

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationWorker
from scripts.load_harness import _peak_rss_bytes
from scripts.sqlite_backup import backup_database, verify_backup


class CapacityLimit(RuntimeError):
    """A synthetic profile exceeded its explicitly bounded resource budget."""


def _summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise ValueError("latency sample is empty")
    ordered = sorted(values)

    def percentile(value: float) -> float:
        return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * value) - 1)], 3)

    return {
        "count": len(ordered),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "p99_ms": percentile(0.99),
        "max_ms": round(ordered[-1], 3),
    }


def _temporary_root(value: Path | None) -> Path:
    system_root = Path(tempfile.gettempdir()).resolve()
    root = system_root if value is None else Path(value).resolve()
    if not root.is_dir() or not root.is_relative_to(system_root):
        raise ValueError("R9 workload requires an existing OS temp directory")
    return root


async def run_mixed_profile(
    destinations: int, *, rounds: int = 2, seed: int = 20261001,
    temp_root: Path | None = None, max_rss_bytes: int = 268_435_456,
    max_seconds: float = 900,
) -> dict[str, object]:
    """Run one profile on a new temp DB; never accept an active DB path."""
    if (
        not isinstance(destinations, int) or isinstance(destinations, bool)
        or not 1 <= destinations <= 40_000
        or not isinstance(rounds, int) or not 2 <= rounds <= 10
        or not isinstance(seed, int) or isinstance(seed, bool)
        or not isinstance(max_rss_bytes, int) or max_rss_bytes < 1
        or not math.isfinite(max_seconds) or max_seconds <= 0
    ):
        raise ValueError("Unsupported R9 synthetic profile")
    root = _temporary_root(temp_root)
    started = time.perf_counter()
    cpu_started = time.process_time()

    def guard(phase: str, directory: Path) -> None:
        if time.perf_counter() - started > max_seconds:
            raise CapacityLimit(f"{phase}: wall time limit")
        rss = _peak_rss_bytes()
        if rss is not None and rss > max_rss_bytes:
            raise CapacityLimit(f"{phase}: process RSS limit")
        if shutil.disk_usage(directory).free < 512 * 1024 * 1024:
            raise CapacityLimit(f"{phase}: temp disk free limit")

    with tempfile.TemporaryDirectory(prefix="twitchsignal-r9-", dir=root) as temporary:
        directory = Path(temporary)
        guard("start", directory)
        path = directory / "profile.db"
        database = Database(str(path))
        await database.connect()
        try:
            login_count = (destinations + 99) // 100
            targets = [
                (-(1_000_000_000 + index), f"synthetic_r9_{index // 100:04d}", 2_000_000 + index)
                for index in range(destinations)
            ]
            random.Random(seed).shuffle(targets)
            grouped: dict[str, list[int]] = {}
            for chat_id, login, _ in targets:
                grouped.setdefault(login, []).append(chat_id)
            requests = [
                (chat_id, login, f"stream-{login}", message_id, None)
                for chat_id, login, message_id in targets
            ]
            phase_started = time.perf_counter()
            for index, (chat_id, login, message_id) in enumerate(targets, 1):
                if not await database.add_channel(chat_id, login):
                    raise RuntimeError("Synthetic destination collision")
                await database.set_live_state(
                    chat_id, login, True, f"stream-{login}", message_id,
                    "Synthetic stream", last_seen_live_at=time.time(),
                )
                if index % 1000 == 0:
                    guard("seed", directory)
            seed_seconds = time.perf_counter() - phase_started
            queue = NotificationQueue(database)
            initial_depth = await queue.depth_snapshot(time.time())
            if initial_depth["pending_jobs"] or initial_depth["leased_jobs"]:
                raise RuntimeError("Synthetic profile did not start with an empty queue")

            snapshot_ms: list[float] = []
            observation_ms: list[float] = []
            enqueue_ms: list[float] = []
            max_depth = 0
            base_now = time.time()
            first_enqueue_started: float | None = None
            lease_reclaimed = False
            stale_ack_accepted = True
            for round_number in range(1, rounds + 1):
                mark = time.perf_counter()
                await database.snapshot_tracked_state()
                snapshot_ms.append((time.perf_counter() - mark) * 1000)
                for login, chat_ids in grouped.items():
                    mark = time.perf_counter()
                    await database.record_stream_observation(
                        login, f"stream-{login}", base_now + round_number,
                        round_number * 10, "Synthetic stream", "Synthetic game", chat_ids,
                    )
                    observation_ms.append((time.perf_counter() - mark) * 1000)
                if first_enqueue_started is None:
                    first_enqueue_started = time.perf_counter()
                mark = time.perf_counter()
                await queue.request_live_updates(requests, now=base_now + round_number)
                enqueue_ms.append((time.perf_counter() - mark) * 1000)
                depth = await queue.depth_snapshot(base_now + round_number)
                max_depth = max(max_depth, int(depth["pending_jobs"] + depth["leased_jobs"]))
                guard("poll_round", directory)

                if round_number == 1:
                    first = (await queue.claim_due(
                        base_now + 1, limit=1, lease_seconds=1.0,
                    ))[0]
                    await database.close()
                    database = Database(str(path))
                    await database.connect()
                    queue = NotificationQueue(database)
                    reclaimed = (await queue.claim_due(
                        base_now + 2, limit=1, lease_seconds=180.0,
                    ))[0]
                    lease_reclaimed = (
                        reclaimed.id == first.id
                        and reclaimed.attempt_count == first.attempt_count + 1
                    )
                    stale_ack_accepted = await queue.ack(
                        first.id, first.attempt_count, now=base_now + 2.1,
                        revision=first.revision,
                    )
                    if not lease_reclaimed or stale_ack_accepted or not await queue.ack(
                        reclaimed.id, reclaimed.attempt_count,
                        now=base_now + 2.2, revision=reclaimed.revision,
                    ):
                        raise RuntimeError("Synthetic lease recovery fence failed")

            guard("before_drain", directory)
            drain_started = time.perf_counter()
            completion_ms: list[float] = []
            db_read_ms: list[float] = []

            async def fake_send(job):
                mark = time.perf_counter()
                rows = await database.list_live_channels(
                    job.chat_id, twitch_login=job.twitch_login,
                )
                db_read_ms.append((time.perf_counter() - mark) * 1000)
                if len(rows) != 1 or rows[0][2] != rounds * 10:
                    raise RuntimeError("Synthetic worker did not see the latest shared sample")
                completion_ms.append((time.perf_counter() - first_enqueue_started) * 1000)
                return NotificationOutcome.SENT

            worker = NotificationWorker(
                queue, fake_send, max_concurrency=16,
                per_chat_interval=0, group_chat_interval=0,
                global_interval=0, send_timeout=5,
                clock=lambda: base_now + rounds + 1000,
            )
            completed_batches = 0
            while await worker.run_once():
                completed_batches += 1
                if completed_batches % 64 == 0:
                    guard("drain", directory)
            drain_seconds = time.perf_counter() - drain_started
            cursor = await database.conn.execute(
                "SELECT status, COUNT(*) FROM notification_jobs GROUP BY status"
            )
            counts = dict(await cursor.fetchall())
            cursor = await database.conn.execute(
                "SELECT COUNT(*), MAX(revision) FROM notification_jobs"
            )
            jobs_total, max_revision = await cursor.fetchone()
            cursor = await database.conn.execute(
                "SELECT COUNT(*) FROM stream_observations"
            )
            shared_observations = (await cursor.fetchone())[0]
            integrity = (await (await database.conn.execute(
                "PRAGMA integrity_check"
            )).fetchone())[0]
            if (
                jobs_total != destinations or counts.get("done", 0) != destinations
                or counts.get("pending", 0) or counts.get("leased", 0)
                or counts.get("failed", 0) or max_revision != rounds
                or shared_observations != login_count * rounds or integrity != "ok"
            ):
                raise RuntimeError("Synthetic fan-out invariant failed")
            backup = directory / "profile-backup.db"
            backup_database(path, backup)
            restored = verify_backup(backup)
            wal = Path(str(path) + "-wal")
            guard("complete", directory)
            return {
                "synthetic": True,
                "network_requests": 0,
                "destinations": destinations,
                "rounds": rounds,
                "seed": seed,
                "distinct_logins": login_count,
                "shared_observations": shared_observations,
                "jobs_total": jobs_total,
                "jobs_done": counts.get("done", 0),
                "jobs_pending": counts.get("pending", 0),
                "jobs_leased": counts.get("leased", 0),
                "jobs_failed": counts.get("failed", 0),
                "max_revision": max_revision,
                "queue_depth_max": max_depth,
                "lease_reclaimed": lease_reclaimed,
                "stale_ack_accepted": stale_ack_accepted,
                "integrity": integrity,
                "backup_integrity": restored["integrity"],
                "backup_restored_tables": restored["restored_tables"],
                "snapshot_ms": _summary(snapshot_ms),
                "observation_write_ms": _summary(observation_ms),
                "request_updates_ms": _summary(enqueue_ms),
                "db_read_ms": _summary(db_read_ms),
                "queue_latency_ms": _summary(completion_ms),
                "seed_seconds": round(seed_seconds, 3),
                "drain_seconds": round(drain_seconds, 3),
                "wall_seconds": round(time.perf_counter() - started, 3),
                "process_cpu_seconds": round(time.process_time() - cpu_started, 3),
                "peak_rss_bytes": _peak_rss_bytes(),
                "db_bytes": path.stat().st_size,
                "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                "temp_disk_free_bytes": shutil.disk_usage(directory).free,
                "fake_send_completions_per_second": round(destinations / drain_seconds, 3),
                "telegram_40ms_start_lower_bound_seconds": round(destinations * 0.04, 3),
            }
        finally:
            await database.close()
