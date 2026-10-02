"""Bounded JSON and Telegram identity verification shared by Mini App routes."""

from __future__ import annotations

import json

from aiohttp import web

from .telegram_identity import VerifiedTelegramIdentity, verify_webapp_identity


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Mini App field")
        result[key] = value
    return result


async def verified_identity_payload(
    request: web.Request, bot_token: str,
) -> tuple[VerifiedTelegramIdentity | None, dict[str, object] | None, int]:
    if request.content_length is not None and request.content_length > 8192:
        return None, None, 413
    try:
        raw = await request.text()
        if len(raw.encode("utf-8")) > 8192:
            return None, None, 413
        values = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, TypeError):
        return None, None, 400
    if not isinstance(values, dict):
        return None, None, 400
    init_data = values.get("init_data")
    if not isinstance(init_data, str) or not init_data:
        return None, None, 401
    identity = verify_webapp_identity(init_data, bot_token)
    return (identity, values, 200) if identity is not None else (None, None, 403)


async def verified_payload(request: web.Request, bot_token: str) -> tuple[int | None, dict[str, object] | None, int]:
    identity, values, status = await verified_identity_payload(request, bot_token)
    return (identity.id if identity is not None else None, values, status)
