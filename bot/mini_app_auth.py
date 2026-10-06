"""Bounded JSON and Telegram identity verification shared by Mini App routes."""

from __future__ import annotations

from aiohttp import web

from .request_body import bounded_json_object, json_content_type
from .telegram_identity import VerifiedTelegramIdentity, verify_webapp_identity


# Тело запроса Mini App: подпись Telegram и несколько коротких полей.
MAX_BODY_BYTES = 8192


async def verified_identity_payload(
    request: web.Request, bot_token: str,
) -> tuple[VerifiedTelegramIdentity | None, dict[str, object] | None, int]:
    if not json_content_type(request):
        return None, None, 415
    values, status = await bounded_json_object(request, MAX_BODY_BYTES)
    if status != 200 or values is None:
        return None, None, status
    init_data = values.get("init_data")
    if not isinstance(init_data, str) or not init_data:
        return None, None, 401
    identity = verify_webapp_identity(init_data, bot_token)
    return (identity, values, 200) if identity is not None else (None, None, 403)


async def verified_payload(request: web.Request, bot_token: str) -> tuple[int | None, dict[str, object] | None, int]:
    identity, values, status = await verified_identity_payload(request, bot_token)
    return (identity.id if identity is not None else None, values, status)
