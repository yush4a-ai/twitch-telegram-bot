"""Ограниченное чтение JSON-тела: предел проверяется во время чтения.

При chunked-запросе ``Content-Length`` неизвестен, поэтому ``await request.text()``
вычитывал тело целиком (до стандартного предела aiohttp в 1 МиБ) и только потом
сравнивал размер с лимитом маршрута. Здесь тело читается порциями, и чтение
прекращается сразу после превышения предела отправителя.
"""

from __future__ import annotations

import json

from aiohttp import web

CHUNK_BYTES = 1024
# Единственный разрешённый тип тела для JSON-маршрутов Mini App и панели.
JSON_CONTENT_TYPE = "application/json"


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Дубли полей — это подмена: словарь молча оставил бы последнее значение."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


async def bounded_json_object(
    request: web.Request, limit: int,
) -> tuple[dict[str, object] | None, int]:
    """Читает JSON-объект, не вычитывая тело дальше предела.

    Возвращает пару (значение, статус): 200 — разобранный объект, 413 — тело
    больше предела, 400 — тело не является JSON-объектом.
    """
    length = request.content_length
    if length is not None and length > limit:
        return None, 413
    raw = bytearray()
    async for chunk in request.content.iter_chunked(CHUNK_BYTES):
        raw.extend(chunk)
        if len(raw) > limit:
            # Остаток тела намеренно не читаем: иначе лимит ничего не экономит.
            return None, 413
    try:
        values = json.loads(bytes(raw), object_pairs_hook=unique_pairs)
    except (UnicodeError, ValueError, TypeError):
        return None, 400
    return (values, 200) if isinstance(values, dict) else (None, 400)


def json_content_type(request: web.Request) -> bool:
    """Mini App и панель принимают только application/json."""
    return request.content_type == JSON_CONTENT_TYPE
