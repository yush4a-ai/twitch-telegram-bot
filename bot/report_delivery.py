from __future__ import annotations

import logging

from .database import Database
from .logging_utils import mask_chat_id

logger = logging.getLogger(__name__)


async def validate_report_destination(
    db: Database,
    recipient_chat_id: int,
    *,
    source_chat_id: int | None,
    telegram_channel_login: str | None = None,
    operation: str,
) -> bool:
    """Последний guard перед отправкой итогового report payload в Telegram.

    Положительный ID — личный чат. Отрицательный ID разрешён только явно отмеченному
    flow зарегистрированного Telegram-канала, когда destination совпадает с source,
    настройка конкретного Twitch-канала включена и текущий routing всё ещё выбирает
    этот destination. Обычная группа и устаревший frozen outbox блокируются.
    """
    if recipient_chat_id > 0:
        return True

    is_enabled_channel_flow = (
        telegram_channel_login is not None
        and source_chat_id is not None
        and recipient_chat_id == source_chat_id
        and await db.is_telegram_channel(recipient_chat_id)
        and await db.get_channel_report_enabled(
            source_chat_id, telegram_channel_login
        )
        and await db.resolve_post_recipient(
            source_chat_id, telegram_channel_login
        ) == recipient_chat_id
    )
    if is_enabled_channel_flow:
        return True

    logger.warning(
        "%s заблокирована final report guard: destination=%s, source=%s, "
        "telegram_channel_login=%s",
        operation,
        mask_chat_id(recipient_chat_id),
        mask_chat_id(source_chat_id) if source_chat_id is not None else "none",
        telegram_channel_login or "none",
    )
    return False
