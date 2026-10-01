"""Named, inert post variants owned by a verified Twitch broadcaster."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .database import Database
from .streamer_template import StreamerTemplate, validate_streamer_template


MAX_PRESETS = 12


class PresetLimit(Exception):
    pass


class PresetNameTaken(Exception):
    pass


class PresetStale(ValueError):
    pass


@dataclass(frozen=True)
class StreamerPreset:
    id: int
    name: str
    headline: str
    body: str
    buttons: tuple[dict[str, str], ...]


def _name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("invalid preset name")
    clean = value.strip()
    if (
        not clean or len(clean.encode("utf-16-le")) // 2 > 40
        or any(ord(char) < 32 or ord(char) == 127 for char in clean)
    ):
        raise ValueError("invalid preset name")
    return clean, clean.casefold()


def _buttons_json(template: StreamerTemplate) -> str:
    return json.dumps(
        [{"label": button.label, "url": button.url} for button in template.buttons],
        ensure_ascii=False, separators=(",", ":"),
    )


def _preset(row) -> StreamerPreset:
    return StreamerPreset(row[0], row[1], row[2], row[3],
                          tuple(json.loads(row[4])))


class StreamerPresetService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_for_user(self, user_id: int) -> tuple[StreamerPreset, ...]:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid owner")
        cursor = await self._db.conn.execute(
            "SELECT p.id,p.name,p.headline,p.body,p.buttons_json "
            "FROM streamer_template_presets p JOIN streamer_identities i "
            "ON i.broadcaster_id=p.broadcaster_id "
            "WHERE i.telegram_user_id=? ORDER BY p.id DESC LIMIT ?",
            (user_id, MAX_PRESETS),
        )
        return tuple(_preset(row) for row in await cursor.fetchall())

    async def create(
        self, user_id: int, name: str, *, headline: str, body: str,
        buttons: object, now: float,
    ) -> StreamerPreset:
        if type(user_id) is not int or user_id <= 0 or not math.isfinite(now):
            raise ValueError("invalid preset request")
        clean, key = _name(name)
        template = validate_streamer_template(headline, body, buttons)
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                owner = await self._db.conn.execute(
                    "SELECT i.broadcaster_id FROM streamer_identities i "
                    "WHERE i.telegram_user_id=? AND EXISTS ("
                    "SELECT 1 FROM entitlement_grants g WHERE "
                    "g.subject_kind='streamer' AND g.subject_id=i.broadcaster_id "
                    "AND g.plan='streamer_plus' AND g.revoked_at IS NULL "
                    "AND g.starts_at<=? AND g.expires_at>?) LIMIT 1",
                    (user_id, now, now),
                )
                row = await owner.fetchone()
                if row is None:
                    raise PermissionError("Streamer Plus required")
                broadcaster_id = row[0]
                cursor = await self._db.conn.execute(
                    "SELECT name_key FROM streamer_template_presets "
                    "WHERE broadcaster_id=?", (broadcaster_id,),
                )
                names = {item[0] for item in await cursor.fetchall()}
                if key in names:
                    raise PresetNameTaken("preset name is used")
                if len(names) >= MAX_PRESETS:
                    raise PresetLimit("preset capacity reached")
                cursor = await self._db.conn.execute(
                    "INSERT INTO streamer_template_presets "
                    "(broadcaster_id,name,name_key,headline,body,buttons_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (broadcaster_id, clean, key, template.headline, template.body,
                     _buttons_json(template), now),
                )
                result = StreamerPreset(
                    cursor.lastrowid, clean, template.headline, template.body,
                    tuple({"label": button.label, "url": button.url}
                          for button in template.buttons),
                )
                await self._db.conn.commit()
                return result
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def delete(self, user_id: int, preset_id: int) -> bool:
        if type(user_id) is not int or user_id <= 0 or type(preset_id) is not int or preset_id <= 0:
            raise ValueError("invalid preset delete")
        async with self._db._write_lock:
            cursor = await self._db.conn.execute(
                "DELETE FROM streamer_template_presets WHERE id=? AND "
                "broadcaster_id IN (SELECT broadcaster_id FROM streamer_identities "
                "WHERE telegram_user_id=?)", (preset_id, user_id),
            )
            await self._db.conn.commit()
            return cursor.rowcount == 1

    async def apply(
        self, user_id: int, preset_id: int, chat_id: int, *,
        expected_version: int, now: float,
    ) -> int:
        if (
            type(user_id) is not int or user_id <= 0
            or type(preset_id) is not int or preset_id <= 0
            or type(chat_id) is not int or chat_id >= 0
            or type(expected_version) is not int or expected_version < 0
            or not isinstance(now, (int, float)) or not math.isfinite(now)
        ):
            raise ValueError("invalid preset application")
        cursor = await self._db.conn.execute(
            "SELECT p.headline,p.body,p.buttons_json,i.broadcaster_id "
            "FROM streamer_template_presets p JOIN streamer_identities i "
            "ON i.broadcaster_id=p.broadcaster_id "
            "WHERE p.id=? AND i.telegram_user_id=?", (preset_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PermissionError("preset denied")
        placement, plus = await self._db.get_streamer_placement_capabilities(
            row[3], chat_id, now=now,
        )
        if not placement or not plus:
            raise PermissionError("placement or Plus denied")
        version = await self._db.save_streamer_template(
            user_id, chat_id, expected_version=expected_version,
            headline=row[0], body=row[1], buttons=json.loads(row[2]), now=now,
        )
        if version is None:
            placement, plus = await self._db.get_streamer_placement_capabilities(
                row[3], chat_id, now=now,
            )
            if not placement or not plus:
                raise PermissionError("placement or Plus denied")
            raise PresetStale("stale template version")
        return version

    async def compare_posts(self, user_id: int, *, now: float) -> dict[str, int]:
        if (type(user_id) is not int or user_id <= 0
                or not isinstance(now, (int, float)) or not math.isfinite(now)):
            raise ValueError("invalid comparison request")
        if not await self._db.has_streamer_plus(user_id, now=now):
            raise PermissionError("Streamer Plus required")
        cursor = await self._db.conn.execute(
            "SELECT COUNT(CASE WHEN e.published_at>=? THEN 1 END), "
            "COUNT(CASE WHEN e.published_at<? THEN 1 END) "
            "FROM streamer_post_events e JOIN streamer_identities i "
            "ON i.broadcaster_id=e.broadcaster_id "
            "WHERE i.telegram_user_id=? AND e.chat_id<0 "
            "AND e.published_at>=? AND e.published_at<?",
            (now - 7 * 86400, now - 7 * 86400, user_id,
             now - 14 * 86400, now),
        )
        current, previous = await cursor.fetchone()
        return {"period_days": 7, "current_posts": int(current),
                "previous_posts": int(previous)}
