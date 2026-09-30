"""Streamer-only web cabinet on the existing callback server."""

from __future__ import annotations

from html import escape
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS
from .database import Database
from .streamer_auth import StreamerAccess

_UI_DIR = Path(__file__).with_name("streamer_ui")


def _login_page(username: str, callback_url: str) -> str:
    widget = (
        '<script async src="https://telegram.org/js/telegram-widget.js?22" '
        f'data-telegram-login="{escape(username, quote=True)}" data-size="large" '
        f'data-auth-url="{escape(callback_url, quote=True)}"></script>'
        if username else '<p>Вход через Telegram Login ещё не настроен.</p>'
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Кабинет стримера · TwitchSignalBot</title>'
        '<link rel="stylesheet" href="/streamer/login.css"></head><body>'
        '<main class="entry"><p class="eyebrow">TwitchSignalBot · staging</p>'
        '<h1>Кабинет стримера</h1>'
        '<p>Сначала подключи Twitch через /streamer_connect в личном чате с ботом. '
        'Затем войди с тем же Telegram-аккаунтом.</p>'
        f'{widget}'
        '<script src="https://telegram.org/js/telegram-web-app.js?63" defer></script>'
        '<script src="/streamer/login.js" defer></script>'
        '</main></body></html>'
    )


def install_streamer_routes(app: web.Application, access: StreamerAccess, db: Database) -> None:
    if not access.enabled:
        return

    @web.middleware
    async def security_headers(request: web.Request, handler):
        response = await handler(request)
        if request.path.startswith("/streamer"):
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app.middlewares.append(security_headers)

    def _user(request: web.Request) -> int | None:
        return access.user_for_session(request.cookies.get("ts_streamer"))

    def _session_response(token: str) -> web.Response:
        response = web.Response(status=303, headers={"Location": "/streamer"})
        response.set_cookie(
            "ts_streamer", token, path="/streamer", max_age=int(access.session_ttl),
            httponly=True, secure=access.secure_cookie, samesite="Strict",
        )
        return response

    async def index(request: web.Request) -> web.Response:
        if _user(request) is not None:
            return web.Response(
                text=(_UI_DIR / "index.html").read_text(encoding="utf-8"),
                content_type="text/html",
            )
        state = access.new_login_state()
        callback_url = (
            f"{access.public_base_url or str(request.url.origin())}"
            f"/streamer/telegram-login?state={state}"
        )
        response = web.Response(
            text=_login_page(access.bot_username, callback_url), content_type="text/html",
        )
        response.set_cookie(
            "ts_streamer_state", state, path="/streamer", max_age=300,
            httponly=True, secure=access.secure_cookie, samesite="Lax",
        )
        response.headers["Content-Security-Policy"] = (
            SECURITY_HEADERS["Content-Security-Policy"]
            .replace("script-src 'self'", "script-src 'self' https://telegram.org")
            .replace("base-uri 'none'", "frame-src https://oauth.telegram.org; base-uri 'none'")
        )
        return response

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in {"login.css", "login.js"} and _user(request) is None:
            return web.Response(status=401)
        return web.Response(
            text=(_UI_DIR / name).read_text(encoding="utf-8"),
            content_type="application/javascript" if name.endswith(".js") else "text/css",
        )

    async def webapp_login(request: web.Request) -> web.Response:
        if request.content_length is not None and request.content_length > 8192:
            return web.Response(status=413)
        try:
            form = await request.post()
            init_data = form.get("init_data", "")
        except Exception:
            return web.Response(status=400)
        token = access.login_webapp(init_data) if isinstance(init_data, str) else None
        if token is None:
            return web.Response(status=403)
        user_id = access.user_for_session(token)
        if user_id is None or await db.get_streamer_identity(user_id) is None:
            access.logout(token)
            return web.Response(status=403)
        return _session_response(token)

    async def widget_login(request: web.Request) -> web.Response:
        query = request.query
        if len(request.query_string) > 4096 or len(query) != len(set(query.keys())):
            return web.Response(status=403)
        state = query.get("state", "")
        values = {key: value for key, value in query.items() if key != "state"}
        token = access.login_telegram_widget(values)
        if token is None:
            return web.Response(status=403)
        user_id = access.user_for_session(token)
        if user_id is None or await db.get_streamer_identity(user_id) is None:
            access.logout(token)
            return web.Response(status=403)
        if not access.consume_login_state(state, request.cookies.get("ts_streamer_state")):
            access.logout(token)
            return web.Response(status=403)
        response = _session_response(token)
        response.del_cookie("ts_streamer_state", path="/streamer")
        return response

    async def logout(request: web.Request) -> web.Response:
        access.logout(request.cookies.get("ts_streamer"))
        response = web.Response(status=303, headers={"Location": "/streamer"})
        response.del_cookie("ts_streamer", path="/streamer")
        return response

    async def profile(request: web.Request) -> web.Response:
        user_id = _user(request)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            identity = await db.get_streamer_identity(user_id)
            if identity is None:
                return web.json_response({"error": "not_linked"}, status=403)
            expiry = await db.get_streamer_plus_expiry(user_id)
        except Exception:
            return web.json_response({"error": "profile_unavailable"}, status=503)
        return web.json_response({
            "twitch_login": identity[1], "plus_active": expiry is not None,
            "plus_expires_at": expiry,
        })

    app.router.add_get("/streamer", index)
    app.router.add_get("/streamer/{name:login\\.css|login\\.js|panel\\.css|panel\\.js}", asset)
    app.router.add_post("/streamer/telegram-webapp", webapp_login)
    app.router.add_get("/streamer/telegram-login", widget_login)
    app.router.add_post("/streamer/logout", logout)
    app.router.add_get("/streamer/api/profile", profile)
