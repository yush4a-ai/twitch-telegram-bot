"""Authenticated read-only web routes mounted on the existing aiohttp server."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

from aiohttp import web

from .admin_auth import AdminAccess
from .config import environment_label
from .database import AccessConflict, AccessDenied

SnapshotProvider = Callable[[], Awaitable[dict]]
PAGE_SIZE = 20
MAX_PAGE = 500

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_UI_DIR = Path(__file__).with_name("admin_ui")


def _environment_note() -> str:
    label = environment_label().strip()
    return f"TwitchSignalBot · окружение {label.upper()}" if label else "TwitchSignalBot · панель владельца"


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
            if username else
            '<p>Вход через Telegram Login ещё не настроен на этом контуре. '
            'Войдите по <a href="/admin/emergency">аварийному ключу</a>.</p>'
        )
        intro = (
            f'<p>Откройте <b>@{escape(username)}</b> в Telegram и отправьте команду '
            '<code>/admin</code> — панель откроется внутри Telegram, вход выполнится сам. '
            'Либо войдите через Telegram прямо здесь.</p>'
            if username else
            '<p>Откройте личный чат с ботом и отправьте команду <code>/admin</code>.</p>'
        )
        widget_hint = (
            '<p class="fine" id="widget-hint" hidden>Кнопка входа не появилась. '
            'Привяжите домен к боту в @BotFather командой /setdomain '
            'или используйте команду /admin в самом боте.</p>'
        )
        login_html = (
            f'{intro}{widget}{error_html}{widget_hint}'
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
        '<h1>Вход в панель</h1><p>Панель доступна только владельцу.</p>'
        f'{login_html}'
        f'<p class="fine">{_environment_note()}</p></main></body></html>'
    )


def install_admin_routes(
    app: web.Application,
    access: AdminAccess,
    snapshot_provider: SnapshotProvider,
    *,
    people_provider=None,
    directory_provider=None,
) -> None:
    if not access.enabled:
        return

    # Сервисы присоединяются в main() уже после старта сервера, поэтому
    # спрашиваем их на каждом запросе, а не запоминаем на момент установки.
    def _people():
        return people_provider() if callable(people_provider) else people_provider

    def _directory():
        return directory_provider() if callable(directory_provider) else directory_provider

    def _authorized(request: web.Request) -> bool:
        return access.authenticated(request.cookies.get("ts_admin"))

    def _page(request: web.Request) -> tuple[int, int]:
        try:
            page = int(request.query.get("page", "1"))
        except (TypeError, ValueError):
            page = 1
        page = max(1, min(page, MAX_PAGE))
        return page, (page - 1) * PAGE_SIZE

    def _person_id(request: web.Request) -> int | None:
        raw = request.match_info.get("user_id", "")
        return int(raw) if raw.isdigit() and int(raw) > 0 else None

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
        if isinstance(payload, dict):
            # Метка для write-запросов выдаётся только вместе с данными владельца.
            payload["csrf"] = access.issue_csrf(request.cookies.get("ts_admin"))
        return web.json_response(payload)

    def _denied(request: web.Request) -> web.Response | None:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    def _write_guard(request: web.Request) -> web.Response | None:
        """Origin (если прислан) и метка CSRF: проверка без потребления."""
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != (access.public_base_url or str(request.url.origin())):
            return web.json_response({"error": "origin_denied"}, status=403)
        if not access.check_csrf(request.cookies.get("ts_admin"),
                                 request.headers.get("X-Admin-CSRF")):
            return web.json_response({"error": "csrf_denied"}, status=403)
        if not access.owner_id:
            return web.json_response({"error": "owner_required"}, status=403)
        return None

    def _consume_request_mark(request: web.Request) -> bool:
        """Метка сгорает только перед самой операцией, а не на ошибке валидации."""
        return access.consume_csrf(request.cookies.get("ts_admin"),
                                   request.headers.get("X-Admin-CSRF"))

    async def _write_body(request: web.Request, allowed: set[str]) -> dict | None:
        try:
            if request.content_length is not None and request.content_length > 4096:
                return None
            raw = await request.content.read(4097)
            if len(raw) > 4096:
                return None
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return None
        if not isinstance(payload, dict) or not set(payload) <= allowed:
            return None
        return payload

    async def api_users(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        query_text = (request.query.get("q", "") or "")[:64]
        filter_kind = request.query.get("filter", "all")
        if filter_kind not in ("all", "plus", "active_today"):
            filter_kind = "all"
        page, offset = _page(request)
        try:
            rows = await people.search_people(
                query_text, filter_kind=filter_kind, limit=PAGE_SIZE,
                offset=offset, now=time.time(),
            )
        except Exception:
            return web.json_response({"error": "unavailable"}, status=503)
        return web.json_response({"people": rows, "page": page, "limit": PAGE_SIZE})

    async def api_person(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        try:
            card = await people.person_card(user_id, now=time.time())
        except Exception:
            return web.json_response({"error": "unavailable"}, status=503)
        if card is None:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(card)

    async def api_person_history(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        _, offset = _page(request)
        try:
            events = await people.person_history(user_id, limit=PAGE_SIZE, offset=offset)
        except Exception:
            return web.json_response({"error": "unavailable"}, status=503)
        return web.json_response({"events": events, "limit": PAGE_SIZE})

    async def api_access(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        directory = _directory()
        if directory is None:
            return web.json_response({"error": "unavailable"}, status=503)
        state = request.query.get("state", "active")
        if state not in ("active", "history"):
            return web.json_response({"error": "invalid_request"}, status=400)
        _, offset = _page(request)
        now = time.time()
        try:
            if state == "active":
                overview = await directory.access_overview(now)
                grants = await directory.active_grants(now, PAGE_SIZE, offset)
                return web.json_response({"overview": overview, "grants": grants, "limit": PAGE_SIZE})
            events = await directory.history(PAGE_SIZE, offset)
        except Exception:
            return web.json_response({"error": "unavailable"}, status=503)
        return web.json_response({"events": events, "limit": PAGE_SIZE})

    GRANT_FIELDS = {"request_key", "target_user_id", "plan", "expires_at",
                    "reason", "reason_note", "comment"}
    EXTEND_FIELDS = {"request_key", "grant_id", "expected_expires_at", "expires_at",
                     "reason", "reason_note", "comment"}
    REVOKE_FIELDS = {"request_key", "grant_id", "expected_expires_at",
                     "reason", "reason_note", "comment"}

    async def access_grant(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_content_type"}, status=415)
        payload = await _write_body(request, GRANT_FIELDS)
        if payload is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            result = await people.grant_manual_access(
                int(payload["target_user_id"]), payload["plan"],
                expires_at=float(payload["expires_at"]), reason=payload.get("reason"),
                reason_note=payload.get("reason_note"), comment=payload.get("comment"),
                issued_by=access.owner_id, request_key=payload.get("request_key"),
                now=time.time(),
            )
        except AccessConflict:
            return web.json_response({"error": "conflict"}, status=409)
        except AccessDenied as error:
            return web.json_response({"error": getattr(error, "code", "denied")}, status=403)
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid_request"}, status=400)
        return web.json_response(result, status=201)

    async def access_extend(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_content_type"}, status=415)
        payload = await _write_body(request, EXTEND_FIELDS)
        if payload is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            result = await people.extend_manual_access(
                str(payload["grant_id"]),
                expected_expires_at=float(payload["expected_expires_at"]),
                expires_at=float(payload["expires_at"]), reason=payload.get("reason"),
                reason_note=payload.get("reason_note"), comment=payload.get("comment"),
                issued_by=access.owner_id, request_key=payload.get("request_key"),
                now=time.time(),
            )
        except AccessConflict:
            return web.json_response({"error": "conflict"}, status=409)
        except AccessDenied as error:
            return web.json_response({"error": getattr(error, "code", "denied")}, status=403)
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid_request"}, status=400)
        return web.json_response(result)

    async def access_revoke(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        people = _people()
        if people is None:
            return web.json_response({"error": "unavailable"}, status=503)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if request.content_type != "application/json":
            return web.json_response({"error": "invalid_content_type"}, status=415)
        payload = await _write_body(request, REVOKE_FIELDS)
        if payload is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            result = await people.revoke_manual_access(
                str(payload["grant_id"]),
                expected_expires_at=float(payload["expected_expires_at"]),
                reason=payload.get("reason"), reason_note=payload.get("reason_note"),
                comment=payload.get("comment"), issued_by=access.owner_id,
                request_key=payload.get("request_key"), now=time.time(),
            )
        except AccessConflict:
            return web.json_response({"error": "conflict"}, status=409)
        except AccessDenied as error:
            return web.json_response({"error": getattr(error, "code", "denied")}, status=403)
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid_request"}, status=400)
        return web.json_response(result)

    app.router.add_get("/admin", index)
    app.router.add_get("/admin/{name:login\\.css|login\\.js|panel\\.css|panel\\.js}", asset)
    app.router.add_get("/admin/emergency", emergency_page)
    app.router.add_post("/admin/emergency/login", emergency_login)
    app.router.add_post("/admin/telegram-webapp", telegram_webapp)
    app.router.add_get("/admin/telegram-login", telegram_login)
    app.router.add_post("/admin/logout", logout)
    app.router.add_get("/admin/api/snapshot", snapshot)
    app.router.add_get("/admin/api/users", api_users)
    app.router.add_get("/admin/api/users/{user_id}", api_person)
    app.router.add_get("/admin/api/users/{user_id}/history", api_person_history)
    app.router.add_get("/admin/api/access", api_access)
    app.router.add_post("/admin/api/access/grant", access_grant)
    app.router.add_post("/admin/api/access/extend", access_extend)
    app.router.add_post("/admin/api/access/revoke", access_revoke)
