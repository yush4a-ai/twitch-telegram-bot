"""Approved server-side feature limits; no payment prices or purchase terms."""

FREE_VIEWER_CHANNEL_LIMIT = 50
VIEWER_PLUS_CHANNEL_LIMIT = 200
VIEWER_PLUS_VIDEO_SLOTS = 5


def viewer_channel_limit(plus_active: bool) -> int:
    return VIEWER_PLUS_CHANNEL_LIMIT if plus_active else FREE_VIEWER_CHANNEL_LIMIT


def viewer_video_slots(plus_active: bool) -> int:
    return VIEWER_PLUS_VIDEO_SLOTS if plus_active else 0
