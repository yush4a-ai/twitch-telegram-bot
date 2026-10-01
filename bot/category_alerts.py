"""Pure state machine for stable, unique Twitch category transitions."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CategoryObservation:
    broadcaster_id: str
    logical_stream_id: str
    category_id: str | None
    category_name: str | None
    observed_at: float
    is_live: bool


@dataclass(frozen=True)
class CategoryState:
    broadcaster_id: str
    logical_stream_id: str
    baseline_id: str
    baseline_name: str | None
    candidate_id: str | None
    candidate_name: str | None
    candidate_since: float | None
    candidate_count: int
    last_observed_at: float
    sequence: int
    is_live: bool


@dataclass(frozen=True)
class CategoryTransition:
    transition_id: str
    broadcaster_id: str
    logical_stream_id: str
    sequence: int
    from_category_id: str
    from_category_name: str | None
    to_category_id: str
    to_category_name: str | None
    observed_at: float


def validate_observation(observation: CategoryObservation) -> None:
    if not isinstance(observation, CategoryObservation):
        raise ValueError("invalid category observation")
    if (
        not isinstance(observation.broadcaster_id, str)
        or not observation.broadcaster_id.isascii()
        or not observation.broadcaster_id.isdecimal()
        or not 1 <= len(observation.broadcaster_id) <= 20
        or int(observation.broadcaster_id) <= 0
        or not isinstance(observation.logical_stream_id, str)
        or not 1 <= len(observation.logical_stream_id) <= 128
        or any(ord(char) < 33 or ord(char) > 126 for char in observation.logical_stream_id)
        or type(observation.observed_at) not in (int, float)
        or not math.isfinite(observation.observed_at)
        or observation.observed_at < 0
        or type(observation.is_live) is not bool
    ):
        raise ValueError("invalid category observation")


def _category_id(value: str | None) -> str | None:
    return (value if isinstance(value, str) and value.isascii()
            and value.isdecimal() and len(value) <= 32 and int(value) > 0 else None)


def _category_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name[:100] if name else None


def _baseline(observation: CategoryObservation, *, sequence: int = 0) -> CategoryState:
    return CategoryState(
        broadcaster_id=observation.broadcaster_id,
        logical_stream_id=observation.logical_stream_id,
        baseline_id=observation.category_id,
        baseline_name=_category_name(observation.category_name),
        candidate_id=None, candidate_name=None, candidate_since=None,
        candidate_count=0, last_observed_at=observation.observed_at,
        sequence=sequence, is_live=True,
    )


def advance_category(
    previous: CategoryState | None, observation: CategoryObservation,
    *, gap_seconds: float,
) -> tuple[CategoryState | None, CategoryTransition | None, bool]:
    """Return new state, confirmed transition, whether persistence changed."""
    validate_observation(observation)
    if previous is not None and observation.observed_at <= previous.last_observed_at:
        return previous, None, False
    if not observation.is_live:
        if previous is None:
            return None, None, False
        return replace(
            previous, candidate_id=None, candidate_name=None,
            candidate_since=None, candidate_count=0,
            last_observed_at=observation.observed_at, is_live=False,
        ), None, True
    category_id = _category_id(observation.category_id)
    if category_id is None:
        return previous, None, False
    valid = replace(observation, category_id=category_id)
    if previous is None or previous.logical_stream_id != valid.logical_stream_id:
        return _baseline(valid), None, True
    if not previous.is_live:
        return _baseline(valid, sequence=previous.sequence), None, True
    if valid.observed_at - previous.last_observed_at > gap_seconds:
        return _baseline(valid, sequence=previous.sequence), None, True
    name = _category_name(valid.category_name)
    if category_id == previous.baseline_id:
        return replace(
            previous, baseline_name=name or previous.baseline_name,
            candidate_id=None, candidate_name=None, candidate_since=None,
            candidate_count=0, last_observed_at=valid.observed_at,
        ), None, True
    if category_id != previous.candidate_id:
        return replace(
            previous, candidate_id=category_id, candidate_name=name,
            candidate_since=valid.observed_at, candidate_count=1,
            last_observed_at=valid.observed_at,
        ), None, True
    count = previous.candidate_count + 1
    if count < 2 or valid.observed_at - previous.candidate_since < 60:
        return replace(
            previous, candidate_name=name or previous.candidate_name,
            candidate_count=count, last_observed_at=valid.observed_at,
        ), None, True
    sequence = previous.sequence + 1
    identity = f"{valid.broadcaster_id}\0{valid.logical_stream_id}\0{sequence}".encode("utf-8")
    transition = CategoryTransition(
        transition_id=hashlib.sha256(identity).hexdigest(),
        broadcaster_id=valid.broadcaster_id,
        logical_stream_id=valid.logical_stream_id,
        sequence=sequence,
        from_category_id=previous.baseline_id,
        from_category_name=previous.baseline_name,
        to_category_id=category_id,
        to_category_name=name or previous.candidate_name,
        observed_at=valid.observed_at,
    )
    return _baseline(valid, sequence=sequence), transition, True
