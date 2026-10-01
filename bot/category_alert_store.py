"""Transactional category detector state and append-only transition IDs."""

from __future__ import annotations

import math

from .category_alerts import (
    CategoryObservation, CategoryState, CategoryTransition, advance_category,
    validate_observation,
)
from .database import Database


class CategoryAlertStore:
    def __init__(self, db: Database, *, poll_interval: float):
        if (not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool)
                or not math.isfinite(poll_interval) or poll_interval <= 0):
            raise ValueError("invalid poll interval")
        self._db = db
        self._gap_seconds = max(120.0, 3.0 * poll_interval)

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
                await self._db.conn.commit()
                return transition
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
