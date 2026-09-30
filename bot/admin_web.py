"""Authenticated read-only web routes mounted on the existing aiohttp server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

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
            body = "<!doctype html><html lang='ru'><meta charset='utf-8'><title>TwitchSignalBot</title><body><h1>Панель владельца</h1></body></html>"
        else:
            body = (
                "<!doctype html><html lang='ru'><meta charset='utf-8'>"
                "<title>Вход · TwitchSignalBot</title><body><h1>Вход</h1>"
                "<form action='/admin/login' method='post'>"
                "<label>Ключ доступа <input name='access_key' type='password' required></label>"
                "<button type='submit'>Войти</button></form></body></html>"
            )
        return web.Response(text=body, content_type="text/html")

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
            return web.Response(status=429, text="Слишком много попыток. Повторите позже.")
        if token is None:
            return web.Response(status=401, text="Неверный ключ доступа")
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
    app.router.add_post("/admin/login", login)
    app.router.add_post("/admin/logout", logout)
    app.router.add_get("/admin/api/snapshot", snapshot)
