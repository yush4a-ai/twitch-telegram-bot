"""Streamer-only web cabinet on the existing callback server."""

from __future__ import annotations

import asyncio
import json
import time
from html import escape
from pathlib import Path

from aiohttp import web

from .admin_web import SECURITY_HEADERS
from .database import Database
from .streamer_auth import StreamerAccess
from .streamer_community import verify_community_permission

_UI_DIR = Path(__file__).with_name("streamer_ui")


def _login_page(username: str, callback_url: str, environment: str = "") -> str:
    # Метка окружения приходит из конфигурации: раньше здесь было жёстко вписано
    # «staging», и это показывалось даже на боевом контуре.
    label = "Twitch Signal" + (f" · {environment}" if environment else "")
    widget = (
        '<script async src="https://telegram.org/js/telegram-widget.js?22" '
        f'data-telegram-login="{escape(username, quote=True)}" data-size="large" '
        f'data-auth-url="{escape(callback_url, quote=True)}"></script>'
        if username else '<p>Вход через Telegram Login ещё не настроен.</p>'
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Кабинет стримера · Twitch Signal</title>'
        '<link rel="stylesheet" href="/streamer/login.css"></head><body>'
        '<main class="entry"><p class="eyebrow">' + escape(label) + '</p>'
        '<h1>Кабинет стримера</h1>'
        '<p>Сначала подключите Twitch через /streamer_connect в личном чате с ботом. '
        'Затем войдите с тем же Telegram-аккаунтом.</p>'
        f'{widget}'
        '<script src="https://telegram.org/js/telegram-web-app.js?63" defer></script>'
        '<script src="/streamer/login.js" defer></script>'
        '</main></body></html>'
    )


