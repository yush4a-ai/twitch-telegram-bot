"""Shared Mini App shell and authenticated bootstrap on the existing web server."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS
from .capabilities import CapabilityService
from .database import Database
from .telegram_identity import verify_webapp_user


_UI_DIR = Path(__file__).with_name("mini_app_ui")
_ASSETS = {
    "app.css": "text/css",
    "app.js": "application/javascript",
    "telegram.js": "application/javascript",
    "router.js": "application/javascript",
    "api.js": "application/javascript",
    "components.js": "application/javascript",
}


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Mini App field")
        result[key] = value
    return result


def install_mini_app_routes(
    app: web.Application,
    db: Database,
    bot_token: str,
    *,
    bot=None,
    capability_service: CapabilityService | None = None,
) -> None:
    if not bot_token:
        raise ValueError("Mini App needs bot token for initData verification")
    capabilities = capability_service or CapabilityService(db)

    @web.middleware
    async def app_headers(request: web.Request, handler):
        response = await handler(request)
        if request.path == "/app" or request.path.startswith("/app/"):
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app.middlewares.append(app_headers)

    async def shell(_request: web.Request) -> web.Response:
        response = web.Response(
            text=(_UI_DIR / "index.html").read_text(encoding="utf-8"),
            content_type="text/html",
        )
        response.headers["Content-Security-Policy"] = (
            SECURITY_HEADERS["Content-Security-Policy"]
            .replace("script-src 'self'", "script-src 'self' https://telegram.org")
        )
        return response

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        return web.Response(
            text=(_UI_DIR / name).read_text(encoding="utf-8"),
            content_type=_ASSETS[name],
        )

    async def bootstrap(request: web.Request) -> web.Response:
        if request.content_length is not None and request.content_length > 8192:
            return web.json_response({"error": "too_large"}, status=413)
        try:
            raw = await request.text()
            if len(raw.encode("utf-8")) > 8192:
                return web.json_response({"error": "too_large"}, status=413)
            values = json.loads(raw, object_pairs_hook=_unique_pairs)
        except (UnicodeError, ValueError, TypeError):
            return web.json_response({"error": "invalid_request"}, status=400)
        if not isinstance(values, dict):
            return web.json_response({"error": "invalid_request"}, status=400)
        init_data = values.get("init_data")
        if not isinstance(init_data, str) or not init_data:
            return web.json_response({"error": "unauthorized"}, status=401)
        user_id = verify_webapp_user(init_data, bot_token)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=403)
        flags = await capabilities.for_user(user_id, now=time.time())
        return web.json_response({"user": {"id": user_id}, "capabilities": asdict(flags)})

    app.router.add_get("/app", shell)
    app.router.add_get("/app/{name:app\\.(?:js|css)|telegram\\.js|router\\.js|api\\.js|components\\.js}", asset)
    app.router.add_post("/app/api/bootstrap", bootstrap)
