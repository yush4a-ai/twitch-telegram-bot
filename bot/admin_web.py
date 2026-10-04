"""Authenticated read-only web routes mounted on the existing aiohttp server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

from aiohttp import web

from .admin_auth import AdminAccess

SnapshotProvider = Callable[[], Awaitable[dict]]

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_UI_DIR = Path(__file__).with_name("admin_ui")


def _login_page(error: str = "", *, emergency: bool = False, username: str = "", callback_url: str = "") -> str:
    error_html = f'<p class="error" role="alert">{error}</p>' if error else ""
    if emergency:
        login_html = (
            '<form action="/admin/emergency/login" method="post">'
            '<label for="access_key">Аварийный ключ доступа</label>'
            '<input id="access_key" name="access_key" type="password" autocomplete="current-password" required>'
            f'{error_html}<button type="submit">Войти</button></form>'
        )
    else:
        widget = (
            '<script async src="https://telegram.org/js/telegram-widget.js?22" '
            f'data-telegram-login="{escape(username, quote=True)}" data-size="large" '
            f'data-auth-url="{escape(callback_url, quote=True)}"></script>'
            if username else '<p>Вход через Telegram Login ещё не настроен на этом контуре.</p>'
        )
        login_html = (
            '<p>Откройте панель из личного чата бота или войдите через Telegram.</p>'
            f'{widget}{error_html}'
            '<script src="https://telegram.org/js/telegram-web-app.js?63" defer></script>'
            '<script src="/admin/login.js" defer></script>'
        )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        '<title>Вход · TwitchSignalBot</title>'
        '<link rel="stylesheet" href="/admin/login.css"></head>'
        '<body><main class="login"><div class="brand"><span class="brand-mark" aria-hidden="true"></span>'
        '<span>TwitchSignal<span class="brand-bot">Bot</span></span></div>'
        '<h1>Вход в панель</h1><p>Диагностика тестового контура доступна владельцу.</p>'
        f'{login_html}'
        '<p class="fine">TwitchSignalBot · staging</p></main></body></html>'
    )


def install_admin_routes(
    app: web.Application,
    access: AdminAccess,
    snapshot_provider: SnapshotProvider,
) -> None:
    if not access.enabled:
        return

    def _authorized(request: web.Request) -> bool:
        return access.authenticated(request.cookies.get("ts_admin"))

    @web.middleware
    async def headers(request: web.Request, handler):
        response = await handler(request)
        if request.path.startswith("/admin"):
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        return response

    app.middlewares.append(headers)

    async def index(request: web.Request) -> web.Response:
        if _authorized(request):
            body = (_UI_DIR / "index.html").read_text(encoding="utf-8")
            return web.Response(text=body, content_type="text/html")
        state = access.new_login_state(request.remote or '', request.cookies.get('ts_admin_state'))
        if state is None:
            return web.Response(status=429, text='Слишком много попыток входа. Попробуй через 5 минут.')
        callback_url = f"{access.public_base_url or str(request.url.origin())}/admin/telegram-login?state={state}"
        response = web.Response(text=_login_page(username=access.bot_username, callback_url=callback_url), content_type="text/html")
        response.set_cookie("ts_admin_state", state, path="/admin", max_age=300, httponly=True, secure=access.secure_cookie, samesite="Lax")
        response.headers["Content-Security-Policy"] = SECURITY_HEADERS["Content-Security-Policy"].replace("script-src 'self'", "script-src 'self' https://telegram.org").replace("base-uri 'none'", "frame-src https://oauth.telegram.org; base-uri 'none'")
        return response

    async def emergency_page(request: web.Request) -> web.Response:
        return web.Response(text=_login_page(emergency=True), content_type="text/html")

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in ("login.css", "login.js") and not _authorized(request):
            return web.Response(status=401)
        content_type = "application/javascript" if name.endswith(".js") else "text/css"
        return web.Response(text=(_UI_DIR / name).read_text(encoding="utf-8"), content_type=content_type)

    def _session_response(token: str) -> web.Response:
        response = web.Response(status=303, headers={"Location": "/admin"})
        response.set_cookie(
            "ts_admin", token, path="/admin", max_age=int(access.session_ttl),
            httponly=True, secure=access.secure_cookie, samesite="Strict",
        )
        return response

    async def emergency_login(request: web.Request) -> web.Response:
        if request.content_length is not None and request.content_length > 1024:
            return web.Response(status=413, text="Слишком большой запрос")
        try:
            form = await request.post()
        except Exception:
            return web.Response(status=400, text="Некорректный запрос")
        candidate = form.get("access_key", "")
        if not isinstance(candidate, str):
            candidate = ""
        token, limited = access.login(candidate, request.remote or "unknown")
        if limited:
            return web.Response(status=429, text=_login_page("Слишком много попыток. Повторите позже.", emergency=True), content_type="text/html")
        if token is None:
            return web.Response(status=401, text=_login_page("Неверный ключ доступа", emergency=True), content_type="text/html")
        return _session_response(token)

    async def telegram_webapp(request: web.Request) -> web.Response:
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
        return _session_response(token)

    async def telegram_login(request: web.Request) -> web.Response:
        query = request.query
        if len(request.query_string) > 4096 or len(query) != len(set(query.keys())):
            return web.Response(status=403)
        state = query.get("state", "")
        values = {key: value for key, value in query.items() if key != "state"}
        if (access.verified_widget_user(values) is None
                or not access.consume_login_state(state, request.cookies.get("ts_admin_state"))):
            return web.Response(status=403)
        token = access.login_telegram_widget(values)
        if token is None:
            return web.Response(status=403)
        response = _session_response(token)
        response.del_cookie("ts_admin_state", path="/admin")
        return response

    async def logout(request: web.Request) -> web.Response:
        access.logout(request.cookies.get("ts_admin"))
        response = web.Response(status=303, headers={"Location": "/admin"})
        response.del_cookie("ts_admin", path="/admin")
        return response

    async def snapshot(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            payload = await snapshot_provider()
        except Exception:
            return web.json_response({"error": "snapshot_unavailable"}, status=503)
        return web.json_response(payload)

    app.router.add_get("/admin", index)
    app.router.add_get("/admin/{name:login\\.css|login\\.js|panel\\.css|panel\\.js}", asset)
    app.router.add_get("/admin/emergency", emergency_page)
    app.router.add_post("/admin/emergency/login", emergency_login)
    app.router.add_post("/admin/telegram-webapp", telegram_webapp)
    app.router.add_get("/admin/telegram-login", telegram_login)
    app.router.add_post("/admin/logout", logout)
    app.router.add_get("/admin/api/snapshot", snapshot)
