"""Перезапуск приёма апдейтов: политика повторных попыток."""

import unittest

from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError

from bot.config import ConfigError
from bot.polling_policy import (
    POLLING_RESTART_LIMIT,
    should_restart_polling,
)


class PollingRestartPolicyTests(unittest.TestCase):
    def test_ordinary_failure_is_retried_within_the_limit(self):
        """Странный апдейт или сетевой сбой: приём перезапускаем сами."""
        self.assertTrue(should_restart_polling(RuntimeError("boom"), attempts=1))
        self.assertTrue(should_restart_polling(
            ValueError("unserializable"), attempts=POLLING_RESTART_LIMIT))

    def test_limit_is_finite(self):
        self.assertFalse(should_restart_polling(
            RuntimeError("boom"), attempts=POLLING_RESTART_LIMIT + 1))

    def test_permanent_failures_are_not_retried(self):
        for error in (ConfigError("bad config"), TokenValidationError("bad token"),
                      TelegramUnauthorizedError(method=None, message="unauthorized")):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(should_restart_polling(error, attempts=1))
