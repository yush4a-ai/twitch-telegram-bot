"""Срок хранения переписки: старое удаляется вместе с файлами.

Срок задаётся настройкой, а не интерфейсом: владелец не должен случайно
продлить хранение чужих сообщений одним нажатием. Файлы картинок удаляются
только внутри каталога медиа.
"""
from __future__ import annotations

import asyncio
import logging
import math
import pathlib
import time

from .media_store import remove_image

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 180.0
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60


def _safe_days(days: float) -> float:
    """Отрицательный срок увёл бы границу в будущее и стёр всю переписку."""
    if not isinstance(days, (int, float)) or not math.isfinite(days) or days < 1:
        logger.warning("Некорректный срок хранения переписки, беру значение по умолчанию")
        return DEFAULT_RETENTION_DAYS
    return float(days)


async def purge_once(
    db, media_dir: str | pathlib.Path, *, days: float = DEFAULT_RETENTION_DAYS,
    now: float | None = None,
) -> dict[str, int]:
    """Одна очистка. Возвращает, сколько сообщений и файлов удалено."""
    moment = time.time() if now is None else now
    cutoff = moment - _safe_days(days) * 86400
    images = await db.old_dialogue_images(older_than=cutoff)
    removed = await db.purge_dialogue_messages(older_than=cutoff)
    files = 0
    directory = pathlib.Path(media_dir)
    for path in images:
        if remove_image(path, directory=directory):
            files += 1
    return {"messages": removed, "files": files}


async def run_retention_loop(
    db, media_dir: str | pathlib.Path, *,
    days: float = DEFAULT_RETENTION_DAYS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Периодическая очистка: раз в сутки достаточно и не грузит базу."""
    while True:
        try:
            result = await purge_once(db, media_dir, days=days)
            if result["messages"]:
                logger.info(
                    "Срок хранения переписки: удалено сообщений %s, файлов %s",
                    result["messages"], result["files"],
                )
        except Exception:
            logger.exception("Не удалось почистить старую переписку")
        await asyncio.sleep(interval)
