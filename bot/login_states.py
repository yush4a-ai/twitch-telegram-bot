"""Bounded anonymous login drafts; capacity never evicts another client."""
import hashlib
import hmac
import secrets
import time

# Сколько новых состояний входа один источник может создать за минуту. Значение
# выше лимита одновременных состояний: человек, который несколько раз обновил
# страницу входа, не должен упираться в отказ.
PER_CLIENT_PER_MINUTE = 6
# Сколько состояний одного источника могут жить одновременно.
PER_CLIENT_ACTIVE = 4
# Насколько пул может вырасти сверх ёмкости для уже подтверждённого владельца.
# Чужой поток по-прежнему не вытесняет ничьи состояния, но владелец входит даже
# при полностью занятом пуле.
TRUSTED_HEADROOM = 8


class LoginStates:
    def __init__(self, capacity, *, per_client_per_minute=PER_CLIENT_PER_MINUTE):
        self.capacity = capacity
        self.per_client_per_minute = per_client_per_minute
        self.entries = {}
        self._recent = {}

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def _allow_rate(self, owner, now):
        """Лимит на источник: без него один поток занимает весь пул."""
        attempts = [stamp for stamp in self._recent.get(owner, []) if stamp > now - 60]
        if len(attempts) >= self.per_client_per_minute:
            self._recent[owner] = attempts
            return False
        attempts.append(now)
        self._recent[owner] = attempts
        return True

    def new(self, client='', existing=None, *, trusted=False):
        now = time.monotonic()
        self.entries = {key: row for key, row in self.entries.items() if row[1] > now}
        self._recent = {
            owner: [stamp for stamp in stamps if stamp > now - 60]
            for owner, stamps in self._recent.items()
        }
        owner = self.digest(client)
        if existing:
            row = self.entries.get(self.digest(existing))
            if row is not None and row[0] == owner:
                return existing
        if sum(row[0] == owner for row in self.entries.values()) >= PER_CLIENT_ACTIVE:
            return None
        if len(self.entries) >= self.capacity and not (
            trusted and len(self.entries) < self.capacity + TRUSTED_HEADROOM
        ):
            return None
        if not self._allow_rate(owner, now):
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
