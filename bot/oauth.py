from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

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


class OAuthCallbackServer:
    """Единственный постоянный веб-сервер на весь процесс бота — принимает редиректы
    от Twitch по адресу REDIRECT_PATH. Раньше на каждый /auth_twitch поднимался и
    останавливался отдельный aiohttp-сервер на localhost — это ломалось на любом
    хостинге, где нет доступа к localhost из браузера пользователя (например, Railway),
    и не позволяло два одновременных запроса авторизации от разных людей."""

    def __init__(self, redirect_uri: str, host: str, port: int) -> None:
        self.redirect_uri = redirect_uri
        self._host = host
        self._port = port
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get(REDIRECT_PATH, self._handle_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info("OAuth callback-сервер слушает на %s:%s", self._host, self._port)

    async def stop(self) -> None:
        for state in list(self._pending):
            self.discard_state(state)
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def register_state(self, state: str) -> None:
        """Регистрирует OAuth state до того, как ссылка станет видна пользователю."""
        if state in self._pending:
            raise OAuthFlowError("Повторный OAuth state")
        if len(self._pending) >= MAX_PENDING_AUTHORIZATIONS:
            raise OAuthFlowError("Слишком много одновременных попыток авторизации")
        self._pending[state] = asyncio.get_running_loop().create_future()

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
        params={
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
                params={
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
