"""Personal Viewer access from independent grants and a frozen Streamer buyer."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database


@dataclass(frozen=True)
class EffectiveAccessSource:
    grant_id: str
    product_id: str
    starts_at: float
    expires_at: float


@dataclass(frozen=True)
class EffectiveViewerState:
    active: bool
    sources: tuple[EffectiveAccessSource, ...]
    expires_at: float | None


def effective_viewer_predicate(user_sql: str, now_sql: str) -> str:
    """Internal expressions only; bind the user once and the clock twice.

    Keeping those three bindings also makes the predicate usable inside the
    existing INSERT/DELETE transactions. No client value is interpolated.
    """
    expression = r"(?:\?(?:[1-9][0-9]*)?|[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)"
    if any(not isinstance(value, str) or re.fullmatch(expression, value) is None
           for value in (user_sql, now_sql)):
        raise ValueError("invalid internal entitlement expression")
    return (
        "EXISTS (SELECT 1 FROM entitlement_grants g, "
        f"(SELECT {user_sql} AS uid, {now_sql} AS starts_at, {now_sql} AS ends_at) ev "
        "WHERE ev.uid>0 AND ((g.subject_kind='viewer' AND g.plan='viewer_plus' "
        "AND g.subject_id=CAST(ev.uid AS TEXT)) OR "
        "(g.subject_kind='streamer' AND g.plan='streamer_plus' "
        "AND g.beneficiary_telegram_user_id=ev.uid)) "
        "AND g.revoked_at IS NULL AND g.starts_at<=ev.starts_at "
        "AND g.expires_at>ev.ends_at)"
    )


async def resolve_effective_viewer(
    db: Database, user_id: int, *, now: float,
) -> EffectiveViewerState:
    if (type(user_id) is not int or user_id <= 0 or type(now) not in (int, float)
            or not math.isfinite(now)):
        raise ValueError("invalid effective Viewer subject")
    cursor = await db.conn.execute(
        "SELECT grant_id,plan,starts_at,expires_at FROM entitlement_grants "
        "WHERE revoked_at IS NULL AND expires_at>? AND "
        "((subject_kind='viewer' AND plan='viewer_plus' AND subject_id=?) OR "
        "(subject_kind='streamer' AND plan='streamer_plus' "
        "AND beneficiary_telegram_user_id=?)) ORDER BY starts_at,expires_at,grant_id",
        (now, str(user_id), user_id),
    )
    sources: list[EffectiveAccessSource] = []
    continuous_end = now
    for row in await cursor.fetchall():
        source = EffectiveAccessSource(*row)
        if (not math.isfinite(source.starts_at) or not math.isfinite(source.expires_at)
                or source.expires_at <= source.starts_at):
            continue
        if source.starts_at > continuous_end:
            break
        sources.append(source)
        continuous_end = max(continuous_end, source.expires_at)
    return EffectiveViewerState(bool(sources), tuple(sources), continuous_end if sources else None)
