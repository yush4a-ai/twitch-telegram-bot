"""Regression tests for the 2026-10-04 production audit findings.

Finding 1: follower lookups for channels without a user token raised
``TwitchAuthError`` and logged a full traceback every poll cycle, which filled
the production journal with 111 tracebacks in four minutes.

Finding 2: posts into chats that blocked the bot (or whose owner deactivated
the account) kept retrying on every cycle and logged a warning each time.

Finding 3: the production volume carried no fresh database backup.

The fixes must stay quiet: no user-visible message and no channel post may be
produced by any of them.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.config import load_config
from bot.poller import StreamPoller
from bot.twitch import TwitchAuthError


def _poller(db, token_store=None):
    return StreamPoller(
        SimpleNamespace(), db, SimpleNamespace(), 60, token_store=token_store
    )


class QuietFollowerLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_user_token_is_not_logged_as_exception(self):
        db = SimpleNamespace(set_followers_at_start=AsyncMock())
        token_store = SimpleNamespace(
            execute_with_token=AsyncMock(
                side_effect=TwitchAuthError(
                    "Для Twitch-канала channel требуется повторная авторизация"
                )
            )
        )
        poller = _poller(db, token_store)
        cache: dict[str, int | None] = {}

        with patch("bot.poller.logger") as logger:
            await poller._maybe_snapshot_followers(1, "channel", None, cache)

        logger.exception.assert_not_called()
        db.set_followers_at_start.assert_not_awaited()
        self.assertIsNone(cache["channel"])

    async def test_final_count_without_user_token_is_not_logged_as_exception(self):
        db = SimpleNamespace(get_follow_event_count=AsyncMock(return_value=None))
        token_store = SimpleNamespace(
            execute_with_token=AsyncMock(side_effect=TwitchAuthError("нет токена"))
        )
        poller = _poller(db, token_store)

        with patch("bot.poller.logger") as logger:
            result = await poller._compute_new_followers(1, "channel", "stream", 10)

        self.assertIsNone(result)
        logger.exception.assert_not_called()

    async def test_unexpected_follower_error_is_still_reported(self):
        db = SimpleNamespace(set_followers_at_start=AsyncMock())
        token_store = SimpleNamespace(
            execute_with_token=AsyncMock(side_effect=RuntimeError("unexpected"))
        )
        poller = _poller(db, token_store)

        with patch("bot.poller.logger") as logger:
            await poller._maybe_snapshot_followers(1, "channel", None, {})

        logger.exception.assert_called_once()


class UnreachableChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_chat_disables_notifications_without_sending(self):
        db = SimpleNamespace(disable_notifications_for_chat=AsyncMock(return_value=2))
        poller = _poller(db)

        async def broken():
            raise TelegramForbiddenError(
                SimpleNamespace(), "Forbidden: bot was blocked by the user"
            )

        with patch("bot.poller.logger") as logger:
            result = await poller._tg_call(
                broken, "Отправка поста о старте стрима", unavailable_chat_id=42
            )

        db.disable_notifications_for_chat.assert_awaited_once_with(42)
        logger.warning.assert_called_once()
        self.assertIsNotNone(result)

    async def test_deactivated_user_disables_notifications(self):
        db = SimpleNamespace(disable_notifications_for_chat=AsyncMock(return_value=1))
        poller = _poller(db)

        async def broken():
            raise TelegramForbiddenError(
                SimpleNamespace(), "Forbidden: user is deactivated"
            )

        with patch("bot.poller.logger"):
            await poller._tg_call(
                broken, "Отправка поста о старте стрима", unavailable_chat_id=7
            )

        db.disable_notifications_for_chat.assert_awaited_once_with(7)

    async def test_permission_error_keeps_subscription(self):
        """Missing posting rights must not silently disable the subscription."""
        db = SimpleNamespace(disable_notifications_for_chat=AsyncMock(return_value=0))
        poller = _poller(db)

        async def broken():
            raise TelegramBadRequest(
                SimpleNamespace(), "Bad Request: CHAT_WRITE_FORBIDDEN"
            )

        with patch("bot.poller.logger"):
            await poller._tg_call(
                broken, "Отправка поста о старте стрима", unavailable_chat_id=9
            )

        db.disable_notifications_for_chat.assert_not_awaited()

    async def test_chat_id_is_not_required(self):
        db = SimpleNamespace(disable_notifications_for_chat=AsyncMock(return_value=0))
        poller = _poller(db)

        async def broken():
            raise TelegramForbiddenError(
                SimpleNamespace(), "Forbidden: bot was blocked by the user"
            )

        with patch("bot.poller.logger"):
            await poller._tg_call(broken, "Другое действие")

        db.disable_notifications_for_chat.assert_not_awaited()


class BackupConfigTests(unittest.TestCase):
    base_env = {
        "TELEGRAM_BOT_TOKEN": "unit-test-token-never-used",
        "TWITCH_CLIENT_ID": "client",
        "TWITCH_CLIENT_SECRET": "secret",
    }

    def _config(self, **extra):
        with patch.dict(os.environ, {**self.base_env, **extra}, clear=True):
            return load_config()

    def test_backup_defaults_are_one_copy_per_day_and_five_kept(self):
        config = self._config()
        self.assertEqual(config.backup_interval_seconds, 24 * 60 * 60)
        self.assertEqual(config.backup_retention, 5)

    def test_backup_settings_are_clamped_to_safe_bounds(self):
        too_often = self._config(BACKUP_INTERVAL_SECONDS="30", BACKUP_RETENTION="99")
        self.assertEqual(too_often.backup_interval_seconds, 300)
        self.assertEqual(too_often.backup_retention, 20)

    def test_backup_interval_must_be_a_number(self):
        from bot.config import ConfigError

        with self.assertRaises(ConfigError):
            self._config(BACKUP_INTERVAL_SECONDS="often")


if __name__ == "__main__":
    unittest.main()
