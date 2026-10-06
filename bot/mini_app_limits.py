"""Ограничители частоты для дорогих маршрутов мини-аппа.

Один пользователь не должен занимать общий ресурс, а память не должна расти
бесконечно: окно на пользователя, необязательный общий предохранитель и
ограничение числа отслеживаемых пользователей.
"""

import time
from collections import deque


class RequestBudget:
    """Скользящее окно запросов: на пользователя и, при желании, общее.

    ``per_user`` — сколько запросов разрешено одному человеку за окно.
    ``global_limit`` — общий предохранитель на всех (None — без ограничения).
    ``max_users`` — сколько пользователей держим в памяти одновременно.
    """

    def __init__(
        self,
        *,
        per_user: int,
        window_seconds: float,
        global_limit: int | None = None,
        max_users: int = 4096,
    ) -> None:
        if type(per_user) is not int or per_user < 1:
            raise ValueError("invalid per-user limit")
        if not isinstance(window_seconds, (int, float)) or window_seconds <= 0:
            raise ValueError("invalid window")
        if global_limit is not None and (type(global_limit) is not int or global_limit < 1):
            raise ValueError("invalid global limit")
        if type(max_users) is not int or max_users < 1:
            raise ValueError("invalid user cap")
        self._per_user = per_user
        self._window = float(window_seconds)
        self._global_limit = global_limit
        self._max_users = max_users
        self._entries: dict[int, deque[float]] = {}
        self._global: deque[float] = deque()

    def _prune(self, now: float) -> None:
        border = now - self._window
        for user_id in list(self._entries):
            stamps = self._entries[user_id]
            while stamps and stamps[0] <= border:
                stamps.popleft()
            if not stamps:
                del self._entries[user_id]
        while self._global and self._global[0] <= border:
            self._global.popleft()

    def admit(self, user_id: int, *, now: float | None = None) -> bool:
        """Разрешить запрос: True — можно, False — слишком часто."""
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid user")
        at = time.monotonic() if now is None else now
        self._prune(at)
        stamps = self._entries.get(user_id)
        if stamps is None:
            if len(self._entries) >= self._max_users:
                # Освобождаем место самому «старому» пользователю, а не всем.
                oldest = min(self._entries, key=lambda key: self._entries[key][-1])
                del self._entries[oldest]
            stamps = deque()
            self._entries[user_id] = stamps
        if len(stamps) >= self._per_user:
            return False
        if self._global_limit is not None and len(self._global) >= self._global_limit:
            return False
        stamps.append(at)
        if self._global_limit is not None:
            self._global.append(at)
        return True

    def tracked_users(self) -> int:
        return len(self._entries)
