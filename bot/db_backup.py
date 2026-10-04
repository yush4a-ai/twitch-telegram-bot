"""Периодические онлайн-копии рабочей SQLite, безопасные для живого бота.

Аудит production 04.10.2026 показал: на диске лежали только копии от 13 и 30
сентября, хотя база пишется каждую минуту. Модуль делает консистентную копию
средствами самого SQLite (журнал WAL читается корректно), не заменяет уже
существующие файлы, удаляет только свои старые копии и никогда не выбрасывает
исключение в цикл бота.

Копия лежит на том же диске, поэтому она защищает от порчи базы и ошибочной
миграции, но не от потери самого диска: выгрузку за пределы Railway владелец
делает отдельно.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_RETENTION = 5
DEFAULT_PREFIX = "auto"
# Копия временно занимает столько же, сколько база; запас нужен, чтобы не
# заполнить диск до отказа и не уронить живого бота.
FREE_SPACE_FACTOR = 3


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _prune(backup_dir: Path, prefix: str, retention: int) -> list[str]:
    """Удаляет самые старые копии этого префикса, чужие файлы не трогает."""
    if retention <= 0:
        return []
    candidates = sorted(backup_dir.glob(f"{prefix}-*.db"))
    removed: list[str] = []
    for path in candidates[:-retention]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as error:
            logger.warning("Не удалось удалить старую копию %s: %s", path.name, error)
    return removed


def create_backup(
    source: Path | str,
    backup_dir: Path | str,
    *,
    prefix: str = DEFAULT_PREFIX,
    retention: int = DEFAULT_RETENTION,
    now: float | None = None,
) -> Path:
    """Публикует одну консистентную копию базы и возвращает её путь."""
    source = Path(source)
    backup_dir = Path(backup_dir)
    if not source.is_file():
        raise ValueError(f"Нет файла базы для копии: {source}")
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = _stamp(time.time() if now is None else now)
    destination = backup_dir / f"{prefix}-{stamp}.db"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"{prefix}-{stamp}-{counter}.db"
        counter += 1

    required = source.stat().st_size * FREE_SPACE_FACTOR
    free = shutil.disk_usage(backup_dir).free
    if free < required:
        raise ValueError(
            f"Мало места для копии базы: свободно {free} байт, нужно {required}"
        )

    try:
        # Важно: sqlite3.Connection как контекстный менеджер только коммитит, но не
        # закрывает файл. Незакрытое соединение блокирует копию на Windows и мешает
        # последующей ротации.
        with closing(sqlite3.connect(str(source))) as original, closing(
            sqlite3.connect(str(destination))
        ) as copy:
            original.backup(copy, pages=256, sleep=0.1)
    except BaseException:
        # Неполный файл не должен выглядеть как рабочая копия.
        destination.unlink(missing_ok=True)
        raise

    removed = _prune(backup_dir, prefix, retention)
    if removed:
        logger.info("Удалены старые копии базы: %s", ", ".join(removed))
    return destination


async def run_backup_loop(
    source: Path | str,
    backup_dir: Path | str,
    *,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    retention: int = DEFAULT_RETENTION,
    prefix: str = DEFAULT_PREFIX,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Раз в сутки делает копию; сбой одной копии не останавливает цикл."""
    while stop_event is None or not stop_event.is_set():
        try:
            path = await asyncio.to_thread(
                create_backup,
                Path(source),
                Path(backup_dir),
                prefix=prefix,
                retention=retention,
            )
            logger.info("Резервная копия базы создана: %s", path.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Копия не должна ронять бота: следующая попытка будет через интервал.
            logger.exception("Не удалось создать резервную копию базы")
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
