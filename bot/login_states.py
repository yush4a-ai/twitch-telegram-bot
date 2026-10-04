"""Bounded anonymous login drafts; capacity never evicts another client."""
import hashlib
import hmac
import secrets
import time


class LoginStates:
    def __init__(self, capacity):
        self.capacity = capacity
        self.entries = {}

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def new(self, client='', existing=None):
        now = time.monotonic()
        self.entries = {key: row for key, row in self.entries.items() if row[1] > now}
        owner = self.digest(client)
        if existing:
            row = self.entries.get(self.digest(existing))
            if row is not None and row[0] == owner:
                return existing
        if len(self.entries) >= self.capacity or sum(row[0] == owner for row in self.entries.values()) >= 4:
            return None
        state = secrets.token_urlsafe(24)
        self.entries[self.digest(state)] = (owner, now+300)
        return state

    def consume(self, candidate, cookie):
        if not candidate or not cookie:
            return False
        try:
            if not hmac.compare_digest(candidate.encode(), cookie.encode()):
                return False
        except UnicodeError:
            return False
        row = self.entries.pop(self.digest(candidate), None)
        return row is not None and row[1] > time.monotonic()
