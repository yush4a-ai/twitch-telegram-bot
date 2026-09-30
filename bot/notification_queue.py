"""Durable, lease-fenced notification jobs for the staging fan-out path."""

from __future__ import annotations

import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .database import Database


_SAFE_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}\Z")
_SAFE_KIND = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")


@dataclass(frozen=True)
class NotificationJob:
    id: int
    kind: str
    chat_id: int
    twitch_login: str
    logical_stream_id: str
    payload_version: int
    due_at: float
    attempt_count: int
    lease_until: float


class NotificationQueue:
    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def _transaction(self):
        # Shared lock protects the single aiosqlite connection from commits by
        # unrelated writers; BEGIN IMMEDIATE also fences another process.
        async with self._db._write_lock:
            try:
                await self._db.conn.execute("BEGIN IMMEDIATE")
                yield self._db.conn
                await self._db.conn.commit()
            except BaseException:
                await self._db.conn.rollback()
                raise

    @staticmethod
    def _check_time(value: float) -> None:
        if not math.isfinite(value):
            raise ValueError("time must be finite")

    @staticmethod
    def _check_error_class(error_class: str) -> None:
        if not _SAFE_CLASS.fullmatch(error_class):
            raise ValueError("error_class must be a safe class name")

    async def enqueue(
        self, kind: str, chat_id: int, twitch_login: str,
        logical_stream_id: str, payload_version: int, *, due_at: float, now: float,
    ) -> int:
        if (
            not isinstance(kind, str) or not _SAFE_KIND.fullmatch(kind)
            or not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id == 0
            or not isinstance(twitch_login, str) or not twitch_login
            or not isinstance(logical_stream_id, str) or not logical_stream_id
        ):
            raise ValueError("invalid notification identity")
        if not isinstance(payload_version, int) or isinstance(payload_version, bool) or payload_version < 1:
            raise ValueError("payload_version must be positive")
        self._check_time(due_at)
        self._check_time(now)
        async with self._transaction() as conn:
            await conn.execute(
                "INSERT INTO notification_jobs "
                "(kind, chat_id, twitch_login, logical_stream_id, payload_version, "
                "due_at, status, attempt_count, lease_until, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?) "
                "ON CONFLICT (kind, chat_id, twitch_login, logical_stream_id, payload_version) "
                "DO NOTHING",
                (kind, chat_id, twitch_login, logical_stream_id,
                 payload_version, due_at, now, now),
            )
            cursor = await conn.execute(
                "SELECT id FROM notification_jobs WHERE kind = ? AND chat_id = ? "
                "AND twitch_login = ? AND logical_stream_id = ? AND payload_version = ?",
                (kind, chat_id, twitch_login, logical_stream_id, payload_version),
            )
            return int((await cursor.fetchone())[0])

    async def claim_due(
        self, now: float, *, limit: int, lease_seconds: float,
    ) -> list[NotificationJob]:
        self._check_time(now)
        self._check_time(lease_seconds)
        if not 1 <= limit <= 100 or lease_seconds <= 0:
            raise ValueError("claim limits must be positive and bounded")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "SELECT id FROM notification_jobs WHERE due_at <= ? "
                "AND (status = 'pending' OR (status = 'leased' AND lease_until <= ?)) "
                "ORDER BY due_at, id LIMIT ?",
                (now, now, limit),
            )
            ids = [row[0] for row in await cursor.fetchall()]
            if not ids:
                return []
            await conn.executemany(
                "UPDATE notification_jobs SET status = 'leased', "
                "attempt_count = attempt_count + 1, lease_until = ?, updated_at = ? "
                "WHERE id = ?",
                [(now + lease_seconds, now, job_id) for job_id in ids],
            )
            placeholders = ",".join("?" for _ in ids)
            cursor = await conn.execute(
                "SELECT id, kind, chat_id, twitch_login, logical_stream_id, "
                "payload_version, due_at, attempt_count, lease_until "
                f"FROM notification_jobs WHERE id IN ({placeholders})",
                ids,
            )
            claimed = {row[0]: NotificationJob(*row) for row in await cursor.fetchall()}
            return [claimed[job_id] for job_id in ids]

    async def ack(self, job_id: int, attempt_count: int, *, now: float) -> bool:
        self._check_time(now)
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "UPDATE notification_jobs SET status = 'done', lease_until = NULL, "
                "updated_at = ? WHERE id = ? AND status = 'leased' AND attempt_count = ?",
                (now, job_id, attempt_count),
            )
            return cursor.rowcount == 1

    async def defer(
        self, job_id: int, attempt_count: int, *, due_at: float,
        error_class: str, now: float,
    ) -> bool:
        self._check_error_class(error_class)
        self._check_time(now)
        self._check_time(due_at)
        if due_at < now:
            raise ValueError("deferred due_at precedes now")
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "UPDATE notification_jobs SET status = 'pending', due_at = ?, "
                "lease_until = NULL, last_error_class = ?, updated_at = ? "
                "WHERE id = ? AND status = 'leased' AND attempt_count = ?",
                (due_at, error_class, now, job_id, attempt_count),
            )
            return cursor.rowcount == 1

    async def fail(
        self, job_id: int, attempt_count: int, *, error_class: str, now: float,
    ) -> bool:
        self._check_error_class(error_class)
        self._check_time(now)
        async with self._transaction() as conn:
            cursor = await conn.execute(
                "UPDATE notification_jobs SET status = 'failed', lease_until = NULL, "
                "last_error_class = ?, updated_at = ? "
                "WHERE id = ? AND status = 'leased' AND attempt_count = ?",
                (error_class, now, job_id, attempt_count),
            )
            return cursor.rowcount == 1

    async def depth_snapshot(self, now: float) -> dict[str, int | float | None]:
        self._check_time(now)
        cursor = await self._db.conn.execute(
            "SELECT status, COUNT(*) FROM notification_jobs "
            "WHERE status IN ('pending', 'leased', 'failed') GROUP BY status"
        )
        counts = {status: count for status, count in await cursor.fetchall()}
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*), MIN(due_at) FROM notification_jobs WHERE status = 'pending'"
        )
        pending, oldest_due = await cursor.fetchone()
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM notification_jobs WHERE status = 'pending' AND due_at <= ?",
            (now,),
        )
        due_pending = (await cursor.fetchone())[0]
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*), MIN(due_at) FROM notification_jobs WHERE status = 'leased' "
            "AND lease_until <= ? AND due_at <= ?",
            (now, now),
        )
        due_expired_lease, oldest_expired_due = await cursor.fetchone()
        oldest_eligible_due = min(
            (float(value) for value in (oldest_due, oldest_expired_due) if value is not None),
            default=None,
        )
        return {
            "pending_jobs": int(pending),
            "leased_jobs": int(counts.get("leased", 0)),
            "failed_jobs": int(counts.get("failed", 0)),
            "due_jobs": int(due_pending + due_expired_lease),
            "oldest_due_age_seconds": (
                max(0.0, now - oldest_eligible_due)
                if oldest_eligible_due is not None else None
            ),
        }
