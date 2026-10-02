"""Fail-closed capabilities, including personal Viewer access from Streamer Plus."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .database import Database
from .plan_catalog import viewer_channel_limit, viewer_video_slots


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserCapabilities:
    basic_notifications: bool
    viewer_plus_active: bool
    viewer_filters: bool
    viewer_category_alerts: bool
    viewer_video_slots: int
    viewer_channel_limit: int
    streamer_plus_active: bool
    streamer_preview: bool
    streamer_custom_post: bool
    streamer_post_stats: bool


@dataclass(frozen=True)
class PlacementCapabilities:
    basic_post: bool
    streamer_preview: bool
    streamer_custom_post: bool
    streamer_post_stats: bool


def _valid_now(now: float) -> bool:
    return type(now) in (int, float) and math.isfinite(now)


class CapabilityService:
    def __init__(self, db: Database):
        self._db = db

    async def for_user(self, telegram_user_id: int, *, now: float) -> UserCapabilities:
        if type(telegram_user_id) is not int or telegram_user_id <= 0 or not _valid_now(now):
            raise ValueError("invalid capability subject")
        try:
            viewer_plus = await self._db.has_viewer_plus(telegram_user_id, now=now)
        except Exception:
            logger.exception("Viewer capability lookup failed")
            viewer_plus = False
        try:
            streamer_plus = await self._db.has_streamer_plus(telegram_user_id, now=now)
        except Exception:
            logger.exception("Streamer capability lookup failed")
            streamer_plus = False
        return UserCapabilities(
            basic_notifications=True,
            viewer_plus_active=viewer_plus,
            viewer_filters=viewer_plus,
            viewer_category_alerts=viewer_plus,
            viewer_video_slots=viewer_video_slots(viewer_plus),
            viewer_channel_limit=viewer_channel_limit(viewer_plus),
            streamer_plus_active=streamer_plus,
            streamer_preview=streamer_plus,
            streamer_custom_post=streamer_plus,
            streamer_post_stats=streamer_plus,
        )

    async def for_placement(
        self, broadcaster_id: str, chat_id: int, *, now: float
    ) -> PlacementCapabilities:
        if (
            not isinstance(broadcaster_id, str)
            or not broadcaster_id.isascii()
            or not broadcaster_id.isdecimal()
            or int(broadcaster_id) <= 0
            or type(chat_id) is not int
            or chat_id >= 0
            or not _valid_now(now)
        ):
            raise ValueError("invalid placement subject")
        try:
            basic, plus = await self._db.get_streamer_placement_capabilities(
                broadcaster_id, chat_id, now=now
            )
        except Exception:
            logger.exception("Placement capability lookup failed")
            basic, plus = False, False
        return PlacementCapabilities(
            basic_post=basic,
            streamer_preview=plus,
            streamer_custom_post=plus,
            streamer_post_stats=plus,
        )