def install_streamer_routes(app: web.Application, access: StreamerAccess, db: Database, bot=None, *,
                            environment: str = "") -> None:
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
            # Метка окружения подставляется из конфигурации: без неё в разметке
            # осталось бы чужое слово «staging» на боевом контуре.
            suffix = f" · {environment}" if environment else ""
            return web.Response(
                text=(_UI_DIR / "index.html").read_text(encoding="utf-8").replace(
                    "{{environment}}", escape(suffix)
                ),
                content_type="text/html",
            )
        state = access.new_login_state(request.remote or '', request.cookies.get('ts_streamer_state'))
        if state is None:
            return web.Response(status=429, text='Слишком много попыток входа. Попробуйте через 5 минут.')
        callback_url = (
            f"{access.public_base_url or str(request.url.origin())}"
            f"/streamer/telegram-login?state={state}"
        )
        response = web.Response(
            text=_login_page(access.bot_username, callback_url, environment), content_type="text/html",
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
        verified_user_id = access.verified_widget_user(values)
        if verified_user_id is None or await db.get_streamer_identity(verified_user_id) is None:
            return web.Response(status=403)
        if not access.consume_login_state(state, request.cookies.get("ts_streamer_state")):
            return web.Response(status=403)
        token = access.login_telegram_widget(values)
        if token is None:
            return web.Response(status=403)
        user_id = access.user_for_session(token)
        if user_id is None or await db.get_streamer_identity(user_id) is None:
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

    async def stats(request: web.Request) -> web.Response:
        user_id = _user(request)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        if await db.get_streamer_identity(user_id) is None or not await db.has_streamer_plus(user_id):
            return web.json_response({"error": "plus_required"}, status=403)
        values = await db.get_streamer_delivery_stats(
            user_id, since=time.time() - 30 * 86400,
        )
        return web.json_response({"period_days": 30, **values})

    async def communities(request: web.Request) -> web.Response:
        user_id = _user(request)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        if bot is None:
            return web.json_response({"error": "unavailable"}, status=503)
        if await db.get_streamer_identity(user_id) is None:
            return web.json_response({"error": "not_linked"}, status=403)
        stored = await db.list_streamer_communities(user_id)
        semaphore = asyncio.Semaphore(3)

        async def check(row):
            async with semaphore:
                return await verify_community_permission(bot, row[0], user_id)

        checked = await asyncio.gather(*(check(row) for row in stored))
        return web.json_response({"communities": [
            {"chat_id": item.chat_id, "title": item.title, "chat_type": item.chat_type}
            for item in checked if item is not None
        ]})

    async def connect_community(request: web.Request) -> web.Response:
        user_id = _user(request)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        if bot is None:
            return web.json_response({"error": "unavailable"}, status=503)
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != (access.public_base_url or str(request.url.origin())):
            return web.json_response({"error": "origin_denied"}, status=403)
        if await db.get_streamer_identity(user_id) is None:
            return web.json_response({"error": "not_linked"}, status=403)
        try:
            body = await request.read()
            if len(body) > 2048:
                return web.json_response({"error": "invalid_request"}, status=413)
            payload = json.loads(body)
            chat_id = payload.get("chat_id") if isinstance(payload, dict) else None
            if type(chat_id) is not int or chat_id >= 0:
                return web.json_response({"error": "invalid_chat"}, status=400)
        except (ValueError, UnicodeError):
            return web.json_response({"error": "invalid_request"}, status=400)
        verified = await verify_community_permission(bot, chat_id, user_id)
        if verified is None:
            return web.json_response({"error": "permission_denied"}, status=403)
        try:
            saved = await db.add_streamer_community(
                user_id, verified.chat_id, verified.title, verified.chat_type,
            )
        except ValueError:
            return web.json_response({"error": "community_limit"}, status=400)
        if not saved:
            return web.json_response({"error": "not_linked"}, status=403)
        return web.json_response({
            "chat_id": verified.chat_id, "title": verified.title,
            "chat_type": verified.chat_type,
        }, status=201)

    async def _template_access(request: web.Request) -> tuple[int, int] | web.Response:
        user_id = _user(request)
        if user_id is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            chat_id = int(request.match_info["chat_id"])
        except ValueError:
            return web.json_response({"error": "invalid_chat"}, status=400)
        if chat_id >= 0:
            return web.json_response({"error": "invalid_chat"}, status=400)
        if bot is None:
            return web.json_response({"error": "unavailable"}, status=503)
        if await db.get_streamer_identity(user_id) is None or not await db.has_streamer_plus(user_id):
            return web.json_response({"error": "plus_required"}, status=403)
        stored = await db.list_streamer_communities(user_id)
        if not any(item[0] == chat_id for item in stored):
            return web.json_response({"error": "permission_denied"}, status=403)
        if await verify_community_permission(bot, chat_id, user_id) is None:
            return web.json_response({"error": "permission_denied"}, status=403)
        return user_id, chat_id

    async def get_template(request: web.Request) -> web.Response:
        permitted = await _template_access(request)
        if isinstance(permitted, web.Response):
            return permitted
        user_id, chat_id = permitted
        row = await db.get_streamer_template(user_id, chat_id)
        if row is None:
            return web.json_response({"version": 0, "headline": "", "body": "", "buttons": []})
        return web.json_response({
            "version": row[0], "headline": row[1], "body": row[2], "buttons": row[3],
        })

    async def put_template(request: web.Request) -> web.Response:
        permitted = await _template_access(request)
        if isinstance(permitted, web.Response):
            return permitted
        user_id, chat_id = permitted
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != (access.public_base_url or str(request.url.origin())):
            return web.json_response({"error": "origin_denied"}, status=403)
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_content_type"}, status=415)
        try:
            if request.content_length is not None and request.content_length > 4096:
                return web.json_response({"error": "invalid_request"}, status=413)
            body = await request.content.read(4097)
            if len(body) > 4096:
                return web.json_response({"error": "invalid_request"}, status=413)
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {
                "version", "headline", "body", "buttons"
            }:
                return web.json_response({"error": "invalid_request"}, status=400)
            version = await db.save_streamer_template(
                user_id, chat_id, expected_version=payload["version"],
                headline=payload["headline"], body=payload["body"],
                buttons=payload["buttons"],
            )
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"error": "invalid_template"}, status=400)
        if version is None:
            return web.json_response({"error": "stale_template"}, status=409)
        return web.json_response({"version": version})

    app.router.add_get("/streamer", index)
    app.router.add_get("/streamer/{name:login\\.css|login\\.js|panel\\.css|panel\\.js}", asset)
    app.router.add_post("/streamer/telegram-webapp", webapp_login)
    app.router.add_get("/streamer/telegram-login", widget_login)
    app.router.add_post("/streamer/logout", logout)
    app.router.add_get("/streamer/api/profile", profile)
    app.router.add_get("/streamer/api/stats", stats)
    app.router.add_get("/streamer/api/communities", communities)
    app.router.add_post("/streamer/api/communities", connect_community)
    app.router.add_get("/streamer/api/templates/{chat_id}", get_template)
    app.router.add_put("/streamer/api/templates/{chat_id}", put_template)
