"""Short-lived staging-only owner sessions for the read-only panel."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping

from .telegram_identity import verify_login_widget_user, verify_webapp_user
from .login_states import LoginStates


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
        self._csrf: dict[str, list[tuple[str, float]]] = {}
        self.csrf_ttl = 15 * 60
        self._login_states = LoginStates(32)
        # Адреса, с которых владелец уже успешно входил: им разрешено получить
        # состояние входа даже при полностью занятом чужом пуле.
        self._owner_clients: dict[str, float] = {}

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
        return self._new_session(now, remote), False

    def login_webapp(self, init_data: str, remote: str | None = None) -> str | None:
        if not self.enabled or self.owner_id is None:
            return None
        if verify_webapp_user(init_data, self._bot_token) != self.owner_id:
            return None
        return self._new_session(time.monotonic(), remote)

    def login_telegram_widget(self, values: Mapping[str, str], remote: str | None = None) -> str | None:
        if self.verified_widget_user(values) is None:
            return None
        return self._new_session(time.monotonic(), remote)

    def verified_widget_user(self, values: Mapping[str, str]) -> int | None:
        if not self.enabled or self.owner_id is None:
            return None
        if verify_login_widget_user(values, self._bot_token) != self.owner_id:
            return None
        return self.owner_id

    def new_login_state(self, client: str = '', existing: str | None = None) -> str | None:
        return self._login_states.new(client, existing, trusted=self._is_owner_client(client))

    def _remember_owner_client(self, remote: str | None, now: float) -> None:
        if not remote:
            return
        digest = self._login_states.digest(remote)
        self._owner_clients[digest] = now + 86400
        if len(self._owner_clients) > 16:
            oldest = min(self._owner_clients, key=self._owner_clients.__getitem__)
            self._owner_clients.pop(oldest, None)

    def _is_owner_client(self, client: str) -> bool:
        if not client:
            return False
        stamp = self._owner_clients.get(self._login_states.digest(client))
        return stamp is not None and stamp > time.monotonic()

    def consume_login_state(self, candidate: str, cookie: str | None) -> bool:
        return self._login_states.consume(candidate, cookie)

    def _new_session(self, now: float, remote: str | None = None) -> str:
        self._prune(now)
        # Успешный вход запоминает адрес: следующий раз он получит резерв в пуле.
        self._remember_owner_client(remote, now)
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
            digest = self._digest(token)
            self._sessions.pop(digest, None)
            self._csrf.pop(digest, None)

    def issue_csrf(self, token: str | None) -> str | None:
        """Метка для write-запросов панели: до четырёх активных на сессию."""
        if not self.authenticated(token):
            return None
        digest = self._digest(token)
        now = time.monotonic()
        active = [(value, expires) for value, expires in self._csrf.get(digest, [])
                  if expires > now][-3:]
        value = secrets.token_urlsafe(24)
        active.append((value, now + self.csrf_ttl))
        self._csrf[digest] = active
        return value

    def check_csrf(self, token: str | None, value: str | None) -> bool:
        """Проверка без потребления: метка не сгорает на отказе валидации."""
        if not self.authenticated(token) or not value:
            return False
        now = time.monotonic()
        return any(
            expires > now and hmac.compare_digest(item, value)
            for item, expires in self._csrf.get(self._digest(token), [])
        )

    def consume_csrf(self, token: str | None, value: str | None) -> bool:
        """Метка одноразовая: успешная проверка удаляет её."""
        if not self.authenticated(token) or not value:
            return False
        digest = self._digest(token)
        now = time.monotonic()
        active = [(item, expires) for item, expires in self._csrf.get(digest, [])
                  if expires > now]
        for index, (item, expires) in enumerate(active):
            if hmac.compare_digest(item, value):
                active.pop(index)
                self._csrf[digest] = active
                return True
        self._csrf[digest] = active
        return False

    def _prune(self, now: float) -> None:
        for digest, expires in list(self._sessions.items()):
            if expires <= now:
                self._sessions.pop(digest, None)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
