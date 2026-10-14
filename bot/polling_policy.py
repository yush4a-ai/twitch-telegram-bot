"""Политика перезапуска приёма апдейтов: один сбой не должен ронять бота навсегда."""

from __future__ import annotations

from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError

from .config import ConfigError
from .database import DatabaseConfigurationError


# Бот лежал в цикле падений, пока апдейт не переставал приходить. Приём
# перезапускаем сами, но не бесконечно: постоянные ошибки доступа и исчерпанный
# лимит пробрасываем наружу, чтобы сработал перезапуск платформы.
POLLING_RESTART_LIMIT = 5
POLLING_RESTART_DELAY_SECONDS = 5


def should_restart_polling(error: BaseException, attempts: int) -> bool:
    """Повторять ли приём апдейтов после падения задачи polling."""
    if attempts > POLLING_RESTART_LIMIT:
        return False
    return not isinstance(error, (
        ConfigError,
        DatabaseConfigurationError,
        TokenValidationError,
        TelegramUnauthorizedError,
    ))
