"""Authenticated read-only web routes mounted on the existing aiohttp server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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


def _login_page(error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{error}</p>' if error else ""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        '<title>Вход · TwitchSignalBot</title>'
        '<link rel="stylesheet" href="/admin/login.css"></head>'
        '<body><main class="login"><div class="brand"><span class="brand-mark" aria-hidden="true"></span>'
        '<span>TwitchSignal<span class="brand-bot">Bot</span></span></div>'
        '<h1>Вход в панель</h1><p>Диагностика тестового контура доступна владельцу.</p>'
        '<form action="/admin/login" method="post">'
        '<label for="access_key">Ключ доступа</label>'
        '<input id="access_key" name="access_key" type="password" autocomplete="current-password" required>'
        '<button type="submit">Войти</button></form>'
        f'{error_html}<p class="fine">TwitchSignalBot · staging</p></main></body></html>'
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
            response.headers.update(SECURITY_HEADERS)
        return response

    app.middlewares.append(headers)

    async def index(request: web.Request) -> web.Response:
        if _authorized(request):
            body = (_UI_DIR / "index.html").read_text(encoding="utf-8")
        else:
            body = _login_page()
        return web.Response(text=body, content_type="text/html")

    async def asset(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name != "login.css" and not _authorized(request):
            return web.Response(status=401)
        content_type = "application/javascript" if name.endswith(".js") else "text/css"
        return web.Response(text=(_UI_DIR / name).read_text(encoding="utf-8"), content_type=content_type)

    async def login(request: web.Request) -> web.Response:
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
            return web.Response(status=429, text=_login_page("Слишком много попыток. Повторите позже."), content_type="text/html")
        if token is None:
            return web.Response(status=401, text=_login_page("Неверный ключ доступа"), content_type="text/html")
        response = web.Response(status=303, headers={"Location": "/admin"})
        response.set_cookie(
            "ts_admin", token, path="/admin", max_age=int(access.session_ttl),
            httponly=True, secure=access.secure_cookie, samesite="Strict",
        )
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
    app.router.add_get("/admin/{name:login\\.css|panel\\.css|panel\\.js}", asset)
    app.router.add_post("/admin/login", login)
    app.router.add_post("/admin/logout", logout)
    app.router.add_get("/admin/api/snapshot", snapshot)
