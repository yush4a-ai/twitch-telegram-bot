"""Read-only projections for the owner admin panel.

Фаза A панели читает существующие таблицы напрямую, не меняя
``bot/database.py``: общий файл занят параллельной работой над Mini App.
После фазы B запросы переезжают в ``Database``, а модуль остаётся тонким
адаптером. Ни один метод здесь не пишет в базу.
"""

from __future__ import annotations

from pathlib import Path

from .plan_catalog import (
    FREE_VIEWER_CHANNEL_LIMIT,
    VIEWER_PLUS_CHANNEL_LIMIT,
    VIEWER_PLUS_VIDEO_SLOTS,
)

DELIVERY_WINDOW_SECONDS = 24 * 60 * 60
EXPIRING_WINDOW_SECONDS = 7 * 24 * 60 * 60
BACKUP_PREFIX = "auto"

_ACTIVE_GRANTS_SQL = (
    "SELECT g.grant_id,g.subject_kind,g.subject_id,g.plan,g.source,g.starts_at,g.expires_at,"
    "g.issued_by,g.beneficiary_telegram_user_id,i.telegram_user_id,"
    "p.display_name,p.username "
    "FROM entitlement_grants g "
    "LEFT JOIN streamer_identities i ON i.broadcaster_id = g.subject_id AND g.subject_kind='streamer' "
    "LEFT JOIN telegram_user_profiles p ON p.user_id = COALESCE("
    "g.beneficiary_telegram_user_id, i.telegram_user_id, "
    "CASE WHEN g.subject_kind='viewer' THEN CAST(g.subject_id AS INTEGER) END) "
    "WHERE g.revoked_at IS NULL AND g.starts_at <= ? AND g.expires_at > ? "
    "ORDER BY g.expires_at ASC, g.grant_id ASC"
)

_HISTORY_SQL = (
    "SELECT e.grant_id AS grant_id,e.action AS action,e.actor_telegram_id AS actor_telegram_id,"
    "e.happened_at AS happened_at,g.plan AS plan,g.source AS source,"
    "g.subject_kind AS subject_kind,g.subject_id AS subject_id "
    "FROM entitlement_events e LEFT JOIN entitlement_grants g ON g.grant_id = e.grant_id "
    "ORDER BY e.happened_at DESC, e.id DESC LIMIT ? OFFSET ?"
)


def _as_person(value) -> int | None:
    """Нечисловое или пустое значение не должно ронять весь блок панели."""
    try:
        person = int(value)
    except (TypeError, ValueError):
        return None
    return person if person > 0 else None


def _grant_row(row) -> dict:
    (grant_id, subject_kind, subject_id, plan, source, starts_at, expires_at,
     issued_by, beneficiary, identity_user_id, display_name, username) = row
    person_id = None
    if subject_kind == "viewer":
        person_id = _as_person(subject_id)
    else:
        # Легаси-гранты Streamer Plus выдают право без beneficiary: человека
        # достаём по подтверждённой связи broadcaster → Telegram.
        person_id = _as_person(beneficiary) or _as_person(identity_user_id)
    return {
        "grant_id": grant_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "plan": plan,
        "source": source,
        "starts_at": starts_at,
        "expires_at": expires_at,
        "issued_by": issued_by,
        "person_id": person_id,
        # Имя нужно панели: сырой ID в списке доступов ничего не говорит владельцу.
        "display_name": display_name if isinstance(display_name, str) else None,
        "username": username if isinstance(username, str) else None,
    }


class AdminDirectory:
    """Read-only срезы прав, доставок, лимитов и резервных копий."""

    def __init__(self, db, *, backup_dir: Path | str, retention: int = 5) -> None:
        self._db = db
        self._backup_dir = Path(backup_dir)
        self._retention = retention

    async def _active_rows(self, now: float) -> list[dict]:
        cursor = await self._db.conn.execute(_ACTIVE_GRANTS_SQL, (now, now))
        return [_grant_row(row) for row in await cursor.fetchall()]

    async def active_grants(self, now: float, limit: int, offset: int) -> list[dict]:
        rows = await self._active_rows(now)
        start = max(0, offset)
        return rows[start:start + max(0, limit)]

    async def access_overview(self, now: float) -> dict:
        rows = await self._active_rows(now)
        people: dict[int, dict[str, bool]] = {}
        by_source: dict[str, int] = {}
        expiring = 0
        for row in rows:
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1
            if row["expires_at"] <= now + EXPIRING_WINDOW_SECONDS:
                expiring += 1
            person = row["person_id"]
            if person is None:
                continue
            flags = people.setdefault(person, {"viewer": False, "streamer": False})
            if row["plan"] == "streamer_plus":
                flags["streamer"] = True
            else:
                flags["viewer"] = True
        return {
            "active_total": len(people),
            "viewer": sum(1 for flags in people.values() if flags["viewer"] or flags["streamer"]),
            "streamer": sum(1 for flags in people.values() if flags["streamer"]),
            "by_source": by_source,
            "expiring_7d": expiring,
        }

    async def history(self, limit: int, offset: int) -> list[dict]:
        cursor = await self._db.conn.execute(_HISTORY_SQL, (max(0, limit), max(0, offset)))
        return [
            {
                "grant_id": row[0],
                "action": row[1],
                "actor_telegram_id": row[2],
                "happened_at": row[3],
                "plan": row[4],
                "source": row[5],
                "subject_kind": row[6],
                "subject_id": row[7],
            }
            for row in await cursor.fetchall()
        ]

    async def backup_status(self) -> dict:
        newest: Path | None = None
        copies = 0
        if self._backup_dir.is_dir():
            found = sorted(self._backup_dir.glob(f"{BACKUP_PREFIX}-*.db"))
            copies = len(found)
            if found:
                newest = max(found, key=lambda path: path.name)
        return {
            "last_backup_at": newest.stat().st_mtime if newest is not None else None,
            "last_backup_name": newest.name if newest is not None else None,
            "copies": copies,
            "retention": self._retention,
            # Восстановление из копии нигде не фиксируется, поэтому честное False.
            "restore_verified": False,
        }

    async def deliveries_24h(self, now: float) -> dict:
        since = now - DELIVERY_WINDOW_SECONDS
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM notification_jobs WHERE status='done' AND updated_at >= ?",
            (since,),
        )
        notifications = int((await cursor.fetchone())[0])
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM report_deliveries WHERE updated_at >= ? "
            "AND text_sent = 1 AND terminal_failed = 0 "
            "AND (report_format = 'brief' OR html_sent = 1)",
            (since,),
        )
        reports = int((await cursor.fetchone())[0])
        return {"notifications": notifications, "reports": reports,
                "total": notifications + reports}

    async def channel_usage(self, chat_id: int) -> dict:
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM tracked_channels WHERE chat_id = ?", (chat_id,),
        )
        used = int((await cursor.fetchone())[0])
        limit = FREE_VIEWER_CHANNEL_LIMIT
        if chat_id > 0 and await self._db.has_viewer_plus(chat_id):
            limit = VIEWER_PLUS_CHANNEL_LIMIT
        return {"used": used, "limit": limit}

    async def video_usage(self, telegram_user_id: int) -> dict:
        cursor = await self._db.conn.execute(
            "SELECT COUNT(*) FROM viewer_video_selections WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )
        return {"used": int((await cursor.fetchone())[0]), "limit": VIEWER_PLUS_VIDEO_SLOTS}
