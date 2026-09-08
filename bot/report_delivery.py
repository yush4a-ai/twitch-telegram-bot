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
    allow_telegram_channel: bool,
    operation: str,
) -> bool:
    """Последний guard перед отправкой итогового report payload в Telegram.

    Положительный ID — личный чат. Отрицательный ID разрешён только специальному
    автоматическому flow зарегистрированного Telegram-канала и только когда source
    и destination совпадают. Обычная группа блокируется независимо от upstream
    routing, legacy-значений и содержимого deferred queue.
    """
    if recipient_chat_id > 0:
        return True

    is_special_channel = (
        allow_telegram_channel
        and source_chat_id is not None
        and recipient_chat_id == source_chat_id
        and await db.is_telegram_channel(recipient_chat_id)
    )
    if is_special_channel:
        return True

    logger.warning(
        "%s заблокирована final report guard: destination=%s, source=%s, "
        "special_channel_flow=%s",
        operation,
        mask_chat_id(recipient_chat_id),
        mask_chat_id(source_chat_id) if source_chat_id is not None else "none",
        allow_telegram_channel,
    )
    return False
