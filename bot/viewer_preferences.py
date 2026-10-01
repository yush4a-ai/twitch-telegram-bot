"""Validated personal video choices, separate from legacy chat preview flags."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .deep_links import TWITCH_LOGIN_RE
from .plan_catalog import VIEWER_PLUS_VIDEO_SLOTS


_TWITCH_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


@dataclass(frozen=True)
class VideoSelection:
    version: int
    selected_ids: tuple[str, ...]
    effective_ids: tuple[str, ...]
    selected_logins: tuple[str, ...]
    limit: int = VIEWER_PLUS_VIDEO_SLOTS


def validate_video_choices(choices: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(choices, (list, tuple)) or len(choices) > VIEWER_PLUS_VIDEO_SLOTS:
        raise ValueError("invalid video choices")
    rows: list[tuple[str, str]] = []
    for choice in choices:
        if not isinstance(choice, (list, tuple)) or len(choice) != 2:
            raise ValueError("invalid video choice")
        broadcaster_id, login = choice
        if (
            not isinstance(broadcaster_id, str)
            or _TWITCH_ID.fullmatch(broadcaster_id) is None
            or not isinstance(login, str)
            or TWITCH_LOGIN_RE.fullmatch(login) is None
            or login != login.lower()
        ):
            raise ValueError("invalid video choice")
        rows.append((broadcaster_id, login))
    if len({row[0] for row in rows}) != len(rows) or len({row[1] for row in rows}) != len(rows):
        raise ValueError("duplicate video choice")
    return tuple(rows)
