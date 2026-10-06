"""Server-side verification of Telegram WebApp and Login Widget identities."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl


# Верхняя граница идентификатора Telegram. Реальные id на порядки меньше, а всё
# выше этой границы не помещается в целочисленные поля SQLite: подпись проходила,
# а маршрут падал 500-й ошибкой вместо честного отказа.
MAX_TELEGRAM_USER_ID = 2 ** 52


def _fresh(raw: str) -> bool:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        return False
    age = time.time() - int(raw)
    return -60 <= age <= 600


def _supported_user_id(user_id: object) -> bool:
    """Только целое (не bool) в поддерживаемом диапазоне 0 < id < 2**52."""
    return type(user_id) is int and 0 < user_id < MAX_TELEGRAM_USER_ID


@dataclass(frozen=True)
class VerifiedTelegramIdentity:
    id: int
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None


def _unique_user(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate Telegram user field")
        values[key] = value
    return values


def _optional_label(value):
    return value.strip() if isinstance(value, str) and 0 < len(value) <= 256 and value.strip() else None


def verify_webapp_identity(init_data: str, bot_token: str) -> VerifiedTelegramIdentity | None:
    if not isinstance(init_data, str) or not bot_token or len(init_data) > 4096:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != len({key for key, _ in pairs}):
            return None
        fields = dict(pairs)
        signature = fields.pop("hash")
        if not _fresh(fields["auth_date"]):
            return None
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        user = json.loads(fields["user"], object_pairs_hook=_unique_user)
        user_id = user.get("id") if isinstance(user, dict) else None
        if not _supported_user_id(user_id):
            return None
        first, last = _optional_label(user.get("first_name")), _optional_label(user.get("last_name"))
        display = " ".join(part for part in (first, last) if part) or None
        if display is not None and len(display) > 256:
            display = None
        photo = user.get('photo_url')
        avatar = photo if isinstance(photo,str) and len(photo)<=2048 and re.fullmatch(r'https://t\.me/i/userpic/[A-Za-z0-9_./=-]+',photo) else None
        return VerifiedTelegramIdentity(user_id, display, _optional_label(user.get("username")), avatar)
    except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


def verify_webapp_user(init_data: str, bot_token: str) -> int | None:
    identity = verify_webapp_identity(init_data, bot_token)
    return identity.id if identity is not None else None


def verify_login_widget_user(values: Mapping[str, str], bot_token: str) -> int | None:
    if not bot_token:
        return None
    try:
        fields = dict(values)
        signature = fields.pop("hash")
        raw_id = fields["id"]
        if not _fresh(fields["auth_date"]) or not raw_id.isascii() or not raw_id.isdecimal():
            return None
        user_id = int(raw_id)
        if not _supported_user_id(user_id) or raw_id != str(user_id):
            return None
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hashlib.sha256(bot_token.encode()).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return user_id if hmac.compare_digest(expected, signature) else None
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None
