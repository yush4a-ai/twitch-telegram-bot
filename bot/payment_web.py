"""Callback провайдера: локальный контракт или публичный маршрут по допуску."""

import ipaddress
import logging
import sqlite3
import time

from aiohttp import web


logger = logging.getLogger(__name__)


def install_payment_routes(app: web.Application, service=None, *,
                           local_contract_enabled=False, public_callback=False) -> bool:
    """Монтирует приём уведомлений провайдера.

    Два независимых режима. Локальный контракт (`local_contract_enabled`)
    обслуживает только loopback — это тестовый контур. Публичный маршрут
    (`public_callback`) открыт внешней сети и включается только тогда, когда
    владелец разрешил банковский канал: подлинность запроса подтверждают
    заголовки провайдера, а не адрес отправителя.
    """
    if service is None:
        return False
    loopback_only = local_contract_enabled is True and service._local_runtime()
    public = public_callback is True and service.public_callback_ready()
    if not (loopback_only or public):
        return False

    async def callback(request):
        if loopback_only:
            try:
                local = request.remote is not None and ipaddress.ip_address(request.remote).is_loopback
            except ValueError:
                local = False
            if not local:
                raise web.HTTPNotFound()
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_content_type"}, status=415)
        try:
            if request.content_length is not None and request.content_length > 8192:
                return web.json_response({"error": "body_too_large"}, status=413)
            body = bytearray()
            async for chunk in request.content.iter_chunked(1024):
                body.extend(chunk)
                if len(body) > 8192:
                    return web.json_response({"error": "body_too_large"}, status=413)
            receipt = await service.accept_provider_notice(bytes(body), request.headers, now=time.time())
        except (ValueError, PermissionError) as error:
            # Причина без значений: иначе отказ неотличим от чужого запроса.
            logger.warning(
                "Уведомление провайдера отклонено: %s: %s",
                type(error).__name__, str(error)[:120],
            )
            return web.json_response({"error": "invalid_callback"}, status=403)
        except (OverflowError, sqlite3.Error, RuntimeError):
            return web.json_response({"error": "callback_not_stored"}, status=503)
        return web.json_response({"received": receipt.durable})

    app.router.add_post("/payments/platega/callback", callback)
    return True
