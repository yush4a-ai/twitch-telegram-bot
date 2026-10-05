"""Оповещения владельцу в Telegram о переходах подсистем в плохое состояние.

Панель не должна требовать постоянных заходов, но и превращаться в спам-ленту
она не должна: сообщение уходит при смене состояния подсистемы и не чаще одного
раза в час на подсистему. В тексте нет ни служебных подробностей, ни секретов —
только что случилось и куда посмотреть.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 3600.0

# Подсистема -> (заголовок в сообщении, совет)
SUBSYSTEM_TITLES = {
    "twitch": "Twitch",
    "queue": "Очередь уведомлений",
    "database": "База данных",
    "backup": "Резервная копия",
}

BAD_TEXT = {
    "twitch": "каналы требуют авторизации: уведомления о стримах могут не уходить.",
    "queue": "есть неудачные задания: часть уведомлений не доставлена.",
    "database": "отвечает с ошибками: часть данных может не сохраняться.",
    "backup": "свежая копия не создана: откат может потерять данные.",
}

GOOD_TEXT = {
    "twitch": "снова работает: авторизация в порядке.",
    "queue": "снова в порядке: неудачных заданий нет.",
    "database": "снова отвечает нормально.",
    "backup": "снова создаётся.",
}


def subsystem_states(snapshot: dict) -> dict[str, str]:
    """Сводит снимок панели к состояниям «в порядке» и «плохо».

    Неизвестное состояние плохим не считается: без данных тревога была бы ложной.
    """
    states: dict[str, str] = {}
    twitch = snapshot.get("twitch") or {}
    if twitch.get("state") in ("ok", "degraded"):
        states["twitch"] = "ok" if twitch.get("state") == "ok" else "bad"
    errors = snapshot.get("errors") or {}
    queues = snapshot.get("queues") or {}
    if queues:
        failed = queues.get("failed_jobs") or 0
        states["queue"] = "bad" if failed else "ok"
    if "database" in errors:
        states["database"] = "bad" if errors.get("database") else "ok"
    backup = snapshot.get("backup")
    if backup is not None:
        states["backup"] = "ok" if backup.get("last_backup_at") else "bad"
    return states


class OwnerAlerter:
    def __init__(
        self,
        send: Callable[[str], Awaitable[None]] | None,
        *,
        cooldown: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._send = send
        self._cooldown = cooldown
        self._clock = clock
        self._previous: dict[str, str] = {}
        self._sent_at: dict[str, float] = {}

    async def inspect(self, snapshot: dict) -> list[str]:
        """Отправляет сообщения о переходах. Возвращает список отправленных."""
        if self._send is None:
            return []
        now = self._clock()
        delivered: list[str] = []
        for subsystem, state in subsystem_states(snapshot).items():
            previous = self._previous.get(subsystem)
            self._previous[subsystem] = state
            if previous == state:
                continue
            if state == "bad":
                # Повтор о незакрытом сбое — отдельным методом, не чаще раза в час.
                last = self._sent_at.get(subsystem, 0.0)
                if previous is not None and now - last < self._cooldown:
                    continue
                text = f"{SUBSYSTEM_TITLES[subsystem]}: {BAD_TEXT[subsystem]}"
            elif previous == "bad":
                text = f"{SUBSYSTEM_TITLES[subsystem]}: {GOOD_TEXT[subsystem]}"
            else:
                continue
            try:
                await self._send(text)
            except Exception:
                logger.exception("Не удалось отправить оповещение владельцу")
                continue
            self._sent_at[subsystem] = now
            delivered.append(text)
        return delivered

    async def repeat_if_still_bad(self, snapshot: dict) -> list[str]:
        """Напоминание о незакрытом сбое: то же состояние, но час уже прошёл."""
        if self._send is None:
            return []
        now = self._clock()
        delivered: list[str] = []
        for subsystem, state in subsystem_states(snapshot).items():
            if state != "bad" or self._previous.get(subsystem) != "bad":
                continue
            if now - self._sent_at.get(subsystem, 0.0) < self._cooldown:
                continue
            text = f"{SUBSYSTEM_TITLES[subsystem]}: всё ещё {BAD_TEXT[subsystem]}"
            try:
                await self._send(text)
            except Exception:
                logger.exception("Не удалось повторить оповещение владельцу")
                continue
            self._sent_at[subsystem] = now
            delivered.append(text)
        return delivered
