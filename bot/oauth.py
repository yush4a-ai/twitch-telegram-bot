from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

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


@dataclass
class UserTokenResult:
    login: str
    broadcaster_id: str
    access_token: str
    refresh_token: str
    expires_at: float


class OAuthFlowError(Exception):
    pass


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
        if self._runner is not None:
            await self._runner.cleanup()

    async def wait_for_code(self, state: str, timeout: int = AUTH_TIMEOUT_SECONDS) -> str:
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[state] = future
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
    logger.info("Ссылка авторизации Twitch: %s", auth_url)
    if on_url_ready is not None:
        await on_url_ready(auth_url)

    code = await callback_server.wait_for_code(state)
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
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()

    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    expires_at = time.time() + data.get("expires_in", 3600) - 60

    async with session.get(
        USERS_URL,
        headers={"Client-Id": client_id, "Authorization": f"Bearer {access_token}"},
    ) as resp:
        resp.raise_for_status()
        users = (await resp.json())["data"]

    if not users:
        raise OAuthFlowError("Не удалось получить данные пользователя Twitch")

    user = users[0]
    return UserTokenResult(
        login=user["login"].lower(),
        broadcaster_id=user["id"],
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )


async def refresh_user_token(
    client_id: str, client_secret: str, refresh_token: str, session: aiohttp.ClientSession
) -> tuple[str, str, float]:
    """Вернёт (access_token, refresh_token, expires_at)."""
    async with session.post(
        TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    ) as resp:
        resp.raise_for_status()
        data = await resp.json()

    expires_at = time.time() + data.get("expires_in", 3600) - 60
    return data["access_token"], data["refresh_token"], expires_at
