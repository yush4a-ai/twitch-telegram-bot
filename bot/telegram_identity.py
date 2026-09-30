"""Server-side verification of Telegram WebApp and Login Widget identities."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl


def _fresh(raw: str) -> bool:
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        return False
    age = time.time() - int(raw)
    return -60 <= age <= 600


def verify_webapp_user(init_data: str, bot_token: str) -> int | None:
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
        user = json.loads(fields["user"])
        user_id = user.get("id") if isinstance(user, dict) else None
        return user_id if type(user_id) is int and user_id > 0 else None
    except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


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
        if user_id <= 0 or raw_id != str(user_id):
            return None
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hashlib.sha256(bot_token.encode()).digest()
        expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return user_id if hmac.compare_digest(expected, signature) else None
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None
