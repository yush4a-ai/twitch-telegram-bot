"""Process-local Telegram request start budget shared by signals and previews."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable


class TelegramSendBudget:
    def __init__(
        self, *, global_interval: float = 0.04,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(global_interval) or global_interval < 0:
            raise ValueError("invalid Telegram interval")
        self._interval = global_interval
        self._clock = clock
        self._condition = asyncio.Condition()
        self._next_start = 0.0
        self._preview_not_before = 0.0
        self._normal_waiters = 0

    async def wait_turn(self, *, normal: bool) -> None:
        async with self._condition:
            if normal:
                self._normal_waiters += 1
                self._condition.notify_all()
            try:
                while True:
                    if not normal and self._normal_waiters:
                        await self._condition.wait()
                        continue
                    due = max(
                        self._next_start,
                        0.0 if normal else self._preview_not_before,
                    )
                    remaining = due - self._clock()
                    if remaining <= 0:
                        self._next_start = self._clock() + self._interval
                        self._condition.notify_all()
                        return
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                    except TimeoutError:
                        pass
            finally:
                if normal:
                    self._normal_waiters -= 1
                    self._condition.notify_all()

    def defer_preview(self, seconds: float) -> None:
        if math.isfinite(seconds) and seconds > 0:
            self._preview_not_before = max(
                self._preview_not_before, self._clock() + seconds,
            )
