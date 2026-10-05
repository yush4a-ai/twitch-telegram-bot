"""Authenticated read-only web routes mounted on the existing aiohttp server."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path

from aiohttp import web

from .admin_auth import AdminAccess
from .config import environment_label
from .database import AccessConflict, AccessDenied
from .media_store import MAX_IMAGE_BYTES, MediaError, remove_image, save_image

SnapshotProvider = Callable[[], Awaitable[dict]]
PAGE_SIZE = 20
MAX_PAGE = 500

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'",
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
    database_provider=None,
    chat_sender_provider=None,
    media_dir_provider=None,
) -> None:
    if not access.enabled:
        return

    # Сервисы присоединяются в main() уже после старта сервера, поэтому
    # спрашиваем их на каждом запросе, а не запоминаем на момент установки.
    def _people():
        return people_provider() if callable(people_provider) else people_provider

    def _directory():
        return directory_provider() if callable(directory_provider) else directory_provider

    def _database():
        return database_provider() if callable(database_provider) else database_provider

    def _chat_sender():
        return chat_sender_provider() if callable(chat_sender_provider) else chat_sender_provider

    def _media_dir():
        return media_dir_provider() if callable(media_dir_provider) else media_dir_provider

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
        # Только цифры: у «²» isdigit() истинно, а int() падает.
        raw = request.match_info.get("user_id", "")
        return int(raw) if re.fullmatch(r"[0-9]{1,18}", raw) else None

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

    async def admin_mark(request: web.Request) -> web.Response:
        """Знак панели: тот же маскот, что в боте и приложении, только авторизованным."""
        if not _authorized(request):
            return web.Response(status=401)
        path = _UI_DIR / "mark.png"
        if not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "image/png", "Cache-Control": "no-store"})

    async def admin_font(request: web.Request) -> web.Response:
        """Локальные файлы шрифтов панели: только своё имя, только авторизованным."""
        if not _authorized(request):
            return web.Response(status=401)
        name = request.match_info["name"]
        if not re.fullmatch(r"[a-z0-9\-]+\.woff2", name):
            return web.Response(status=404)
        path = _UI_DIR / "fonts" / name
        if not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "font/woff2",
                "Cache-Control": "private, max-age=604800",
                **SECURITY_HEADERS,
            },
        )

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
        token = (access.login_webapp(init_data, request.remote)
                 if isinstance(init_data, str) else None)
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
        token = access.login_telegram_widget(values, request.remote)
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
            # Различаем «не настроен» и «не тот пользователь»: иначе владелец
            # видит запрет и не понимает, что дело в конфигурации.
            return web.json_response({"error": "owner_not_configured"}, status=403)
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

    # --- Рассылки и переписка ------------------------------------------------

    def _short_text(value: object, limit: int) -> str | None:
        """Обрезает пользовательский текст до разумной длины; пустое — это None."""
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text[:limit] if text else None

    def _campaign_id(request: web.Request) -> int | None:
        raw = request.match_info.get("campaign_id", "")
        return int(raw) if re.fullmatch(r"[0-9]{1,18}", raw) else None

    BROADCAST_FIELDS = {"request_key", "campaign_id", "title", "body",
                        "button_text", "button_url"}
    SEND_FIELDS = {"request_key"}
    REPLY_FIELDS = {"request_key", "body"}

    def _db_or_503():
        db = _database()
        return db if db is not None else None

    async def api_broadcasts(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        audience = await db.broadcast_audience(owner_id=access.owner_id or 0)
        return web.json_response({
            "campaigns": await db.list_broadcast_campaigns(limit=30),
            "audience": {key: value for key, value in audience.items() if key != "recipients"},
            "optouts": await db.list_broadcast_optouts(limit=200),
            "unread": await db.dialogue_unread_total(),
        })

    async def api_broadcast_save(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        payload = await _write_body(request, BROADCAST_FIELDS)
        if payload is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        title = _short_text(payload.get("title"), 120)
        body = _short_text(payload.get("body"), 4096)
        if not title or not body:
            return web.json_response({"error": "invalid_request"}, status=400)
        button_text = _short_text(payload.get("button_text"), 64)
        button_url = _short_text(payload.get("button_url"), 512)
        # Ссылку проверяем независимо от текста: иначе кнопка с чужим протоколом
        # уйдёт в Telegram, тот её отвергнет, и вся кампания получит «ошибки».
        if button_url and not button_url.startswith("https://"):
            return web.json_response({"error": "invalid_button"}, status=400)
        if button_text and not button_url:
            return web.json_response({"error": "invalid_button"}, status=400)
        if button_url and not button_text:
            button_text = "Открыть"
        campaign_id = payload.get("campaign_id")
        if campaign_id is not None and not isinstance(campaign_id, int):
            return web.json_response({"error": "invalid_request"}, status=400)
        request_key = payload.get("request_key")
        if request_key is not None and not isinstance(request_key, str):
            return web.json_response({"error": "invalid_request"}, status=400)
        if isinstance(request_key, str):
            existing = await db.broadcast_campaign_by_request_key(request_key)
            if existing is not None:
                return web.json_response({"campaign_id": existing, "repeat": True})
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            saved = await db.save_broadcast_campaign(
                title=title, body=body, created_by=access.owner_id or 0, now=time.time(),
                campaign_id=campaign_id, button_text=button_text, button_url=button_url,
                request_key=request_key,
            )
        except ValueError:
            return web.json_response({"error": "conflict"}, status=409)
        return web.json_response({"campaign_id": saved})

    async def api_broadcast_image(request: web.Request) -> web.Response:
        """Картинка кампании: тип по содержимому, не больше 5 МБ, только в черновик."""
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        campaign_id = _campaign_id(request)
        if campaign_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        directory = _media_dir()
        if not directory:
            return web.json_response({"error": "unavailable"}, status=503)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if request.content_length is not None and request.content_length > MAX_IMAGE_BYTES + 8192:
            return web.json_response({"error": "image_too_large"}, status=413)
        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response({"error": "invalid_request"}, status=400)
        payload = b""
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "image":
                continue
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = await part.read_chunk(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return web.json_response({"error": "image_too_large"}, status=413)
                chunks.append(chunk)
            payload = b"".join(chunks)
            break
        if not payload:
            return web.json_response({"error": "invalid_request"}, status=400)
        campaign = await db.get_broadcast_campaign(campaign_id)
        if campaign is None:
            return web.json_response({"error": "not_found"}, status=404)
        if campaign["state"] != "draft":
            return web.json_response({"error": "conflict"}, status=409)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            saved_path = save_image(
                Path(directory), payload, prefix=f"campaign-{campaign_id}")
        except MediaError:
            return web.json_response({"error": "invalid_image"}, status=400)
        # Прежняя картинка удаляется, чтобы каталог не копил мусор.
        remove_image(campaign.get("image_path"), directory=Path(directory))
        await db.set_broadcast_image(
            campaign_id, image_path=saved_path, image_bytes=len(payload))
        return web.json_response({"image_bytes": len(payload)})

    async def api_broadcast_send(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        campaign_id = _campaign_id(request)
        if campaign_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        payload = await _write_body(request, SEND_FIELDS)
        if payload is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        campaign = await db.get_broadcast_campaign(campaign_id)
        if campaign is None:
            return web.json_response({"error": "not_found"}, status=404)
        if campaign["state"] != "draft":
            return web.json_response({"error": "conflict"}, status=409)
        audience = await db.broadcast_audience(owner_id=access.owner_id or 0)
        if not audience["total"]:
            return web.json_response({"error": "empty_audience"}, status=400)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        now = time.time()
        queued = await db.prepare_broadcast_recipients(
            campaign_id, audience["recipients"], now=now)
        await db.set_broadcast_state(campaign_id, "sending", now=now)
        return web.json_response({"campaign_id": campaign_id, "queued": queued})

    async def api_broadcast_stop(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        campaign_id = _campaign_id(request)
        if campaign_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        stopped = await db.stop_broadcast(campaign_id, now=time.time())
        return web.json_response({"stopped": stopped})

    async def api_optout_restore(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        return web.json_response({"restored": await db.restore_broadcast_optout(user_id)})

    async def admin_media(request: web.Request) -> web.Response:
        """Картинки переписки и кампаний: только своё имя и только владельцу."""
        if not _authorized(request):
            return web.Response(status=401)
        directory = _media_dir()
        if not directory:
            return web.Response(status=503)
        name = request.match_info["name"]
        if not re.fullmatch(r"[a-z0-9\-]+\.(jpg|png)", name):
            return web.Response(status=404)
        path = Path(directory) / name
        if not path.is_file():
            return web.Response(status=404)
        content_type = "image/png" if name.endswith(".png") else "image/jpeg"
        return web.FileResponse(
            path,
            headers={
                "Content-Type": content_type,
                "Cache-Control": "private, max-age=600",
                **SECURITY_HEADERS,
            },
        )

    async def api_dialogues(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        query = (request.query.get("q", "") or "")[:64]
        return web.json_response({
            "dialogues": await db.list_dialogues(limit=40, query=query),
            "unread": await db.dialogue_unread_total(),
        })

    async def api_dialogue(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        messages = await db.dialogue_history(user_id, limit=200)
        for message in messages:
            # Путь на диске браузеру не нужен: отдаём только имя файла.
            image_path = message.pop("image_path", None)
            message["image_name"] = Path(image_path).name if image_path else None
        return web.json_response({"user_id": user_id, "messages": messages})

    async def api_dialogue_read(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        await db.mark_dialogue_read(user_id, now=time.time())
        return web.json_response({"ok": True})

    async def _read_reply(request: web.Request) -> tuple[str | None, bytes | None, str | None] | None:
        """Ответ в чате: JSON с текстом или multipart с текстом и картинкой."""
        if (request.content_type or "").startswith("multipart/form-data"):
            if (request.content_length is not None
                    and request.content_length > MAX_IMAGE_BYTES + 8192):
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_IMAGE_BYTES + 8192, actual_size=request.content_length)
            reader = await request.multipart()
            text: str | None = None
            image: bytes | None = None
            key: str | None = None
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "body":
                    raw = await part.read_chunk(8192)
                    text = _short_text(raw.decode("utf-8", "ignore"), 4096)
                elif part.name == "request_key":
                    raw = await part.read_chunk(512)
                    key = _short_text(raw.decode("utf-8", "ignore"), 128)
                elif part.name == "image":
                    chunks: list[bytes] = []
                    size = 0
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise web.HTTPRequestEntityTooLarge(
                                max_size=MAX_IMAGE_BYTES, actual_size=size)
                        chunks.append(chunk)
                    image = b"".join(chunks)
            return text, image, key
        payload = await _write_body(request, REPLY_FIELDS)
        if payload is None:
            return None
        key = payload.get("request_key")
        return (
            _short_text(payload.get("body"), 4096),
            None,
            key if isinstance(key, str) else None,
        )

    async def api_dialogue_reply(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        try:
            parsed = await _read_reply(request)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response({"error": "image_too_large"}, status=413)
        except Exception:
            return web.json_response({"error": "invalid_request"}, status=400)
        if parsed is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        text, image_bytes, request_key = parsed
        if not text and not image_bytes:
            return web.json_response({"error": "invalid_request"}, status=400)
        sender = _chat_sender()
        if sender is None:
            return web.json_response({"error": "unavailable"}, status=503)
        # Ключ проверяем до отправки: иначе повтор с тем же ключом отправит второе сообщение.
        if isinstance(request_key, str):
            existing = await db.dialogue_message_by_request_key(request_key)
            if existing is not None:
                return web.json_response({"repeat": True})
        image_path = None
        if image_bytes:
            directory = _media_dir()
            if not directory:
                return web.json_response({"error": "unavailable"}, status=503)
            try:
                image_path = save_image(Path(directory), image_bytes, prefix="chat")
            except MediaError:
                return web.json_response({"error": "invalid_image"}, status=400)
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        try:
            delivered = bool(await sender(user_id, text or "", image_path))
        except Exception:
            delivered = False
        message_id = await db.record_dialogue_message(
            user_id, "out", text, now=time.time(),
            image_path=image_path,
            delivery="sent" if delivered else "failed",
            request_key=request_key,
        )
        if message_id is None:
            return web.json_response({"repeat": True})
        return web.json_response({"delivered": delivered})

    async def api_dialogue_delete(request: web.Request) -> web.Response:
        denied = _denied(request)
        if denied is not None:
            return denied
        db = _db_or_503()
        if db is None:
            return web.json_response({"error": "unavailable"}, status=503)
        user_id = _person_id(request)
        if user_id is None:
            return web.json_response({"error": "invalid_request"}, status=400)
        guard = _write_guard(request)
        if guard is not None:
            return guard
        if not _consume_request_mark(request):
            return web.json_response({"error": "csrf_denied"}, status=403)
        images = await db.delete_dialogue(user_id)
        # Удаление переписки удаляет и файлы: иначе они переживут удаление.
        directory = _media_dir()
        removed = 0
        if directory:
            for path in images:
                if remove_image(path, directory=Path(directory)):
                    removed += 1
        return web.json_response({"deleted": True, "images": removed})

    app.router.add_get("/admin", index)
    app.router.add_get("/admin/{name:login\\.css|login\\.js|panel\\.css|panel\\.js|fonts\\.css}", asset)
    app.router.add_get("/admin/mark.png", admin_mark)
    app.router.add_get("/admin/fonts/{name}", admin_font)
    app.router.add_get("/admin/api/media/{name}", admin_media)
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
    app.router.add_get("/admin/api/broadcasts", api_broadcasts)
    app.router.add_post("/admin/api/broadcasts", api_broadcast_save)
    app.router.add_post("/admin/api/broadcasts/{campaign_id}/send", api_broadcast_send)
    app.router.add_post("/admin/api/broadcasts/{campaign_id}/image", api_broadcast_image)
    app.router.add_post("/admin/api/broadcasts/{campaign_id}/stop", api_broadcast_stop)
    app.router.add_post("/admin/api/optouts/{user_id}/restore", api_optout_restore)
    app.router.add_get("/admin/api/dialogues", api_dialogues)
    app.router.add_get("/admin/api/dialogues/{user_id}", api_dialogue)
    app.router.add_post("/admin/api/dialogues/{user_id}/read", api_dialogue_read)
    app.router.add_post("/admin/api/dialogues/{user_id}/reply", api_dialogue_reply)
    app.router.add_post("/admin/api/dialogues/{user_id}/delete", api_dialogue_delete)
