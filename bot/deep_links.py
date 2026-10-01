from __future__ import annotations

import re


# Production-бот у проекта один и публичный username стабилен. Константа позволяет
# строить ссылку без Telegram get_me() на каждом создании/редактировании live-поста.
TELEGRAM_BOT_USERNAME = "twitchSignalBot"
TRACK_START_PREFIX = "track_"
TELEGRAM_START_PAYLOAD_MAX_BYTES = 64

TWITCH_LOGIN_RE = re.compile(r"^[a-zA-Z0-9_]{4,25}$")
BOT_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
REFERRAL_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")


def _bot_username(value: str) -> str:
    if not isinstance(value, str) or BOT_USERNAME_RE.fullmatch(value) is None:
        raise ValueError("Invalid Telegram bot username")
    return value


def normalize_twitch_login(value: str) -> str | None:
    """Возвращает безопасный Twitch login или None.

    Deep-link payload приходит от клиента и считается недоверенным. Разрешены
    только символы настоящего Twitch login; URL, пробелы и произвольные данные не
    принимаются.
    """
    if not isinstance(value, str) or not TWITCH_LOGIN_RE.fullmatch(value):
        return None
    return value.lower()


def build_track_deep_link(
    login: str, *, bot_username: str = TELEGRAM_BOT_USERNAME,
) -> str:
    normalized = normalize_twitch_login(login)
    if normalized is None:
        raise ValueError("Invalid Twitch login for Telegram deep-link")

    payload = f"{TRACK_START_PREFIX}{normalized}"
    if len(payload.encode("ascii")) > TELEGRAM_START_PAYLOAD_MAX_BYTES:
        raise ValueError("Telegram deep-link payload is too long")
    return f"https://t.me/{_bot_username(bot_username)}?start={payload}"


def parse_growth_start_payload(payload: str) -> tuple[str, str] | None:
    if not isinstance(payload, str) or not payload.isascii() or len(payload) > TELEGRAM_START_PAYLOAD_MAX_BYTES:
        return None
    if payload == "src_site":
        return "site", "site"
    if payload.startswith("ref_") and REFERRAL_CODE_RE.fullmatch(payload[4:]):
        return "referral", payload[4:]
    return None


def build_growth_deep_link(bot_username: str, payload: str) -> str:
    if parse_growth_start_payload(payload) is None:
        raise ValueError("Invalid growth deep-link payload")
    return f"https://t.me/{_bot_username(bot_username)}?start={payload}"


def parse_track_start_payload(payload: str) -> str | None:
    if not isinstance(payload, str) or not payload.startswith(TRACK_START_PREFIX):
        return None
    if len(payload.encode("utf-8")) > TELEGRAM_START_PAYLOAD_MAX_BYTES:
        return None
    return normalize_twitch_login(payload.removeprefix(TRACK_START_PREFIX))
