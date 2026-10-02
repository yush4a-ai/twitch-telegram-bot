from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from .admin_auth import AdminAccess
from .admin_web import SnapshotProvider, install_admin_routes
from .streamer_auth import StreamerAccess
from .streamer_web import install_streamer_routes
from .viewer_web import install_viewer_routes
from .mini_app_web import install_mini_app_routes
from .payment_web import install_payment_routes
from .growth_site import install_growth_site
from .database import Database

from .twitch import (
    TwitchUnauthorizedError,
    TwitchUserTokenError,
    user_token_request,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"

# moderator:read:followers — число фолловеров канала для итогового отчёта,
# user:read:follows — список подписок пользователя для импорта каналов
SCOPES = "moderator:read:followers user:read:follows"
REDIRECT_PATH = "/twitch/callback"
HEALTH_PATH = "/healthz"

# сколько ждать, что пользователь пройдёт авторизацию по присланной ссылке,
# прежде чем считать попытку истёкшей
AUTH_TIMEOUT_SECONDS = 300
MAX_PENDING_AUTHORIZATIONS = 100
TOKEN_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)
TOKEN_HTTP_MAX_RETRIES = 1
TOKEN_HTTP_MAX_RETRY_DELAY = 2.0


@dataclass
class UserTokenResult:
    login: str
    broadcaster_id: str
    access_token: str
    refresh_token: str
    expires_at: float


class OAuthFlowError(Exception):
    pass


class OAuthTokenTerminalError(OAuthFlowError):
    """Повтор того же refresh request без изменения credentials не поможет."""


class OAuthTokenRevokedError(OAuthTokenTerminalError):
    """Refresh token отозван или больше не действителен."""


class OAuthTokenTemporaryError(OAuthFlowError):
    """Временный 429/5xx/network/timeout token endpoint."""


def _token_retry_delay(attempt: int, headers) -> float:
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            pass
    reset = headers.get("Ratelimit-Reset") if headers is not None else None
    if reset:
        try:
            return max(float(reset) - time.time(), 0.0)
        except (TypeError, ValueError):
            pass
    return min(float(2 ** attempt), TOKEN_HTTP_MAX_RETRY_DELAY)


