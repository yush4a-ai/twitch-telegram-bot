from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InaccessibleMessage, TelegramObject

logger = logging.getLogger(__name__)

# минимальный интервал между любыми двумя действиями одного пользователя
DEFAULT_INTERVAL_SECONDS = 0.5

# отдельные лимиты для операций, которые стоят дорого: перебор чатов через
# Telegram API, сборка HTML-отчёта, полная пагинация подписок на Twitch.
# Без них один человек, удерживающий кнопку, выбирает общий лимит Telegram
# (30 запросов в секунду на бота) и подвешивает бота для всех остальных
HEAVY_ACTION_INTERVAL_SECONDS = 8.0
HEAVY_ACTIONS = (
    "menu:manage_group",
    "menu:import_follows",
    "importfollows:add",
    "menu:report",
    "report:",
    "menu:live",
)
HEAVY_COMMANDS = ("/report", "/import_follows", "/live", "/auth_twitch")

# как часто выбрасывать записи о давно неактивных пользователях, чтобы словарь
# не рос бесконечно на большом числе пользователей
_CLEANUP_EVERY_SECONDS = 300
_ENTRY_TTL_SECONDS = 600


class ThrottleMiddleware(BaseMiddleware):
    """Ограничивает частоту действий на пользователя. Лишние апдейты отбрасываются,
    а не ставятся в очередь — иначе накопленный хвост всё равно упрётся в лимиты
    Telegram, просто с задержкой."""

    def __init__(self) -> None:
        self._last_action: dict[int, float] = {}
        self._last_heavy: dict[int, float] = {}
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < _CLEANUP_EVERY_SECONDS:
            return
        self._last_cleanup = now
        cutoff = now - _ENTRY_TTL_SECONDS
        for storage in (self._last_action, self._last_heavy):
            for user_id in [uid for uid, ts in storage.items() if ts < cutoff]:
                del storage[user_id]

    @staticmethod
    def _is_heavy(event: TelegramObject) -> bool:
        if isinstance(event, CallbackQuery):
            data = event.data or ""
            return any(data.startswith(prefix) for prefix in HEAVY_ACTIONS)
        text = getattr(event, "text", None) or ""
        return any(text.startswith(cmd) for cmd in HEAVY_COMMANDS)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup(now)

        heavy = self._is_heavy(event)
        interval = HEAVY_ACTION_INTERVAL_SECONDS if heavy else DEFAULT_INTERVAL_SECONDS
        storage = self._last_heavy if heavy else self._last_action

        last = storage.get(user.id)
        if last is not None and now - last < interval:
            if isinstance(event, CallbackQuery):
                # без ответа на callback у пользователя вечно крутится «часики»
                try:
                    await event.answer("Слишком часто — подожди пару секунд.")
                except Exception:
                    pass
            logger.debug("Действие пользователя отброшено троттлингом (heavy=%s)", heavy)
            return None

        storage[user.id] = now
        return await handler(event, data)


class CallbackGuardMiddleware(BaseMiddleware):
    """Отсекает callback-запросы, у которых недоступно сообщение с кнопкой.

    Telegram не отдаёт тело сообщений старше 48 часов: в aiogram это приходит как
    InaccessibleMessage (или None). Обработчики читают из него chat_id и редактируют
    текст, поэтому без такой проверки старая кнопка роняла бы их с AttributeError."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            message = event.message
            if message is None or isinstance(message, InaccessibleMessage):
                try:
                    await event.answer(
                        "Это сообщение слишком старое. Открой меню заново: /start",
                        show_alert=True,
                    )
                except Exception:
                    pass
                return None
        return await handler(event, data)


class ErrorGuardMiddleware(BaseMiddleware):
    """Последний рубеж: не даёт исключению из обработчика всплыть в поллинг-цикл.

    aiogram и сам не роняет процесс на ошибке обработчика, но пользователь при этом
    остаётся с зависшим интерфейсом и без единого намёка на то, что пошло не так."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("Необработанная ошибка в обработчике %s", type(event).__name__)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Что-то пошло не так. Попробуй ещё раз.", show_alert=True)
                except Exception:
                    pass
            return None


def setup_middlewares(dp) -> None:
    """Порядок важен: сначала отбраковка мусорных апдейтов, затем троттлинг,
    и только потом — перехват ошибок вокруг самого обработчика."""
    for observer in (dp.message, dp.callback_query):
        observer.middleware(ErrorGuardMiddleware())

    dp.callback_query.middleware(CallbackGuardMiddleware())

    throttle = ThrottleMiddleware()
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)
