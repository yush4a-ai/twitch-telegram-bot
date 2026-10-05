"""Картинки рассылок и переписки: тип по содержимому, лимит размера, удаление.

Файлы лежат рядом с базой и никогда не исполняются. Тип определяется по
сигнатуре файла, а не по расширению из запроса: расширению верить нельзя.
"""
from __future__ import annotations

import logging
import pathlib
import secrets

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class MediaError(ValueError):
    """Файл не принят: слишком большой или не картинка нужного типа."""


def image_extension(payload: bytes) -> str | None:
    if payload.startswith(JPEG_MAGIC):
        return "jpg"
    if payload.startswith(PNG_MAGIC):
        return "png"
    return None


def save_image(directory: pathlib.Path, payload: bytes, *, prefix: str) -> str:
    """Сохраняет картинку и возвращает путь. Имя случайное: чужие имена не повторяем."""
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise MediaError("empty payload")
    if len(payload) > MAX_IMAGE_BYTES:
        raise MediaError("image is too large")
    extension = image_extension(bytes(payload))
    if extension is None:
        raise MediaError("unsupported image type")
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{prefix}-{secrets.token_hex(8)}.{extension}"
    target.write_bytes(bytes(payload))
    return str(target)


def remove_image(path: str | None, *, directory: pathlib.Path) -> bool:
    """Удаляет файл, только если он действительно лежит в каталоге медиа."""
    if not path:
        return False
    directory = pathlib.Path(directory).resolve()
    candidate = pathlib.Path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    if resolved.parent != directory or not resolved.is_file():
        return False
    try:
        resolved.unlink()
    except OSError:
        logger.warning("Не удалось удалить файл медиа")
        return False
    return True
