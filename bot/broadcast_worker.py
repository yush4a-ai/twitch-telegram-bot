"""Постепенная отправка рассылки владельца.

Работает поверх своих таблиц и того же ограничителя скорости, что и уведомления
о стримах: отдельного приоритетного канала у рассылки нет, поэтому обычные
уведомления не голодают.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Awaitable, Callable

from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 25


class BroadcastWorker:
    def __init__(
        self,
        db,
        send_message: Callable[[int, dict], Awaitable[None]],
        *,
        send_budget=None,
        batch_size: int = BATCH_SIZE,
        idle_interval: float = 2.0,
        error_backoff: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 200:
            raise ValueError("invalid batch size")
        if not math.isfinite(idle_interval) or idle_interval <= 0:
            raise ValueError("invalid idle interval")
        if not math.isfinite(error_backoff) or error_backoff <= 0:
            raise ValueError("invalid error backoff")
        self._db = db
        self._send_message = send_message
        self._send_budget = send_budget
        self._batch_size = batch_size
        self._idle_interval = idle_interval
        self._error_backoff = error_backoff
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Итоги, которые не удалось записать после отправки: сообщение уже ушло,
        # поэтому повторять отправку нельзя — дописываем только запись.
        self._unmarked: dict[tuple[int, int], str] = {}

    async def _mark(
        self, campaign_id: int, user_id: int, state: str, *, now: float,
        error_code: str | None = None,
    ) -> None:
        try:
            await self._db.mark_broadcast_recipient(
                campaign_id, user_id, state, now=now, error_code=error_code)
        except Exception:
            logger.exception("Не удалось записать итог отправки")
            self._unmarked[(int(campaign_id), int(user_id))] = state
            return

    async def _flush_unmarked(self) -> None:
        """Дописывает итоги, потерянные из-за сбоя базы, не отправляя заново."""
        for (campaign_id, user_id), state in list(self._unmarked.items()):
            try:
                await self._db.mark_broadcast_recipient(
                    campaign_id, user_id, state, now=self._clock())
            except Exception:
                continue
            self._unmarked.pop((campaign_id, user_id), None)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="broadcast-worker")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Сбой отправителя рассылок")
                # Без паузы сбой БД превратился бы в попытки отправки каждые 2 секунды.
                await asyncio.sleep(self._error_backoff)
                continue
            if processed:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._idle_interval)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        """Одна пачка по каждой незавершённой кампании. Возвращает число отправок."""
        await self._flush_unmarked()
        processed = 0
        for campaign_id in await self._db.resume_broadcast_campaigns():
            campaign = await self._db.get_broadcast_campaign(campaign_id)
            # Владелец мог остановить кампанию между пачками.
            if campaign is None or campaign["state"] != "sending":
                continue
            recipients = await self._db.next_broadcast_recipients(
                campaign_id, limit=self._batch_size)
            if not recipients:
                await self._db.set_broadcast_state(campaign_id, "sent", now=self._clock())
                continue
            for user_id in recipients:
                if await self._deliver(campaign, user_id):
                    processed += 1
            # Кампания закрывается в том же проходе, иначе она «отправляется» до
            # следующего захода, хотя очередь уже пуста.
            remaining = await self._db.next_broadcast_recipients(campaign_id, limit=1)
            fresh = await self._db.get_broadcast_campaign(campaign_id)
            if remaining:
                continue
            if fresh is not None and fresh["state"] == "sending":
                await self._db.set_broadcast_state(campaign_id, "sent", now=self._clock())
                # Кампания закончилась — аренды больше не нужны.
                await self._db.clear_broadcast_leases(campaign_id)
        return processed

    async def _deliver(self, campaign: dict, user_id: int) -> bool:
        """Отправляет одному человеку. False — если отправку сделал кто-то другой."""
        now = self._clock()
        # Отписка могла случиться, пока пачка была в полёте: пачка идёт минутами.
        if await self._db.is_broadcast_opted_out(user_id):
            await self._mark(campaign["id"], user_id, "opted_out", now=now)
            return False
        # Аренда: при выкладке старая и новая копия сервиса работают одновременно
        # и без неё человек получил бы два одинаковых сообщения.
        if not await self._db.try_claim_broadcast_recipient(
                campaign["id"], user_id, now=now):
            return False
        if self._send_budget is not None:
            await self._send_budget.wait_turn(normal=True)
        try:
            await self._send_message(user_id, campaign)
        except TelegramForbiddenError:
            # Человек заблокировал бота: это недоступность, а не сбой сервера.
            await self._mark(
                campaign["id"], user_id, "unreachable", now=now, error_code="forbidden")
            return True
        except TelegramRetryAfter as error:
            # Telegram просит подождать: получатель остаётся в очереди,
            # а аренда истечёт сама и позволит вернуться к нему позже.
            await asyncio.sleep(float(getattr(error, "retry_after", 1.0) or 1.0))
            return False
        except (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError) as error:
            # Временный сбой связи: человека не записываем в «ошибки» навсегда,
            # он останется в очереди и получит сообщение со следующего захода.
            logger.warning("Временная ошибка отправки: %s", type(error).__name__)
            return False
        except Exception as error:
            await self._mark(
                campaign["id"], user_id, "failed", now=now,
                error_code=type(error).__name__)
            return True
        await self._mark(campaign["id"], user_id, "sent", now=now)
        return True
