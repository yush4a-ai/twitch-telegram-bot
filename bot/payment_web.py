"""Reserved callback route; this release mounts it only in a local contract app."""

import ipaddress
import sqlite3
import time

from aiohttp import web


def install_payment_routes(app: web.Application, service=None, *, local_contract_enabled=False) -> bool:
    if local_contract_enabled is not True or service is None or not service._local_runtime():
        return False

    async def callback(request):
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
        except (ValueError, PermissionError):
            return web.json_response({"error": "invalid_callback"}, status=403)
        except (OverflowError, sqlite3.Error, RuntimeError):
            return web.json_response({"error": "callback_not_stored"}, status=503)
        return web.json_response({"received": receipt.durable})

    app.router.add_post("/payments/platega/callback", callback)
    return True
