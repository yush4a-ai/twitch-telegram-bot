"""Separate short-lived sessions for verified streamer Telegram identities."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping

from .telegram_identity import verify_login_widget_user, verify_webapp_user
from .login_states import LoginStates


class StreamerAccess:
    def __init__(
        self, bot_token: str, *, enabled: bool = True, bot_username: str = "",
        public_base_url: str = "", secure_cookie: bool = True,
        session_ttl: float = 12 * 60 * 60,
    ) -> None:
        self.enabled = enabled and bool(bot_token)
        self.bot_username = bot_username
        self.public_base_url = public_base_url.rstrip("/")
        self.secure_cookie = secure_cookie
        self.session_ttl = session_ttl
        self._bot_token = bot_token
        self._sessions: dict[str, tuple[int, float]] = {}
        self._login_states = LoginStates(128)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _new_session(self, user_id: int) -> str | None:
        now = time.monotonic()
        self._sessions = {
            key: value for key, value in self._sessions.items() if value[1] > now
        }
        own = [key for key, row in self._sessions.items() if row[0] == user_id]
        if len(own) >= 4 or (len(self._sessions) >= 128 and own):
            self._sessions.pop(min(own, key=lambda key: self._sessions[key][1]))
        if len(self._sessions) >= 128:
            return None
        token = secrets.token_urlsafe(32)
        self._sessions[self._digest(token)] = (user_id, now + self.session_ttl)
        return token

    def login_webapp(self, init_data: str) -> str | None:
        user_id = verify_webapp_user(init_data, self._bot_token) if self.enabled else None
        return self._new_session(user_id) if user_id is not None else None

    def login_telegram_widget(self, values: Mapping[str, str]) -> str | None:
        user_id = self.verified_widget_user(values)
        return self._new_session(user_id) if user_id is not None else None

    def verified_widget_user(self, values: Mapping[str, str]) -> int | None:
        return verify_login_widget_user(values, self._bot_token) if self.enabled else None

    def user_for_session(self, token: str | None) -> int | None:
        if not self.enabled or not token:
            return None
        row = self._sessions.get(self._digest(token))
        if row is None:
            return None
        if row[1] <= time.monotonic():
            self._sessions.pop(self._digest(token), None)
            return None
        return row[0]

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(self._digest(token), None)

    def new_login_state(self, client: str = '', existing: str | None = None) -> str | None:
        return self._login_states.new(client, existing)

    def consume_login_state(self, candidate: str, cookie: str | None) -> bool:
        return self._login_states.consume(candidate, cookie)
