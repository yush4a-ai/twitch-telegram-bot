"""Shared Mini App shell and authenticated bootstrap on the existing web server."""

from __future__ import annotations

import time
import re
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS, security_headers
from .capabilities import CapabilityService
from .database import Database
from .mini_app_auth import verified_identity_payload
from .mini_app_viewer import install_mini_app_viewer_routes
from .mini_app_streamer import install_mini_app_streamer_routes
from .mini_app_billing import install_mini_app_billing_routes
from .legal_web import install_legal_routes
from .mini_app_reports import install_report_routes


_UI_DIR = Path(__file__).with_name("mini_app_ui")
_ASSETS = {
    "app.css": "text/css",
    "reports.js": "text/javascript",
    "app.js": "application/javascript",
    "telegram.js": "application/javascript",
    "theme.js": "application/javascript",
    "router.js": "application/javascript",
    "api.js": "application/javascript",
    "failures.js": "application/javascript",
    "components.js": "application/javascript",
    "viewer.js": "application/javascript",
    "streamer.js": "application/javascript",
    "streamer_posts.js": "application/javascript",
    "subscription.js": "application/javascript",
    "purchase.js": "application/javascript",
    "profile.js": "application/javascript",
    "support.js": "application/javascript",
    "plus-mascot.png": "image/png",
    "mascot-cutout.png": "image/png",
}


def install_mini_app_routes(
    app: web.Application,
    db: Database,
    bot_token: str,
    *,
    bot=None,
    twitch=None,
    bot_username: str = "",
    oauth_server=None,
    capability_service: CapabilityService | None = None,
    billing_test_enabled: bool = False,
    billing_test_user_ids: frozenset[int] = frozenset(),
    billing_service=None,
    preview_status_provider=None,
    owner_config=None,
    legal_store=None,
) -> None:
    if not bot_token:
        raise ValueError("Mini App needs bot token for initData verification")
    capabilities = capability_service or CapabilityService(db)

    @web.middleware
    async def app_headers(request: web.Request, handler):
        response = await handler(request)
        if request.path == "/app" or request.path.startswith("/app/"):
            for key, value in security_headers(request).items():
                if request.path == "/app" and key == "X-Frame-Options":
                    continue
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
            .replace("img-src 'self'", "img-src 'self' data: https://static-cdn.jtvnw.net https://t.me")
            # Мини-апп открывается во фрейме web.telegram.org — это единственное
            # исключение; у остальных приватных страниц frame-ancestors 'none'.
            .replace("frame-ancestors 'none'", "frame-ancestors https://web.telegram.org")
        )
        return response

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        return web.Response(
            body=(_UI_DIR / name).read_bytes(),
            content_type=_ASSETS[name],
            charset=None if _ASSETS[name].startswith("image/") else "utf-8",
        )

    async def bootstrap(request: web.Request) -> web.Response:
        identity, _values, status = await verified_identity_payload(request, bot_token)
        if status != 200:
            return web.json_response({"error": "unauthorized"}, status=status)
        flags = await capabilities.for_user(identity.id, now=time.time())
        return web.json_response({"user": asdict(identity), "capabilities": asdict(flags)})

    app.router.add_get("/app", shell)
    asset_pattern = "|".join(re.escape(name) for name in _ASSETS)
    app.router.add_get(f"/app/{{name:{asset_pattern}}}", asset)
    app.router.add_post("/app/api/bootstrap", bootstrap)
    install_legal_routes(app, bot_token, owner_config=owner_config, store=legal_store)
    install_report_routes(app, db, bot_token, bot)
    install_mini_app_viewer_routes(
        app, db, bot_token, capabilities, twitch,
        preview_status_provider=preview_status_provider,
    )
    install_mini_app_streamer_routes(
        app, db, bot_token, bot, bot_username=bot_username,
        oauth_server=oauth_server,
    )
    install_mini_app_billing_routes(
        app, db, bot_token, test_enabled=billing_test_enabled,
        test_user_ids=billing_test_user_ids, live_service=billing_service,
    )
