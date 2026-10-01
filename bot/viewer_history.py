"""Private, bounded metadata history of terminal viewer notification outcomes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .database import Database


RETENTION_SECONDS = 30 * 86400
_KINDS = frozenset({"go_live", "viewer_category_change", "viewer_reminder"})
_OUTCOMES = frozenset({"sent", "suppressed", "unknown"})


@dataclass(frozen=True)
class ViewerEvent:
    id: int
    kind: str
    login: str
    logical_stream_id: str
    category_name: str | None
    outcome: str
    happened_at: float


@dataclass(frozen=True)
class ViewerHistoryPage:
    events: tuple[ViewerEvent, ...]
    next_before_id: int | None


async def record_job_outcome(
    conn, *, job_id: int, kind: str, user_id: int, login: str,
    stream_id: str, category_transition_id: str | None,
    outcome: str, now: float,
) -> None:
    """Write inside the caller's terminal job transaction, once per job."""
    if kind not in _KINDS or user_id <= 0 or outcome not in _OUTCOMES:
        return
    category_name = None
    if kind == "viewer_category_change" and category_transition_id:
        cursor = await conn.execute(
            "SELECT to_category_name FROM category_transitions WHERE transition_id=?",
            (category_transition_id,),
        )
        row = await cursor.fetchone()
        if row and row[0]:
            category_name = str(row[0])[:80]
    await conn.execute(
        "INSERT OR IGNORE INTO viewer_event_history "
        "(telegram_user_id,event_key,kind,twitch_login,logical_stream_id,"
        "category_name,outcome,happened_at) "
        "SELECT ?,?,?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM entitlement_grants g "
        "WHERE g.subject_kind='viewer' AND g.subject_id=CAST(? AS TEXT) "
        "AND g.plan='viewer_plus' AND g.revoked_at IS NULL "
        "AND g.starts_at<=? AND g.expires_at>?)",
        (user_id, f"job:{job_id}", kind, login, stream_id,
         category_name, outcome, now, user_id, now, now),
    )


class ViewerHistoryService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_events(
        self, user_id: int, *, before_id: int | None = None,
        limit: int = 20, now: float,
    ) -> ViewerHistoryPage:
        if (
            type(user_id) is not int or user_id <= 0
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
            or type(limit) is not int or not 1 <= limit <= 30
            or not isinstance(now, (int, float)) or not math.isfinite(now)
        ):
            raise ValueError("invalid history page")
        if not await self._db.has_viewer_plus(user_id, now=now):
            raise PermissionError("Viewer Plus required")
        cursor = await self._db.conn.execute(
            "SELECT id,kind,twitch_login,logical_stream_id,category_name,"
            "outcome,happened_at FROM viewer_event_history "
            "WHERE telegram_user_id=? AND happened_at>=? AND (? IS NULL OR id<?) "
            "AND EXISTS (SELECT 1 FROM entitlement_grants g "
            "WHERE g.subject_kind='viewer' AND g.subject_id=CAST(? AS TEXT) "
            "AND g.plan='viewer_plus' AND g.revoked_at IS NULL "
            "AND g.starts_at<=? AND g.expires_at>?) "
            "ORDER BY id DESC LIMIT ?",
            (user_id, now - RETENTION_SECONDS, before_id, before_id,
             user_id, now, now, limit + 1),
        )
        rows = await cursor.fetchall()
        events = tuple(ViewerEvent(*row) for row in rows[:limit])
        return ViewerHistoryPage(
            events, events[-1].id if len(rows) > limit else None,
        )

    async def record_direct_live(
        self, user_id: int, login: str, stream_id: str, message_id: int,
        *, now: float,
    ) -> bool:
        if (
            type(user_id) is not int or user_id <= 0
            or not isinstance(login, str) or re.fullmatch(r"[A-Za-z0-9_]{2,25}", login) is None
            or not isinstance(stream_id, str) or not stream_id or len(stream_id) > 128
            or type(message_id) is not int or message_id <= 0
            or not math.isfinite(now)
        ):
            raise ValueError("invalid confirmed live event")
        async with self._db._write_lock:
            cursor = await self._db.conn.execute(
                "INSERT OR IGNORE INTO viewer_event_history "
                "(telegram_user_id,event_key,kind,twitch_login,logical_stream_id,"
                "category_name,outcome,happened_at) "
                "SELECT ?,?,'go_live',?,?,NULL,'sent',? FROM tracked_channels "
                "WHERE chat_id=? AND twitch_login=? AND is_live=1 "
                "AND last_stream_id=? AND last_message_id=? "
                "AND EXISTS (SELECT 1 FROM entitlement_grants g "
                "WHERE g.subject_kind='viewer' AND g.subject_id=CAST(? AS TEXT) "
                "AND g.plan='viewer_plus' AND g.revoked_at IS NULL "
                "AND g.starts_at<=? AND g.expires_at>?)",
                (user_id, f"direct:{user_id}:{message_id}", login.lower(),
                 stream_id, now, user_id, login.lower(), stream_id, message_id,
                 user_id, now, now),
            )
            await self._db.conn.commit()
            return cursor.rowcount == 1

    async def purge_expired(self, *, now: float) -> int:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("invalid cleanup time")
        async with self._db._write_lock:
            cursor = await self._db.conn.execute(
                "DELETE FROM viewer_event_history WHERE happened_at < ?",
                (now - RETENTION_SECONDS,),
            )
            await self._db.conn.commit()
            return cursor.rowcount