async def _safe_error_payload(response) -> dict:
    try:
        payload = await response.json()
    except (aiohttp.ClientError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_number(value: object) -> float | None:
    """bool — подкласс int, но осмысленным числом здесь он никогда не является."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate_runtime_health(
    poller: Mapping[str, object] | None,
    eventsub: Mapping[str, object] | None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Готовность процесса для Railway healthcheck по уже собранным in-memory snapshot.

    Осознанно строже Telegram /health в одном и мягче в другом. /health — диагностика
    для владельца, поэтому там degraded даёт любая мелочь: непустой last_cycle_error,
    отставший EventSub, единственный канал, которому нужна re-auth. Здесь ответ
    решает, оставить ли Railway deployment живым, поэтому единственный вопрос —
    работает ли процесс в целом. Один протухший Twitch-аккаунт весь бот не роняет.

    Возвращает (healthy, status) без деталей: наружу уходит только status.
    """
    snapshot_at = time.time() if now is None else now

    if poller is None:
        # Poller ещё не создан: OAuth server стартует раньше него в main().
        return False, "starting"
    if poller.get("stopping"):
        return False, "stopping"
    if not poller.get("running"):
        return False, "degraded"

    stale_after = _as_number(poller.get("stale_after_seconds"))
    if stale_after is None or stale_after <= 0:
        # Без осмысленного порога считать свежесть нечем; молча объявлять
        # процесс здоровым в таком состоянии нельзя.
        return False, "degraded"

    success_age = _as_number(poller.get("last_successful_cycle_age_seconds"))
    if success_age is None:
        last_success_at = _as_number(poller.get("last_successful_cycle_at"))
        if last_success_at is not None:
            success_age = max(0.0, snapshot_at - last_success_at)

    if success_age is None:
        # Первого успешного цикла ещё не было. Даём тот же запас, что и на
        # протухание: иначе Railway убьёт новый deployment раньше первого poll.
        uptime = _as_number(poller.get("uptime_seconds"))
        if uptime is None or uptime <= stale_after:
            return False, "starting"
        return False, "degraded"

    if success_age > stale_after:
        return False, "degraded"

    if eventsub is not None:
        configured = _as_number(eventsub.get("configured_logins")) or 0.0
        ready = _as_number(eventsub.get("ready_logins")) or 0.0
        if eventsub.get("stopping"):
            return False, "stopping"
        # Подсистема EventSub целиком мертва — это глобальный отказ. А вот
        # ready < configured сам по себе нормален: отдельному каналу может
        # требоваться re-auth, пока остальной бот полностью работоспособен.
        if configured > 0 and not eventsub.get("running"):
            return False, "degraded"
        if configured > 0 and ready <= 0:
            return False, "degraded"

    return True, "ok"


class OAuthCallbackServer:
    """Единственный постоянный веб-сервер на весь процесс бота — принимает редиректы
    от Twitch по адресу REDIRECT_PATH. Раньше на каждый /auth_twitch поднимался и
    останавливался отдельный aiohttp-сервер на localhost — это ломалось на любом
    хостинге, где нет доступа к localhost из браузера пользователя (например, Railway),
    и не позволяло два одновременных запроса авторизации от разных людей."""

    def __init__(
        self,
        redirect_uri: str,
        host: str,
        port: int,
        health_provider: Callable[[], tuple[bool, str]] | None = None,
        admin_access: AdminAccess | None = None,
        streamer_access: StreamerAccess | None = None,
        streamer_db: Database | None = None,
        streamer_bot=None,
        viewer_db: Database | None = None,
        viewer_bot_token: str | None = None,
        mini_app_db: Database | None = None,
        mini_app_bot_token: str | None = None,
        mini_app_twitch=None,
        mini_app_bot=None,
        mini_app_bot_username: str = "",
        mini_app_oauth_client_id: str = "",
        mini_app_oauth_client_secret: str = "",
        mini_app_billing_test_enabled: bool = False,
        mini_app_billing_test_user_ids: frozenset[int] = frozenset(),
        growth_bot_username: str | None = None,
        growth_public_base_url: str | None = None,
    ) -> None:
        self.redirect_uri = redirect_uri
        self._host = host
        self._port = port
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._runner: web.AppRunner | None = None
        # Синхронный provider из main(): все нужные snapshot уже лежат в памяти,
        # поэтому /healthz не ходит ни в SQLite, ни в Twitch, ни в Telegram.
        self._health_provider = health_provider
        self._admin_access = admin_access
        self._streamer_access = streamer_access
        self._streamer_db = streamer_db
        self._streamer_bot = streamer_bot
        self._viewer_db = viewer_db
        self._viewer_bot_token = viewer_bot_token
        self._mini_app_db = mini_app_db
        self._mini_app_bot_token = mini_app_bot_token
        self._mini_app_twitch = mini_app_twitch
        self._mini_app_bot = mini_app_bot or streamer_bot
        self._mini_app_bot_username = mini_app_bot_username
        self._mini_app_oauth_client_id = mini_app_oauth_client_id
        self._mini_app_oauth_client_secret = mini_app_oauth_client_secret
        self._mini_app_billing_test_enabled = mini_app_billing_test_enabled
        self._mini_app_billing_test_user_ids = mini_app_billing_test_user_ids
        self._preview_observer = None
        self._mini_app_connect_tasks: dict[int, asyncio.Task] = {}
        self._growth_bot_username = growth_bot_username
        self._growth_public_base_url = growth_public_base_url
        self._admin_snapshot_provider: SnapshotProvider | None = None

    def set_admin_snapshot_provider(self, provider: SnapshotProvider | None) -> None:
        self._admin_snapshot_provider = provider

    def set_preview_observer(self, observer) -> None:
        self._preview_observer = observer

    def _mini_app_preview_status(self, login: str) -> str:
        observer = self._preview_observer
        if observer is None or not hasattr(observer, "photo_delivery_status"):
            return "unknown"
        return observer.photo_delivery_status(login)

    def set_health_provider(
        self, provider: Callable[[], tuple[bool, str]] | None
    ) -> None:
        """Позволяет main() отдать provider после создания poller.

        Сервер поднимается раньше runtime, поэтому провайдер появляется позже —
        до этого /healthz честно отвечает starting, а не выдумывает готовность."""
        self._health_provider = provider

    async def start(self) -> None:
        app = web.Application()
        # No monetary provider or callback is enabled for this first release.
        install_payment_routes(app)
        app.router.add_get(REDIRECT_PATH, self._handle_callback)
        app.router.add_get(HEALTH_PATH, self._handle_health)
        if self._admin_access is not None:
            async def _snapshot() -> dict:
                provider = self._admin_snapshot_provider
                if provider is None:
                    raise RuntimeError("Admin snapshot is not ready")
                return await provider()

            install_admin_routes(app, self._admin_access, _snapshot)
        if self._streamer_access is not None and self._streamer_db is not None:
            install_streamer_routes(app, self._streamer_access, self._streamer_db, self._streamer_bot)
        if self._viewer_db is not None and self._viewer_bot_token is not None:
            install_viewer_routes(app, self._viewer_db, self._viewer_bot_token)
        if self._mini_app_db is not None and self._mini_app_bot_token is not None:
            install_mini_app_routes(
                app, self._mini_app_db, self._mini_app_bot_token,
                bot=self._mini_app_bot, twitch=self._mini_app_twitch,
                bot_username=self._mini_app_bot_username,
                oauth_server=self,
                billing_test_enabled=self._mini_app_billing_test_enabled,
                billing_test_user_ids=self._mini_app_billing_test_user_ids,
                preview_status_provider=self._mini_app_preview_status,
            )
        if self._growth_bot_username is not None:
            install_growth_site(app, self._growth_bot_username, self._growth_public_base_url)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("OAuth callback-сервер слушает на %s:%s", self._host, self._port)

    async def stop(self) -> None:
        for task in self._mini_app_connect_tasks.values():
            task.cancel()
        if self._mini_app_connect_tasks:
            await asyncio.gather(*self._mini_app_connect_tasks.values(), return_exceptions=True)
        self._mini_app_connect_tasks.clear()
        for state in list(self._pending):
            self.discard_state(state)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def health_snapshot(self) -> dict[str, int | bool]:
        """Безопасный aggregate snapshot; OAuth state values не раскрываются."""
        return {
            "runner_started": self._runner is not None,
            "pending_states": len(self._pending),
        }

    def register_state(self, state: str) -> None:
        """Регистрирует OAuth state до того, как ссылка станет видна пользователю."""
        if state in self._pending:
            raise OAuthFlowError("Повторный OAuth state")
        if len(self._pending) >= MAX_PENDING_AUTHORIZATIONS:
            raise OAuthFlowError("Слишком много одновременных попыток авторизации")
        self._pending[state] = asyncio.get_running_loop().create_future()

    async def create_streamer_connect_intent(self, telegram_user_id: int) -> tuple[str, str, float]:
        db = self._mini_app_db
        if db is None or not self._mini_app_oauth_client_id or not self._mini_app_oauth_client_secret:
            raise OAuthFlowError("Mini App Twitch connection is unavailable")
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid streamer user")
        old = self._mini_app_connect_tasks.get(telegram_user_id)
        if old is not None and not old.done():
            raise OAuthFlowError("Twitch connection already pending")
        state = secrets.token_urlsafe(24)
        intent_id = secrets.token_urlsafe(18)
        now = time.time()
        await db.create_streamer_connect_intent(
            intent_id, telegram_user_id, hashlib.sha256(state.encode()).hexdigest(),
            now=now,
        )
        try:
            self.register_state(state)
        except Exception:
            await db.finish_streamer_connect_intent(intent_id, "failed")
            raise
        task = asyncio.create_task(
            self._finish_streamer_connect_intent(telegram_user_id, intent_id, state),
            name="mini-app-streamer-oauth",
        )
        self._mini_app_connect_tasks[telegram_user_id] = task
        return intent_id, build_authorize_url(
            self._mini_app_oauth_client_id, self.redirect_uri, state,
        ), now + 600

    async def _finish_streamer_connect_intent(
        self, telegram_user_id: int, intent_id: str, state: str,
    ) -> None:
        db = self._mini_app_db
        try:
            code = await self.wait_for_code(state, timeout=600)
            if not await db.claim_streamer_connect_intent(
                intent_id, telegram_user_id, now=time.time(),
            ):
                return
            async with aiohttp.ClientSession() as session:
                result = await _exchange_code(
                    self._mini_app_oauth_client_id,
                    self._mini_app_oauth_client_secret,
                    code, self.redirect_uri, session,
                )
            saved = await db.save_verified_streamer_connection(
                telegram_user_id, result, verified_at=time.time(),
            )
            await db.finish_streamer_connect_intent(
                intent_id, "connected" if saved else "conflict",
                twitch_login=result.login if saved else None,
            )
        except asyncio.CancelledError:
            await db.finish_streamer_connect_intent(intent_id, "cancelled")
        except Exception:
            logger.exception("Mini App Twitch connection failed")
            await db.finish_streamer_connect_intent(intent_id, "failed")
        finally:
            self.discard_state(state)
            if self._mini_app_connect_tasks.get(telegram_user_id) is asyncio.current_task():
                self._mini_app_connect_tasks.pop(telegram_user_id, None)

    async def cancel_streamer_connect_intent(self, telegram_user_id: int, intent_id: str) -> bool:
        db = self._mini_app_db
        row = await db.get_streamer_connect_intent(intent_id)
        if row is None or row[1] != telegram_user_id or row[4] != "pending":
            return False
        task = self._mini_app_connect_tasks.get(telegram_user_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await db.finish_streamer_connect_intent(intent_id, "cancelled")
        final = await db.get_streamer_connect_intent(intent_id)
        return final is not None and final[4] == "cancelled"

    def discard_state(self, state: str) -> None:
        future = self._pending.pop(state, None)
        if future is not None and not future.done():
            future.cancel()

    async def wait_for_code(self, state: str, timeout: int = AUTH_TIMEOUT_SECONDS) -> str:
        future = self._pending.get(state)
        if future is None:
            # Сохраняем совместимость для прямых вызовов метода, но основной flow
            # регистрирует state заранее, до отправки ссылки в Telegram.
            self.register_state(state)
            future = self._pending[state]
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise OAuthFlowError("Истекло время ожидания авторизации") from e
        finally:
            self._pending.pop(state, None)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Read-only: только чтение in-memory snapshot, без DB/сети и без мутаций."""
        provider = self._health_provider
        if provider is None:
            healthy, status = False, "starting"
        else:
            try:
                healthy, status = provider()
            except Exception:
                # Сломанный provider — это отказ, но /healthz обязан ответить
                # кодом, а не 500 со стектрейсом наружу.
                logger.exception("Не удалось собрать health snapshot")
                healthy, status = False, "degraded"
        # Наружу уходит только status: ни логинов, ни chat_id, ни путей, ни ошибок.
        return web.json_response(
            {"status": status},
            status=200 if healthy else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state")
        future = self._pending.get(state or "")
        if future is None or future.done():
            return web.Response(text="Ссылка авторизации недействительна или уже использована.", status=400)

        error = request.query.get("error")
        if error:
            future.set_exception(OAuthFlowError(f"Twitch вернул ошибку: {error}"))
            return web.Response(text=f"Авторизация отклонена: {error}", status=400)

        code = request.query.get("code")
        if not code:
            future.set_exception(OAuthFlowError("В ответе Twitch нет code"))
            return web.Response(text="Ошибка: отсутствует code.", status=400)

        future.set_result(code)
        return web.Response(
            text="Авторизация прошла успешно! Можно закрыть эту вкладку и вернуться в Telegram.",
            content_type="text/html",
        )


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    # scope из нескольких разрешений разделяется пробелом — его обязательно кодировать,
    # иначе Twitch получит обрезанную ссылку и вернёт ошибку
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def run_authorization_flow(
    client_id: str,
    client_secret: str,
    session: aiohttp.ClientSession,
    callback_server: OAuthCallbackServer,
    on_url_ready=None,
) -> UserTokenResult:
    """Ждёт, пока пользователь пройдёт авторизацию через уже запущенный
    callback_server, и обменивает полученный code на токен."""
    state = secrets.token_urlsafe(16)
    auth_url = build_authorize_url(client_id, callback_server.redirect_uri, state)
    # ссылка содержит одноразовый state — в обычные логи её писать незачем
    logger.debug("Ссылка авторизации Twitch сформирована для state=%s", state[:6])
    callback_server.register_state(state)
    try:
        if on_url_ready is not None:
            await on_url_ready(auth_url)
        code = await callback_server.wait_for_code(state)
    finally:
        # wait_for_code удаляет запись само; этот вызов нужен, если отправка ссылки
        # упала раньше ожидания, чтобы не держать Future до завершения процесса.
        callback_server.discard_state(state)
    return await _exchange_code(client_id, client_secret, code, callback_server.redirect_uri, session)


async def _exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str, session: aiohttp.ClientSession
) -> UserTokenResult:
    async with session.post(
        TOKEN_URL,
        # Twitch ожидает x-www-form-urlencoded body. Секреты не должны
        # попадать в URL, который HTTP-клиенты часто включают в exception/log.
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=TOKEN_HTTP_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise OAuthFlowError(f"Twitch token exchange вернул HTTP {resp.status}")
        try:
            data = await resp.json()
        except (aiohttp.ClientError, TypeError, ValueError) as e:
            raise OAuthFlowError("Twitch token exchange вернул некорректный JSON") from e
    if not isinstance(data, dict):
        raise OAuthTokenTemporaryError(
            "Twitch token exchange вернул некорректный payload"
        )

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthFlowError("Twitch token exchange не вернул access token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise OAuthFlowError("Twitch token exchange не вернул refresh token")
    expires_in = data.get("expires_in", 3600)
    if not isinstance(expires_in, (int, float)) or expires_in <= 60:
        raise OAuthTokenTemporaryError(
            "Twitch token exchange вернул некорректный expires_in"
        )
    expires_at = time.time() + expires_in - 60

    try:
        validation = await user_token_request(
            session,
            client_id,
            "GET",
            USERS_URL,
            access_token,
        )
    except TwitchUnauthorizedError as e:
        raise OAuthFlowError("Twitch отклонил только что выданный access token") from e
    except TwitchUserTokenError as e:
        raise OAuthTokenTemporaryError(
            "Не удалось временно проверить Twitch access token"
        ) from e
    users = validation.get("data", [])
    if not isinstance(users, list):
        raise OAuthTokenTemporaryError(
            "Twitch user validation вернул некорректный payload"
        )

    if not users:
        raise OAuthFlowError("Не удалось получить данные пользователя Twitch")

    user = users[0]
    if not isinstance(user, dict):
        raise OAuthTokenTemporaryError(
            "Twitch user validation вернул некорректного пользователя"
        )
    login = user.get("login")
    broadcaster_id = user.get("id")
    if (
        not isinstance(login, str)
        or not login
        or not isinstance(broadcaster_id, str)
        or not broadcaster_id
    ):
        raise OAuthTokenTemporaryError(
            "Twitch user validation не вернул login/id"
        )
    return UserTokenResult(
        login=login.lower(),
        broadcaster_id=broadcaster_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


async def refresh_user_token(
    client_id: str, client_secret: str, refresh_token: str, session: aiohttp.ClientSession
) -> tuple[str, str, float]:
    """Обновляет user token с bounded retry, не раскрывая секреты в исключениях."""
    for attempt in range(TOKEN_HTTP_MAX_RETRIES + 1):
        try:
            async with session.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=TOKEN_HTTP_TIMEOUT,
            ) as response:
                if response.status == 429:
                    delay = _token_retry_delay(attempt, response.headers)
                    if (
                        attempt < TOKEN_HTTP_MAX_RETRIES
                        and delay <= TOKEN_HTTP_MAX_RETRY_DELAY
                    ):
                        logger.warning(
                            "Twitch token refresh ограничен по частоте; повтор через %.1fс",
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh временно ограничен по частоте"
                    )
                if response.status >= 500:
                    if attempt < TOKEN_HTTP_MAX_RETRIES:
                        delay = min(float(2 ** attempt), TOKEN_HTTP_MAX_RETRY_DELAY)
                        logger.warning(
                            "Twitch token refresh временно недоступен; повтор через %.1fс",
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise OAuthTokenTemporaryError(
                        f"Twitch token refresh временно недоступен: HTTP {response.status}"
                    )
                if response.status != 200:
                    payload = await _safe_error_payload(response)
                    error_code = str(payload.get("error", "")).lower()
                    message = str(payload.get("message", "")).lower()
                    if error_code == "invalid_grant" or "refresh token" in message:
                        raise OAuthTokenRevokedError(
                            "Twitch refresh token отозван или недействителен"
                        )
                    raise OAuthTokenTerminalError(
                        f"Twitch token refresh вернул HTTP {response.status}"
                    )
                try:
                    data = await response.json()
                except (aiohttp.ClientError, TypeError, ValueError) as e:
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh вернул некорректный JSON"
                    ) from e
                if not isinstance(data, dict):
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh вернул некорректный payload"
                    )
                new_access_token = data.get("access_token")
                new_refresh_token = data.get("refresh_token")
                expires_in = data.get("expires_in", 3600)
                if not isinstance(new_access_token, str) or not new_access_token:
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh не вернул access token"
                    )
                if not isinstance(new_refresh_token, str) or not new_refresh_token:
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh не вернул rotated refresh token"
                    )
                if not isinstance(expires_in, (int, float)) or expires_in <= 60:
                    raise OAuthTokenTemporaryError(
                        "Twitch token refresh вернул некорректный expires_in"
                    )
                expires_at = time.time() + expires_in - 60
                return new_access_token, new_refresh_token, expires_at
        except OAuthFlowError:
            raise
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            if attempt >= TOKEN_HTTP_MAX_RETRIES:
                raise OAuthTokenTemporaryError(
                    "Twitch token refresh временно недоступен"
                ) from e
            delay = min(float(2 ** attempt), TOKEN_HTTP_MAX_RETRY_DELAY)
            logger.warning(
                "Сеть недоступна для Twitch token refresh; повтор через %.1fс", delay
            )
            await asyncio.sleep(delay)

    raise OAuthTokenTemporaryError("Twitch token refresh не завершён")
