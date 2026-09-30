"""Independent bounded worker for durable notification jobs."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from enum import Enum

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from .notification_queue import NotificationJob, NotificationQueue


logger = logging.getLogger(__name__)


class NotificationOutcome(Enum):
    SENT = "sent"
    STALE = "stale"


class NotificationRetryAfter(Exception):
    def __init__(self, retry_after: float):
        super().__init__("notification rate limited")
        self.retry_after = retry_after


class NotificationTerminalError(Exception):
    pass


class NotificationWorker:
    def __init__(
        self,
        queue: NotificationQueue,
        send_job: Callable[[NotificationJob], Awaitable[NotificationOutcome]],
        *,
        max_concurrency: int,
        per_chat_interval: float,
        lease_seconds: float = 180.0,
        send_timeout: float = 30.0,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        idle_interval: float = 1.0,
    ) -> None:
        if not 1 <= max_concurrency <= 16:
            raise ValueError("max_concurrency must be 1..16")
        if (
            not math.isfinite(per_chat_interval) or per_chat_interval < 0
            or not math.isfinite(send_timeout) or send_timeout <= 0
            or not math.isfinite(lease_seconds)
            or lease_seconds <= max_concurrency * send_timeout
            + per_chat_interval * (max_concurrency - 1)
            or not math.isfinite(idle_interval) or idle_interval <= 0
        ):
            raise ValueError("invalid worker timing bounds")
        self._queue = queue
        self._send_job = send_job
        self._max_concurrency = max_concurrency
        self._per_chat_interval = per_chat_interval
        self._lease_seconds = lease_seconds
        self._send_timeout = send_timeout
        self._clock = clock
        self._monotonic = monotonic_clock
        self._idle_interval = idle_interval
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._chat_next_start: dict[int, float] = {}

    @property
    def tracked_chat_count(self) -> int:
        return len(self._chat_next_start)

    def _prune_chat_timing(self) -> None:
        cutoff = self._monotonic() - max(60.0, self._per_chat_interval * 2)
        for chat_id, next_start in list(self._chat_next_start.items()):
            lock = self._chat_locks.get(chat_id)
            if next_start < cutoff and (lock is None or not lock.locked()):
                self._chat_next_start.pop(chat_id, None)
                self._chat_locks.pop(chat_id, None)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="notification-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("Notification worker cycle failed: %s", type(error).__name__)
                processed = 0
            if processed == self._max_concurrency:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._idle_interval)
            except TimeoutError:
                pass

    async def run_once(self) -> int:
        jobs = await self._queue.claim_due(
            self._clock(), limit=self._max_concurrency,
            lease_seconds=self._lease_seconds,
        )
        if not jobs:
            self._prune_chat_timing()
            return 0
        results = await asyncio.gather(
            *(self._process(job) for job in jobs), return_exceptions=True
        )
        self._prune_chat_timing()
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return len(jobs)

    async def _process(self, job: NotificationJob) -> None:
        lock = self._chat_locks.setdefault(job.chat_id, asyncio.Lock())
        async with lock:
            start_at = self._monotonic()
            due_at = self._chat_next_start.get(job.chat_id, start_at)
            if due_at > start_at:
                await asyncio.sleep(due_at - start_at)
            self._chat_next_start[job.chat_id] = self._monotonic() + self._per_chat_interval
            try:
                outcome = await asyncio.wait_for(self._send_job(job), self._send_timeout)
            except (NotificationRetryAfter, TelegramRetryAfter) as error:
                retry_after = float(error.retry_after)
                if not math.isfinite(retry_after) or retry_after <= 0:
                    retry_after = 1.0
                now = self._clock()
                await self._queue.defer(
                    job.id, job.attempt_count, due_at=now + retry_after,
                    error_class=type(error).__name__, now=now,
                )
                return
            except (NotificationTerminalError, TelegramForbiddenError, TelegramBadRequest) as error:
                await self._queue.fail(
                    job.id, job.attempt_count, error_class=type(error).__name__,
                    now=self._clock(),
                )
                return
            except Exception as error:
                now = self._clock()
                backoff = min(120.0, float(2 ** min(job.attempt_count, 6)))
                await self._queue.defer(
                    job.id, job.attempt_count, due_at=now + backoff,
                    error_class=type(error).__name__, now=now,
                )
                return
            if outcome not in (NotificationOutcome.SENT, NotificationOutcome.STALE):
                raise ValueError("send_job returned an unknown outcome")
            # Отправка уже могла пройти; сбой здесь оставляет lease для повторного
            # claim. Внешний Telegram send поэтому имеет at-least-once семантику.
            await self._queue.ack(job.id, job.attempt_count, now=self._clock())
