from __future__ import annotations

import logging
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from .logging_utils import mask_chat_id


logger = logging.getLogger(__name__)


class LivePostUpdateResult(Enum):
    UPDATED = "updated"
    RETRY_LATER = "retry_later"
    REPLACE_REQUIRED = "replace_required"
    KEEP_EXISTING = "keep_existing"


class LivePostUpdater:
    """Редактирует существующий Telegram live-post без управления его lifecycle."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def update(
        self,
        *,
        chat_id: int,
        message_id: int,
        message_kind: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> LivePostUpdateResult:
        if message_kind != "text":
            raise ValueError(f"Unsupported message kind: {message_kind!r}")

        try:
            await self._bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return LivePostUpdateResult.UPDATED
        except TelegramRetryAfter as e:
            logger.info(
                "Лимит Telegram при обновлении поста в %s, повтор через %sс",
                mask_chat_id(chat_id),
                e.retry_after,
            )
            return LivePostUpdateResult.RETRY_LATER
        except TelegramNetworkError as e:
            logger.warning(
                "Сеть недоступна при обновлении поста в %s: %s",
                mask_chat_id(chat_id),
                e,
            )
            return LivePostUpdateResult.RETRY_LATER
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return LivePostUpdateResult.UPDATED
            if "there is no text in the message to edit" in str(e):
                return LivePostUpdateResult.REPLACE_REQUIRED
            if "message to edit not found" in str(e):
                return LivePostUpdateResult.REPLACE_REQUIRED
            logger.warning(
                "Не удалось отредактировать сообщение %s в %s: %s",
                message_id,
                mask_chat_id(chat_id),
                e,
            )
            return LivePostUpdateResult.KEEP_EXISTING
        except TelegramForbiddenError as e:
            logger.warning(
                "Не удалось отредактировать сообщение %s в %s: %s",
                message_id,
                mask_chat_id(chat_id),
                e,
            )
            return LivePostUpdateResult.KEEP_EXISTING
