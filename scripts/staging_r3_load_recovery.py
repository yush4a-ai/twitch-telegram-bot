"""Pinned staging lease recovery and mixed SQLite queue workload.

Uses a disposable database and a fake Telegram sender. It never reads or
writes the running bot database and never calls Telegram or Twitch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Mapping

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationWorker


TARGET = Path(__file__).with_name("staging_target.json")


def validate_runtime(environ: Mapping[str, str]) -> None:
    target = json.loads(TARGET.read_text(encoding="utf-8"))
    if (
        environ.get("RAILWAY_ENVIRONMENT_NAME") != "staging"
        or environ.get("RAILWAY_PROJECT_ID") != target["project_id"]
        or environ.get("RAILWAY_ENVIRONMENT_ID") != target["staging_environment_id"]
        or environ.get("RAILWAY_SERVICE_ID") != target["service_id"]
        or environ.get("NOTIFICATION_QUEUE_ENABLED") != "1"
    ):
        raise ValueError("Pinned staging queue runtime mismatch")


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


async def run_experiment(*, destinations: int, rounds: int) -> dict[str, object]:
    if not 1 <= destinations <= 5000 or not 2 <= rounds <= 10:
        raise ValueError("destinations must be 1..5000 and rounds 2..10")
    chat_ids = [-(100000 + index) for index in range(destinations)]
    sampled_ms: list[float] = []
    cpu_start = time.process_time()
    queue_depth_max = 0
    with tempfile.TemporaryDirectory(prefix="twitchsignal-r3-recovery-") as directory:
        path = Path(directory) / "mixed.db"
        db = Database(str(path))
        await db.connect()
        try:
            for index, chat_id in enumerate(chat_ids):
                await db.add_channel(chat_id, "synthetic_r3")
                await db.set_live_state(
                    chat_id, "synthetic_r3", True, "synthetic_stream",
                    1000 + index, "Synthetic stream",
                )
            queue = NotificationQueue(db)
            sampled_at = time.time()
            start = time.perf_counter()
            await db.record_stream_observation(
                "synthetic_r3", "synthetic_stream", sampled_at, 10,
                "Synthetic stream", "Synthetic game", chat_ids,
            )
            sampled_ms.append((time.perf_counter() - start) * 1000)
            requests = [
                (chat_id, "synthetic_r3", "synthetic_stream", 1000 + index, None)
                for index, chat_id in enumerate(chat_ids)
            ]
            await queue.request_live_updates(requests, now=sampled_at)
            queue_depth_max = max(
                queue_depth_max,
                (await queue.depth_snapshot(time.time()))["pending_jobs"],
            )
            first = (await queue.claim_due(
                sampled_at, limit=1, lease_seconds=1.0
            ))[0]
        finally:
            await db.close()

        # Reopen the same disposable DB to model a process restart after claim.
        db = Database(str(path))
        await db.connect()
        try:
            queue = NotificationQueue(db)
            reclaimed = (await queue.claim_due(
                sampled_at + 1.0, limit=1, lease_seconds=180.0
            ))[0]
            lease_reclaimed = (
                reclaimed.id == first.id
                and reclaimed.attempt_count == first.attempt_count + 1
            )
            old_ack_accepted = await queue.ack(
                first.id, first.attempt_count, now=sampled_at + 1.1,
                revision=first.revision,
            )
            await queue.ack(
                reclaimed.id, reclaimed.attempt_count,
                now=sampled_at + 1.2, revision=reclaimed.revision,
            )
            if not lease_reclaimed or old_ack_accepted:
                raise RuntimeError("lease recovery fence failed")

            for round_number in range(2, rounds + 1):
                async def write_sample():
                    start = time.perf_counter()
                    await db.record_stream_observation(
                        "synthetic_r3", "synthetic_stream",
                        sampled_at + round_number, round_number * 10,
                        "Synthetic stream", "Synthetic game", chat_ids,
                    )
                    sampled_ms.append((time.perf_counter() - start) * 1000)

                await asyncio.gather(
                    write_sample(),
                    queue.request_live_updates(
                        requests, now=time.time(),
                    ),
                )
                queue_depth_max = max(
                    queue_depth_max,
                    (await queue.depth_snapshot(time.time()))["pending_jobs"],
                )

            async def fake_send(job):
                rows = await db.list_live_channels(
                    job.chat_id, twitch_login=job.twitch_login
                )
                if len(rows) != 1 or rows[0][2] != rounds * 10:
                    raise RuntimeError("worker did not see newest shared sample")
                return NotificationOutcome.SENT

            worker = NotificationWorker(
                queue, fake_send, max_concurrency=4,
                per_chat_interval=0, group_chat_interval=0,
                global_interval=0,
            )
            drain_start = time.perf_counter()
            for _ in range((destinations + 3) // 4 + 4):
                if await worker.run_once() == 0:
                    break
            drain_seconds = time.perf_counter() - drain_start
            cursor = await db.conn.execute(
                "SELECT status, COUNT(*) FROM notification_jobs "
                "WHERE kind = 'live_update' GROUP BY status"
            )
            counts = dict(await cursor.fetchall())
            cursor = await db.conn.execute(
                "SELECT COUNT(*), MAX(revision) FROM notification_jobs "
                "WHERE kind = 'live_update'"
            )
            jobs_total, max_revision = await cursor.fetchone()
            cursor = await db.conn.execute(
                "SELECT updated_at - created_at FROM notification_jobs "
                "WHERE kind = 'live_update'"
            )
            completion_seconds = [row[0] for row in await cursor.fetchall()]
            integrity = (await (await db.conn.execute(
                "PRAGMA integrity_check"
            )).fetchone())[0]
            if counts.get("done", 0) != destinations or integrity != "ok":
                raise RuntimeError("mixed queue workload did not drain cleanly")
            try:
                import resource
                peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            except ImportError:
                peak_rss_bytes = None
            return {
                "destinations": destinations,
                "rounds": rounds,
                "lease_reclaimed": lease_reclaimed,
                "old_ack_accepted": old_ack_accepted,
                "jobs_total": jobs_total,
                "jobs_done": counts.get("done", 0),
                "jobs_pending": counts.get("pending", 0),
                "jobs_failed": counts.get("failed", 0),
                "max_revision": max_revision,
                "integrity": integrity,
                "sample_write_p95_ms": round(_p95(sampled_ms), 3),
                "drain_seconds": round(drain_seconds, 3),
                "job_completion_p95_seconds": round(_p95(completion_seconds), 3),
                "process_cpu_seconds": round(time.process_time() - cpu_start, 3),
                "peak_rss_bytes": peak_rss_bytes,
                "queue_depth_max": queue_depth_max,
                "db_bytes": path.stat().st_size,
                "wal_bytes": Path(str(path) + "-wal").stat().st_size
                if Path(str(path) + "-wal").exists() else 0,
            }
        finally:
            await db.close()


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destinations", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        validate_runtime(os.environ)
        result = await run_experiment(
            destinations=args.destinations, rounds=args.rounds,
        )
    except Exception as error:
        print(f"staging_r3_load_recovery_failed={type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
