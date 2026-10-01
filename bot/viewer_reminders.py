"""Persistent one-shot reminders for a viewer's current Twitch live session."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .category_alert_store import _quiet_at
from .database import Database
from .notification_queue import NotificationJob


@dataclass(frozen=True)
class ReminderState:
    twitch_login: str
    broadcaster_id: str
    logical_stream_id: str
    delay_minutes: int
    due_at: float
    version: int
    status: str


class ReminderInFlightError(Exception):
    """The external send has begun, so changing this reminder is unsafe."""


class ViewerReminderService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _state(row: tuple) -> ReminderState:
        return ReminderState(*row)

    async def for_user(self, user_id: int) -> dict[str, ReminderState]:
        cursor = await self._db.conn.execute(
            "SELECT twitch_login,broadcaster_id,logical_stream_id,delay_minutes,"
            "due_at,version,status FROM viewer_reminders WHERE telegram_user_id=?",
            (user_id,),
        )
        return {row[0]: self._state(tuple(row)) for row in await cursor.fetchall()}

    async def set_reminder(
        self, user_id: int, broadcaster_id: str, logical_stream_id: str,
        delay_minutes: int, *, now: float,
    ) -> ReminderState:
        if (
            type(user_id) is not int or user_id <= 0
            or not isinstance(broadcaster_id, str) or not broadcaster_id.isascii()
            or not broadcaster_id.isdecimal() or not 1 <= len(broadcaster_id) <= 32
            or not isinstance(logical_stream_id, str) or not logical_stream_id
            or len(logical_stream_id) > 128 or type(delay_minutes) is not int
            or delay_minutes not in (15, 30) or not math.isfinite(now)
        ):
            raise ValueError("invalid reminder")
        db = self._db
        async with db._write_lock:
            await db.conn.execute("BEGIN IMMEDIATE")
            try:
                if not await db.has_viewer_plus(user_id, now=now):
                    raise PermissionError("viewer Plus required")
                cursor = await db.conn.execute(
                    "SELECT twitch_login,notify_enabled,is_live,last_seen_live_at "
                    "FROM tracked_channels WHERE chat_id=? AND last_broadcaster_id=? "
                    "AND last_stream_id=?",
                    (user_id, broadcaster_id, logical_stream_id),
                )
                matches = await cursor.fetchall()
                if len(matches) != 1:
                    raise PermissionError("current stream not owned")
                login, notify, is_live, observed_at = matches[0]
                if (
                    not notify or not is_live or observed_at is None
                    or not 0 <= now - observed_at <= 300
                    or not await db.is_personal_channel_active(user_id, login, now=now)
                ):
                    raise PermissionError("current stream unavailable")
                cursor = await db.conn.execute(
                    "SELECT version,status FROM viewer_reminders WHERE telegram_user_id=? "
                    "AND twitch_login=?", (user_id, login),
                )
                old = await cursor.fetchone()
                if old and old[1] == "sending":
                    raise ReminderInFlightError("reminder delivery is in progress")
                version = int(old[0]) + 1 if old else 1
                due_at = now + delay_minutes * 60
                await db.conn.execute(
                    "INSERT INTO viewer_reminders "
                    "(telegram_user_id,twitch_login,broadcaster_id,logical_stream_id,"
                    "delay_minutes,due_at,version,queued_version,status,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,0,'scheduled',?) "
                    "ON CONFLICT(telegram_user_id,twitch_login) DO UPDATE SET "
                    "broadcaster_id=excluded.broadcaster_id, "
                    "logical_stream_id=excluded.logical_stream_id, "
                    "delay_minutes=excluded.delay_minutes,due_at=excluded.due_at, "
                    "version=excluded.version,queued_version=0, "
                    "status='scheduled',updated_at=excluded.updated_at",
                    (user_id, login, broadcaster_id, logical_stream_id,
                     delay_minutes, due_at, version, now),
                )
                await db.conn.execute(
                    "UPDATE notification_jobs SET status='done',lease_until=NULL,"
                    "updated_at=? WHERE kind='viewer_reminder' AND chat_id=? "
                    "AND twitch_login=? AND payload_version<>? "
                    "AND status IN ('pending','leased')",
                    (now, user_id, login, version),
                )
                await db.conn.commit()
                return ReminderState(
                    login, broadcaster_id, logical_stream_id,
                    delay_minutes, due_at, version, "scheduled",
                )
            except BaseException:
                await db.conn.rollback()
                raise

    async def cancel_reminder(self, user_id: int, login: str, *, now: float) -> bool:
        if type(user_id) is not int or user_id <= 0 or not login or not math.isfinite(now):
            raise ValueError("invalid reminder cancellation")
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                current = await self._db.conn.execute(
                    "SELECT status FROM viewer_reminders WHERE telegram_user_id=? "
                    "AND twitch_login=?", (user_id, login),
                )
                row = await current.fetchone()
                if row and row[0] == "sending":
                    raise ReminderInFlightError("reminder delivery is in progress")
                cursor = await self._db.conn.execute(
                    "UPDATE viewer_reminders SET status='cancelled',version=version+1,"
                    "updated_at=? WHERE telegram_user_id=? AND twitch_login=? "
                    "AND status='scheduled'", (now, user_id, login),
                )
                if cursor.rowcount == 1:
                    await self._db.conn.execute(
                        "UPDATE notification_jobs SET status='done',lease_until=NULL,"
                        "updated_at=? WHERE kind='viewer_reminder' AND chat_id=? "
                        "AND twitch_login=? AND status IN ('pending','leased')",
                        (now, user_id, login),
                    )
                await self._db.conn.commit()
                return cursor.rowcount == 1
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def _finish(self, job: NotificationJob, status: str, *, now: float) -> bool:
        async with self._db._write_lock:
            cursor = await self._db.conn.execute(
                "UPDATE viewer_reminders SET status=?,updated_at=? "
                "WHERE telegram_user_id=? AND twitch_login=? AND version=? "
                "AND status IN ('scheduled','sending')", (status, now, job.chat_id,
                                          job.twitch_login, job.payload_version),
            )
            await self._db.conn.commit()
            return cursor.rowcount == 1

    async def mark_sent(self, job: NotificationJob, *, now: float) -> bool:
        return await self._finish(job, "sent", now=now)

    async def mark_unknown(self, job: NotificationJob, *, now: float) -> bool:
        return await self._finish(job, "unknown", now=now)

    async def mark_suppressed(self, job: NotificationJob, *, now: float) -> bool:
        return await self._finish(job, "suppressed", now=now)

    async def mark_retryable(self, job: NotificationJob, *, now: float) -> bool:
        # Telegram explicitly rejected this attempt before delivering it.
        async with self._db._write_lock:
            cursor = await self._db.conn.execute(
                "UPDATE viewer_reminders SET status='scheduled',updated_at=? "
                "WHERE telegram_user_id=? AND twitch_login=? AND version=? "
                "AND status='sending'",
                (now, job.chat_id, job.twitch_login, job.payload_version),
            )
            await self._db.conn.commit()
            return cursor.rowcount == 1

    async def _eligible(self, job: NotificationJob, broadcaster_id: str,
                        *, now: float) -> bool:
        eligible = (
            await self._db.has_viewer_plus(job.chat_id, now=now)
            and await self._db.is_personal_channel_active(
                job.chat_id, job.twitch_login, now=now,
            )
            and not _quiet_at(await self._db.get_quiet_hours(job.chat_id), now)
        )
        if not eligible:
            return False
        cursor = await self._db.conn.execute(
            "SELECT is_live,notify_enabled,last_stream_id,last_broadcaster_id,"
            "last_seen_live_at FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
            (job.chat_id, job.twitch_login),
        )
        tracked = await cursor.fetchone()
        return bool(
            tracked is not None and tracked[0] and tracked[1]
            and tracked[2] == job.logical_stream_id
            and tracked[3] == broadcaster_id
            and tracked[4] is not None
            and 0 <= now - tracked[4] <= 300
        )

    async def begin_delivery(self, job: NotificationJob, *, now: float) -> bool:
        """Fence cancellation/rescheduling before invoking the external sender."""
        if job.kind != "viewer_reminder" or job.chat_id <= 0 or not math.isfinite(now):
            return False
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.conn.execute(
                    "SELECT broadcaster_id,logical_stream_id,due_at,version,status "
                    "FROM viewer_reminders WHERE telegram_user_id=? AND twitch_login=?",
                    (job.chat_id, job.twitch_login),
                )
                row = await cursor.fetchone()
                if (
                    row is None or row[1] != job.logical_stream_id
                    or row[3] != job.payload_version or row[4] != "scheduled"
                    or row[2] > now
                ):
                    await self._db.conn.commit()
                    return False
                eligible = await self._eligible(job, row[0], now=now)
                await self._db.conn.execute(
                    "UPDATE viewer_reminders SET status=?,updated_at=? "
                    "WHERE telegram_user_id=? AND twitch_login=? AND version=? "
                    "AND status='scheduled'",
                    ("sending" if eligible else "suppressed", now, job.chat_id,
                     job.twitch_login, job.payload_version),
                )
                await self._db.conn.commit()
                return eligible
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def ready_for_delivery(self, job: NotificationJob, *, now: float) -> bool:
        if job.kind != "viewer_reminder" or job.chat_id <= 0 or not math.isfinite(now):
            return False
        cursor = await self._db.conn.execute(
            "SELECT broadcaster_id,logical_stream_id,due_at,version,status "
            "FROM viewer_reminders WHERE telegram_user_id=? AND twitch_login=?",
            (job.chat_id, job.twitch_login),
        )
        row = await cursor.fetchone()
        if (
            row is None or row[1] != job.logical_stream_id
            or row[3] != job.payload_version or row[4] != "scheduled"
            or row[2] > now
        ):
            return False
        eligible = await self._eligible(job, row[0], now=now)
        if not eligible:
            await self.mark_suppressed(job, now=now)
        return eligible
