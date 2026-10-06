"""Потолки места для временных файлов захвата превью.

Раньше потолок был зашит числом (384 МБ на все захваты), поэтому больше двух
каналов с превью не запускалось даже при свободном томе: система закрывала все
захваты сразу. Теперь значения берутся из переменных окружения, а при мусоре
или отсутствии переменной используется прежнее безопасное значение.
"""
import os
import unittest
from unittest.mock import patch

from bot.preview_capture.buffer import (
    FREE_DISK_RESERVE_BYTES,
    ROOT_HARD_MAX_BYTES,
    _env_bytes,
)

DEFAULT_ROOT = 384 * 1024 * 1024
DEFAULT_RESERVE = 256 * 1024 * 1024


class EnvBytesTests(unittest.TestCase):
    def test_missing_setting_keeps_the_safe_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PREVIEW_TEST_LIMIT", None)
            self.assertEqual(_env_bytes("PREVIEW_TEST_LIMIT", DEFAULT_ROOT), DEFAULT_ROOT)

    def test_setting_raises_the_ceiling(self):
        with patch.dict(os.environ, {"PREVIEW_TEST_LIMIT": str(2 * 1024 ** 3)}):
            self.assertEqual(_env_bytes("PREVIEW_TEST_LIMIT", DEFAULT_ROOT), 2 * 1024 ** 3)

    def test_broken_or_dangerous_values_fall_back(self):
        for raw in ("много", "10", str(1024 ** 4), "-5", ""):
            with self.subTest(value=raw):
                with patch.dict(os.environ, {"PREVIEW_TEST_LIMIT": raw}):
                    self.assertEqual(
                        _env_bytes("PREVIEW_TEST_LIMIT", DEFAULT_ROOT), DEFAULT_ROOT)

    def test_module_limits_follow_their_settings(self):
        self.assertEqual(
            ROOT_HARD_MAX_BYTES,
            _env_bytes("PREVIEW_ROOT_MAX_BYTES", DEFAULT_ROOT),
        )
        self.assertEqual(
            FREE_DISK_RESERVE_BYTES,
            _env_bytes("PREVIEW_FREE_DISK_RESERVE_BYTES", DEFAULT_RESERVE),
        )


if __name__ == "__main__":
    unittest.main()
