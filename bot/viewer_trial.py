"""One explicit, non-monetary Viewer Plus trial on pinned test staging."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from .database import Database


DAY = 86400
TRIAL_SECONDS = 7 * DAY


class TrialAlreadyUsed(Exception):
    pass


@dataclass(frozen=True)
class TrialStatus:
    used: bool
    expires_at: float | None
    active: bool


@dataclass(frozen=True)
class TrialStart:
    expires_at: float
    started_now: bool


class ViewerTrialService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _check(user_id: int, now: float) -> None:
        if (
            type(user_id) is not int or user_id <= 0
            or not isinstance(now, (int, float)) or isinstance(now, bool)
            or not math.isfinite(now)
        ):
            raise ValueError("invalid trial request")

    async def status(self, user_id: int, *, now: float) -> TrialStatus:
        self._check(user_id, now)
        cursor = await self._db.conn.execute(
            "SELECT t.expires_at, g.revoked_at FROM viewer_test_trials t "
            "JOIN entitlement_grants g ON g.grant_id=t.grant_id "
            "WHERE t.telegram_user_id=?", (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return TrialStatus(False, None, False)
        return TrialStatus(True, row[0], row[1] is None and now < row[0])

    async def start(self, user_id: int, *, now: float) -> TrialStart:
        self._check(user_id, now)
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.conn.execute(
                    "SELECT t.expires_at,g.revoked_at FROM viewer_test_trials t "
                    "JOIN entitlement_grants g ON g.grant_id=t.grant_id "
                    "WHERE t.telegram_user_id=?", (user_id,),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    if existing[1] is not None or now >= existing[0]:
                        raise TrialAlreadyUsed("trial already used")
                    await self._db.conn.commit()
                    return TrialStart(existing[0], False)
                cursor = await self._db.conn.execute(
                    "SELECT 1 FROM entitlement_grants WHERE subject_kind='viewer' "
                    "AND subject_id=? AND plan='viewer_plus' AND revoked_at IS NULL "
                    "AND starts_at<=? AND expires_at>? LIMIT 1",
                    (str(user_id), now, now),
                )
                if await cursor.fetchone() is not None:
                    raise PermissionError("Viewer Plus already active")
                grant_id = uuid.uuid4().hex
                expires_at = now + TRIAL_SECONDS
                await self._db.conn.execute(
                    "INSERT INTO entitlement_grants "
                    "(grant_id,request_key,subject_kind,subject_id,plan,source,"
                    "starts_at,expires_at,issued_by,created_at) "
                    "VALUES (?,?,'viewer',?,'viewer_plus','test',?,?,?,?)",
                    (grant_id, f"trial:v1:{user_id}", str(user_id), now,
                     expires_at, user_id, now),
                )
                await self._db.conn.execute(
                    "INSERT INTO entitlement_events "
                    "(grant_id,action,actor_telegram_id,happened_at) "
                    "VALUES (?,'grant',?,?)", (grant_id, user_id, now),
                )
                await self._db.conn.execute(
                    "INSERT INTO viewer_test_trials "
                    "(telegram_user_id,grant_id,started_at,expires_at) "
                    "VALUES (?,?,?,?)", (user_id, grant_id, now, expires_at),
                )
                await self._db.conn.commit()
                return TrialStart(expires_at, True)
            except BaseException:
                await self._db.conn.rollback()
                raise
