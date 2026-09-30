"""Short-lived staging-only owner sessions for the read-only panel."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time


class AdminAccess:
    def __init__(
        self,
        key: str,
        *,
        enabled: bool,
        secure_cookie: bool = True,
        session_ttl: float = 12 * 60 * 60,
    ) -> None:
        if enabled and len(key) < 32:
            raise ValueError("Admin access key must be at least 32 characters")
        self.enabled = enabled
        self.secure_cookie = secure_cookie
        self.session_ttl = session_ttl
        self._key = key
        self._sessions: dict[str, float] = {}
        self._failures: dict[str, list[float]] = {}

    def login(self, candidate: str, remote: str) -> tuple[str | None, bool]:
        now = time.monotonic()
        failures = [at for at in self._failures.get(remote, []) if now - at < 60]
        if len(failures) >= 5:
            self._failures[remote] = failures
            return None, True
        if not self.enabled or not hmac.compare_digest(candidate, self._key):
            failures.append(now)
            self._failures[remote] = failures
            return None, False
        self._failures.pop(remote, None)
        self._prune(now)
        if len(self._sessions) >= 32:
            oldest = min(self._sessions, key=self._sessions.__getitem__)
            self._sessions.pop(oldest, None)
        token = secrets.token_urlsafe(32)
        self._sessions[self._digest(token)] = now + self.session_ttl
        return token, False

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
