"""Short-lived staging-only owner sessions for the read-only panel."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import json
from collections.abc import Mapping
from urllib.parse import parse_qsl


class AdminAccess:
    def __init__(
        self,
        key: str,
        *,
        enabled: bool,
        secure_cookie: bool = True,
        session_ttl: float = 12 * 60 * 60,
        owner_id: int | None = None,
        bot_token: str = "",
        bot_username: str = "",
        public_base_url: str = "",
    ) -> None:
        if enabled and len(key) < 32:
            raise ValueError("Admin access key must be at least 32 characters")
        self.enabled = enabled
        self.secure_cookie = secure_cookie
        self.session_ttl = session_ttl
        self._key = key
        self.owner_id = owner_id
        self.bot_username = bot_username
        self.public_base_url = public_base_url.rstrip("/")
        self._bot_token = bot_token
        self._sessions: dict[str, float] = {}
        self._failures: dict[str, list[float]] = {}
        self._login_states: dict[str, float] = {}

    def login(self, candidate: str, remote: str) -> tuple[str | None, bool]:
        now = time.monotonic()
        failures = [at for at in self._failures.get(remote, []) if now - at < 60]
        if len(failures) >= 5:
            self._failures[remote] = failures
            return None, True
        if not self.enabled or not hmac.compare_digest(
            candidate.encode("utf-8"), self._key.encode("utf-8")
        ):
            failures.append(now)
            self._failures[remote] = failures
            return None, False
        self._failures.pop(remote, None)
        return self._new_session(now), False

    def login_webapp(self, init_data: str) -> str | None:
        if not self.enabled or self.owner_id is None or not self._bot_token or len(init_data) > 4096:
            return None
        try:
            pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
            if len(pairs) != len({key for key, _ in pairs}):
                return None
            fields = dict(pairs)
            signature = fields.pop("hash")
            if not self._fresh(fields["auth_date"]):
                return None
            check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
            secret = hmac.new(b"WebAppData", self._bot_token.encode(), hashlib.sha256).digest()
            expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return None
            user = json.loads(fields["user"])
            if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] != self.owner_id:
                return None
        except (ValueError, KeyError, TypeError, UnicodeError, json.JSONDecodeError):
            return None
        return self._new_session(time.monotonic())

    def login_telegram_widget(self, values: Mapping[str, str]) -> str | None:
        if not self.enabled or self.owner_id is None or not self._bot_token:
            return None
        try:
            fields = dict(values)
            signature = fields.pop("hash")
            if not self._fresh(fields["auth_date"]):
                return None
            if int(fields["id"]) != self.owner_id or fields["id"] != str(self.owner_id):
                return None
            check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
            secret = hashlib.sha256(self._bot_token.encode()).digest()
            expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return None
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None
        return self._new_session(time.monotonic())

    def new_login_state(self) -> str:
        state = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._login_states = {key: expiry for key, expiry in self._login_states.items() if expiry > now}
        if len(self._login_states) >= 32:
            self._login_states.pop(next(iter(self._login_states)))
        self._login_states[self._digest(state)] = now + 300
        return state

    def consume_login_state(self, candidate: str, cookie: str | None) -> bool:
        if not candidate or not cookie or not hmac.compare_digest(candidate.encode("utf-8"), cookie.encode("utf-8")):
            return False
        expiry = self._login_states.pop(self._digest(candidate), None)
        return expiry is not None and expiry > time.monotonic()

    @staticmethod
    def _fresh(raw: str) -> bool:
        if not raw.isascii() or not raw.isdecimal():
            return False
        age = time.time() - int(raw)
        return -60 <= age <= 600

    def _new_session(self, now: float) -> str:
        self._prune(now)
        if len(self._sessions) >= 32:
            oldest = min(self._sessions, key=self._sessions.__getitem__)
            self._sessions.pop(oldest, None)
        token = secrets.token_urlsafe(32)
        self._sessions[self._digest(token)] = now + self.session_ttl
        return token

    def authenticated(self, token: str | None) -> bool:
        if not self.enabled or not token:
            return False
        digest = self._digest(token)
        expires = self._sessions.get(digest)
        if expires is None:
            return False
        if expires <= time.monotonic():
            self._sessions.pop(digest, None)
            return False
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(self._digest(token), None)

    def _prune(self, now: float) -> None:
        for digest, expires in list(self._sessions.items()):
            if expires <= now:
                self._sessions.pop(digest, None)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
