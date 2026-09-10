from __future__ import annotations

import re


# Production-бот у проекта один и публичный username стабилен. Константа позволяет
# строить ссылку без Telegram get_me() на каждом создании/редактировании live-поста.
TELEGRAM_BOT_USERNAME = "twitchSignalBot"
TRACK_START_PREFIX = "track_"
TELEGRAM_START_PAYLOAD_MAX_BYTES = 64

TWITCH_LOGIN_RE = re.compile(r"^[a-zA-Z0-9_]{4,25}$")


def normalize_twitch_login(value: str) -> str | None:
    """Возвращает безопасный Twitch login или None.

    Deep-link payload приходит от клиента и считается недоверенным. Разрешены
    только символы настоящего Twitch login; URL, пробелы и произвольные данные не
    принимаются.
    """
    if not isinstance(value, str) or not TWITCH_LOGIN_RE.fullmatch(value):
        return None
    return value.lower()


def build_track_deep_link(login: str) -> str:
    normalized = normalize_twitch_login(login)
    if normalized is None:
        raise ValueError("Invalid Twitch login for Telegram deep-link")

    payload = f"{TRACK_START_PREFIX}{normalized}"
    if len(payload.encode("ascii")) > TELEGRAM_START_PAYLOAD_MAX_BYTES:
        raise ValueError("Telegram deep-link payload is too long")
    return f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={payload}"


def parse_track_start_payload(payload: str) -> str | None:
    if not isinstance(payload, str) or not payload.startswith(TRACK_START_PREFIX):
        return None
    if len(payload.encode("utf-8")) > TELEGRAM_START_PAYLOAD_MAX_BYTES:
        return None
    return normalize_twitch_login(payload.removeprefix(TRACK_START_PREFIX))
