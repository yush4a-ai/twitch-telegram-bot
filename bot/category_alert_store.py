"""Transactional category detector state and append-only transition IDs."""

from __future__ import annotations

import math
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .category_alerts import (
    CategoryObservation, CategoryState, CategoryTransition, advance_category,
    validate_observation,
)
from .database import Database
from .deep_links import TWITCH_LOGIN_RE
from .viewer_filter import matches_viewer_filter


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryAlertPreference:
    enabled: bool
    category_ids: tuple[str, ...]
    version: int
    category_names: tuple[str, ...] = ()


def _selected_ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > 5:
        raise ValueError("invalid category choices")
    ids = tuple(values)
    if any(
        not isinstance(value, str) or not value.isascii() or not value.isdecimal()
        or not 1 <= len(value) <= 32 or int(value) <= 0
        for value in ids
    ) or len(set(ids)) != len(ids):
        raise ValueError("invalid category choices")
    return ids


def _quiet_at(quiet: tuple[int, int, int, bool] | None, at: float) -> bool:
    if quiet is None:
        return False
    utc = datetime.fromtimestamp(at, timezone.utc)
    minute = utc.hour * 60 + utc.minute
    start, end = quiet[:2]
    return start <= minute < end if start <= end else minute >= start or minute < end


class CategoryAlertStore:
    def __init__(self, db: Database, *, poll_interval: float):
        if (not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool)
                or not math.isfinite(poll_interval) or poll_interval <= 0):
            raise ValueError("invalid poll interval")
        self._db = db
        self._gap_seconds = max(120.0, 3.0 * poll_interval)

    async def get_preference(self, user_id: int, login: str) -> CategoryAlertPreference:
        cursor = await self._db.conn.execute(
            "SELECT enabled,category_ids_json,version,category_names_json "
            "FROM category_alert_preferences "
            "WHERE telegram_user_id=? AND twitch_login=?", (user_id, login),
        )
        row = await cursor.fetchone()
        return (CategoryAlertPreference(bool(row[0]), tuple(json.loads(row[1])),
                                        row[2], tuple(json.loads(row[3])))
                if row else CategoryAlertPreference(False, (), 0))

    async def list_preferences(self, user_id: int) -> dict[str, CategoryAlertPreference]:
        cursor = await self._db.conn.execute(
            "SELECT twitch_login,enabled,category_ids_json,version,category_names_json "
            "FROM category_alert_preferences WHERE telegram_user_id=?", (user_id,),
        )
        return {
            row[0]: CategoryAlertPreference(bool(row[1]), tuple(json.loads(row[2])),
                                             row[3], tuple(json.loads(row[4])))
            for row in await cursor.fetchall()
        }

    async def save_preference(
        self, user_id: int, login: str, *, enabled: bool,
        category_ids: object, expected_version: int, now: float,
        category_names: object = None,
    ) -> CategoryAlertPreference | None:
        ids = _selected_ids(category_ids)
        names = tuple(category_names if category_names is not None else ids)
        if (len(names) != len(ids) or any(
            not isinstance(name, str) or not 1 <= len(name) <= 100
            or any(ord(char) < 32 for char in name)
            for name in names
        )):
            raise ValueError("invalid category names")
        if (type(user_id) is not int or user_id <= 0 or not isinstance(login, str)
                or TWITCH_LOGIN_RE.fullmatch(login) is None or login != login.lower()
                or type(enabled) is not bool or type(expected_version) is not int
                or expected_version < 0 or type(now) not in (int, float)
                or not math.isfinite(now)):
            raise ValueError("invalid category preference")
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                if not await self._db.has_viewer_plus(user_id, now=now):
                    raise PermissionError("Viewer Plus is required")
                cursor = await self._db.conn.execute(
                    "SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
                    (user_id, login),
                )
                if await cursor.fetchone() is None:
                    raise LookupError("not subscribed")
                previous = await self.get_preference(user_id, login)
                if previous.version != expected_version:
                    await self._db.conn.rollback()
                    return None
                result = CategoryAlertPreference(enabled, ids, previous.version + 1, names)
                await self._db.conn.execute(
                    "INSERT INTO category_alert_preferences "
                    "(telegram_user_id,twitch_login,enabled,category_ids_json,category_names_json,version,updated_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(telegram_user_id,twitch_login) "
                    "DO UPDATE SET enabled=excluded.enabled, "
                    "category_ids_json=excluded.category_ids_json, "
                    "category_names_json=excluded.category_names_json, "
                    "version=excluded.version,updated_at=excluded.updated_at",
                    (user_id, login, int(enabled), json.dumps(ids),
                     json.dumps(names, ensure_ascii=False), result.version, now),
                )
                await self._db.conn.commit()
                return result
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def observe(self, observation: CategoryObservation) -> CategoryTransition | None:
        validate_observation(observation)
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.conn.execute(
                    "SELECT broadcaster_id,logical_stream_id,baseline_id,baseline_name, "
                    "candidate_id,candidate_name,candidate_since,candidate_count, "
                    "last_observed_at,sequence,is_live FROM category_alert_state "
                    "WHERE broadcaster_id=?", (observation.broadcaster_id,),
                )
                row = await cursor.fetchone()
                previous = CategoryState(*row) if row is not None else None
                current, transition, changed = advance_category(
                    previous, observation, gap_seconds=self._gap_seconds,
                )
                if changed and current is None:
                    await self._db.conn.execute(
                        "DELETE FROM category_alert_state WHERE broadcaster_id=?",
                        (observation.broadcaster_id,),
                    )
                elif changed:
                    await self._db.conn.execute(
                        "INSERT INTO category_alert_state "
                        "(broadcaster_id,logical_stream_id,baseline_id,baseline_name, "
                        "candidate_id,candidate_name,candidate_since,candidate_count, "
                        "last_observed_at,sequence,is_live) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(broadcaster_id) DO UPDATE SET "
                        "logical_stream_id=excluded.logical_stream_id, "
                        "baseline_id=excluded.baseline_id,baseline_name=excluded.baseline_name, "
                        "candidate_id=excluded.candidate_id,candidate_name=excluded.candidate_name, "
                        "candidate_since=excluded.candidate_since, "
                        "candidate_count=excluded.candidate_count, "
                        "last_observed_at=excluded.last_observed_at,sequence=excluded.sequence, "
                        "is_live=excluded.is_live",
                        (
                            current.broadcaster_id, current.logical_stream_id,
                            current.baseline_id, current.baseline_name,
                            current.candidate_id, current.candidate_name,
                            current.candidate_since, current.candidate_count,
                            current.last_observed_at, current.sequence, int(current.is_live),
                        ),
                    )
                if transition is not None:
                    await self._db.conn.execute(
                        "INSERT INTO category_transitions "
                        "(transition_id,broadcaster_id,logical_stream_id,sequence, "
                        "from_category_id,from_category_name,to_category_id, "
                        "to_category_name,observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            transition.transition_id, transition.broadcaster_id,
                            transition.logical_stream_id, transition.sequence,
                            transition.from_category_id, transition.from_category_name,
                            transition.to_category_id, transition.to_category_name,
                            transition.observed_at,
                        ),
                    )
                    await self._enqueue_transition_locked(transition, now=observation.observed_at)
                await self._db.conn.commit()
                return transition
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def enqueue_for_transition(self, transition_id: str, *, now: float) -> int:
        if not isinstance(transition_id, str) or len(transition_id) != 64 or not math.isfinite(now):
            raise ValueError("invalid transition enqueue")
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.conn.execute(
                    "SELECT transition_id,broadcaster_id,logical_stream_id,sequence, "
                    "from_category_id,from_category_name,to_category_id,to_category_name,observed_at "
                    "FROM category_transitions WHERE transition_id=?", (transition_id,),
                )
                row = await cursor.fetchone()
                result = await self._enqueue_transition_locked(CategoryTransition(*row), now=now) if row else 0
                await self._db.conn.commit()
                return result
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def _eligible(self, user_id: int, login: str, transition: CategoryTransition, now: float) -> bool:
        if not await self._db.has_viewer_plus(user_id, now=now):
            return False
        if not await self._db.is_personal_channel_active(user_id, login, now=now):
            return False
        preference = await self.get_preference(user_id, login)
        if not preference.enabled or (preference.category_ids and transition.to_category_id not in preference.category_ids):
            return False
        if _quiet_at(await self._db.get_quiet_hours(user_id), now):
            return False
        cursor = await self._db.conn.execute(
            "SELECT is_live,notify_enabled,last_stream_id,last_broadcaster_id,last_title "
            "FROM tracked_channels WHERE chat_id=? AND twitch_login=?", (user_id, login),
        )
        row = await cursor.fetchone()
        if (row is None or not row[0] or not row[1]
                or row[2] != transition.logical_stream_id or row[3] != transition.broadcaster_id):
            return False
        rule = await self._db.get_effective_viewer_filter(user_id, login, now=now)
        return rule is None or matches_viewer_filter(rule, transition.to_category_name, row[4])

    async def _enqueue_transition_locked(self, transition: CategoryTransition, *, now: float) -> int:
        cursor = await self._db.conn.execute(
            "SELECT logical_stream_id,baseline_id,is_live,last_observed_at "
            "FROM category_alert_state WHERE broadcaster_id=?", (transition.broadcaster_id,),
        )
        state = await cursor.fetchone()
        if (state is None or state[0] != transition.logical_stream_id
                or state[1] != transition.to_category_id or not state[2]
                or now - state[3] > self._gap_seconds):
            return 0
        cursor = await self._db.conn.execute(
            "SELECT t.chat_id,t.twitch_login FROM tracked_channels t "
            "JOIN category_alert_preferences p ON p.telegram_user_id=t.chat_id "
            "AND p.twitch_login=t.twitch_login AND p.enabled=1 "
            "WHERE t.chat_id>0 AND t.last_broadcaster_id=? "
            "AND t.last_stream_id=? AND t.is_live=1 AND t.notify_enabled=1 "
            "AND EXISTS (SELECT 1 FROM entitlement_grants g "
            "WHERE g.subject_kind='viewer' AND g.subject_id=CAST(t.chat_id AS TEXT) "
            "AND g.plan='viewer_plus' AND g.revoked_at IS NULL "
            "AND g.starts_at<=? AND g.expires_at>?)",
            (transition.broadcaster_id, transition.logical_stream_id, now, now),
        )
        recipients = await cursor.fetchall()
        added = 0
        for user_id, login in recipients:
            try:
                eligible = await self._eligible(user_id, login, transition, now)
            except (ValueError, TypeError):
                logger.warning("Invalid viewer category preference or filter skipped")
                continue
            if not eligible:
                continue
            cursor = await self._db.conn.execute(
                "SELECT last_sent_at FROM category_alert_delivery_state "
                "WHERE telegram_user_id=? AND broadcaster_id=? AND logical_stream_id=?",
                (user_id, transition.broadcaster_id, transition.logical_stream_id),
            )
            last = await cursor.fetchone()
            due = max(now, last[0] + 300) if last else now
            await self._db.conn.execute(
                "UPDATE notification_jobs SET status='failed',last_error_class='Superseded', "
                "updated_at=? WHERE kind='viewer_category_change' AND chat_id=? "
                "AND twitch_login=? AND logical_stream_id=? AND status='pending' "
                "AND category_transition_id<>?",
                (now, user_id, login, transition.logical_stream_id, transition.transition_id),
            )
            result = await self._db.conn.execute(
                "INSERT INTO notification_jobs "
                "(kind,chat_id,twitch_login,logical_stream_id,payload_version,due_at, "
                "status,attempt_count,lease_until,created_at,updated_at,category_transition_id) "
                "VALUES ('viewer_category_change',?,?,?,?,?,'pending',0,NULL,?,?,?) "
                "ON CONFLICT DO NOTHING",
                (user_id, login, transition.logical_stream_id, transition.sequence,
                 due, now, now, transition.transition_id),
            )
            added += result.rowcount
        return added

    async def ready_for_delivery(
        self, user_id: int, login: str, transition_id: str, *, now: float,
    ) -> CategoryTransition | None:
        cursor = await self._db.conn.execute(
            "SELECT transition_id,broadcaster_id,logical_stream_id,sequence, "
            "from_category_id,from_category_name,to_category_id,to_category_name,observed_at "
            "FROM category_transitions WHERE transition_id=?", (transition_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        transition = CategoryTransition(*row)
        cursor = await self._db.conn.execute(
            "SELECT logical_stream_id,baseline_id,is_live,last_observed_at "
            "FROM category_alert_state WHERE broadcaster_id=?", (transition.broadcaster_id,),
        )
        state = await cursor.fetchone()
        if (state is None or state[0] != transition.logical_stream_id
                or state[1] != transition.to_category_id or not state[2]
                or now - state[3] > self._gap_seconds):
            return None
        try:
            eligible = await self._eligible(user_id, login, transition, now)
        except (ValueError, TypeError):
            logger.warning("Invalid viewer category preference or filter skipped")
            return None
        if not eligible:
            return None
        cursor = await self._db.conn.execute(
            "SELECT last_transition_id,last_sent_at FROM category_alert_delivery_state "
            "WHERE telegram_user_id=? AND broadcaster_id=? AND logical_stream_id=?",
            (user_id, transition.broadcaster_id, transition.logical_stream_id),
        )
        sent = await cursor.fetchone()
        if sent and sent[0] == transition_id:
            return None
        cursor = await self._db.conn.execute(
            "SELECT transition_id FROM category_transitions WHERE broadcaster_id=? "
            "AND logical_stream_id=? ORDER BY observed_at DESC,sequence DESC LIMIT 1",
            (transition.broadcaster_id, transition.logical_stream_id),
        )
        latest = await cursor.fetchone()
        return transition if latest and latest[0] == transition_id else None

    async def cooldown_remaining(self, user_id: int, transition: CategoryTransition, *, now: float) -> float:
        cursor = await self._db.conn.execute(
            "SELECT last_sent_at FROM category_alert_delivery_state "
            "WHERE telegram_user_id=? AND broadcaster_id=? AND logical_stream_id=?",
            (user_id, transition.broadcaster_id, transition.logical_stream_id),
        )
        row = await cursor.fetchone()
        return max(0.0, row[0] + 300 - now) if row else 0.0

    async def record_success(self, user_id: int, transition_id: str, *, now: float) -> None:
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self._db.conn.execute(
                    "SELECT broadcaster_id,logical_stream_id FROM category_transitions "
                    "WHERE transition_id=?", (transition_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ValueError("unknown category transition")
                await self._db.conn.execute(
                    "INSERT INTO category_alert_delivery_state "
                    "(telegram_user_id,broadcaster_id,logical_stream_id,last_transition_id,last_sent_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(telegram_user_id,broadcaster_id,logical_stream_id) "
                    "DO UPDATE SET last_transition_id=excluded.last_transition_id, "
                    "last_sent_at=excluded.last_sent_at",
                    (user_id, row[0], row[1], transition_id, now),
                )
                await self._db.conn.commit()
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def list_transitions(self, broadcaster_id: str) -> list[CategoryTransition]:
        cursor = await self._db.conn.execute(
            "SELECT transition_id,broadcaster_id,logical_stream_id,sequence, "
            "from_category_id,from_category_name,to_category_id,to_category_name,observed_at "
            "FROM category_transitions WHERE broadcaster_id=? ORDER BY observed_at,sequence",
            (broadcaster_id,),
        )
        return [CategoryTransition(*row) for row in await cursor.fetchall()]
