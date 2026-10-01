"""Signed Telegram Mini App routes for private viewer settings."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS
from .database import Database
from .telegram_identity import verify_webapp_user


_UI_DIR = Path(__file__).with_name("viewer_ui")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Mini App field")
        result[key] = value
    return result


def install_viewer_routes(app: web.Application, db: Database, bot_token: str) -> None:
    if not bot_token:
        raise ValueError("Mini App needs bot token for initData verification")

    @web.middleware
    async def viewer_headers(request: web.Request, handler):
        response = await handler(request)
        if request.path.startswith("/viewer"):
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app.middlewares.append(viewer_headers)

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
            content_type="application/javascript" if name == "app.js" else "text/css",
        )

    async def _payload(request: web.Request) -> tuple[int | None, dict[str, object] | None, int]:
        if request.content_length is not None and request.content_length > 8192:
            return None, None, 413
        try:
            raw = await request.text()
            if len(raw.encode("utf-8")) > 8192:
                return None, None, 413
            values = json.loads(raw, object_pairs_hook=_unique_pairs)
            if not isinstance(values, dict):
                return None, None, 400
        except (UnicodeError, ValueError, TypeError):
            return None, None, 400
        init_data = values.get("init_data")
        if not isinstance(init_data, str) or not init_data:
            return None, None, 401
        user_id = verify_webapp_user(init_data, bot_token)
        return (user_id, values, 200) if user_id is not None else (None, None, 403)

    async def state(request: web.Request) -> web.Response:
        user_id, _values, status = await _payload(request)
        if status != 200:
            return web.json_response({"error": "unauthorized"}, status=status)
        plus = await db.has_viewer_plus(user_id)
        subscriptions = []
        for login, notify_enabled, is_live in await db.list_channels_with_notify(user_id):
            stored = await db.get_viewer_filter(user_id, login) if plus else None
            subscriptions.append({
                "login": login, "notify_enabled": bool(notify_enabled),
                "is_live": bool(is_live),
                "filter": ({
                    "version": stored[0], "games": list(stored[1].games),
                    "title_keywords": list(stored[1].title_keywords),
                    "exclude_keywords": list(stored[1].exclude_keywords),
                } if stored is not None else None),
            })
        quiet = await db.get_quiet_hours(user_id)
        return web.json_response({
            "plus_active": plus, "subscriptions": subscriptions,
            "digest_available": quiet is not None,
            "digest_enabled": bool(quiet[3]) if quiet is not None else False,
        })

    async def notify(request: web.Request) -> web.Response:
        user_id, values, status = await _payload(request)
        if status != 200:
            return web.json_response({"error": "unauthorized"}, status=status)
        login = values.get("login")
        enabled = values.get("enabled")
        if not isinstance(login, str) or type(enabled) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if login not in await db.list_channels(user_id):
            return web.json_response({"error": "not_subscribed"}, status=404)
        await db.set_notify_enabled(user_id, login, enabled)
        return web.json_response({"notify_enabled": enabled})

    async def save_filter(request: web.Request) -> web.Response:
        user_id, values, status = await _payload(request)
        if status != 200:
            return web.json_response({"error": "unauthorized"}, status=status)
        login = values.get("login")
        version = values.get("expected_version")
        if not isinstance(login, str) or type(version) is not int:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if login not in await db.list_channels(user_id):
            return web.json_response({"error": "not_subscribed"}, status=404)
        try:
            saved = await db.save_viewer_filter(
                user_id, login, expected_version=version,
                games=values.get("games"),
                title_keywords=values.get("title_keywords"),
                exclude_keywords=values.get("exclude_keywords"),
            )
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        except ValueError:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if saved is None:
            return web.json_response({"error": "version_conflict"}, status=409)
        return web.json_response({"version": saved})

    async def digest(request: web.Request) -> web.Response:
        user_id, values, status = await _payload(request)
        if status != 200:
            return web.json_response({"error": "unauthorized"}, status=status)
        enabled = values.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if not await db.set_quiet_hours_notify_after(user_id, enabled):
            return web.json_response({"error": "quiet_hours_required"}, status=409)
        return web.json_response({"digest_enabled": enabled})

    app.router.add_get("/viewer", shell)
    app.router.add_get("/viewer/{name:app\\.(?:js|css)}", asset)
    app.router.add_post("/viewer/api/state", state)
    app.router.add_post("/viewer/api/notify", notify)
    app.router.add_post("/viewer/api/filter", save_filter)
    app.router.add_post("/viewer/api/digest", digest)
