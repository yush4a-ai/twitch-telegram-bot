from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiohttp

from .database import Database
from .oauth import OAuthTokenTerminalError, refresh_user_token
from .twitch import TwitchAuthError, TwitchUnauthorizedError


T = TypeVar("T")


class TokenStore:
    def __init__(self, db: Database, client_id: str, client_secret: str, session: aiohttp.ClientSession) -> None:
        self._db = db
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session
        # поллер и FollowEventListener независимо дёргают get_valid_token для одного
        # и того же канала — без блокировки на канал оба могут прочитать один и тот же
        # протухший refresh_token и попытаться обновить его одновременно. Twitch
        # ротирует refresh-токены при использовании, поэтому второй вызов получил бы
        # invalid_grant вместо валидного токена
        self._refresh_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Не повторяем заведомо terminal invalid_grant каждый poll. Значение — тот
        # refresh token, который Twitch отверг; новая ручная авторизация запишет другой
        # token, и marker автоматически перестанет действовать.
        self._terminal_refresh_tokens: dict[str, str] = {}
        # Если Twitch уже ротировал refresh token, а SQLite commit временно упал,
        # нельзя снова использовать старую lineage. Держим новую пару в памяти и
        # сначала повторяем persistence; access token до успешного save не выдаётся.
        self._pending_persistence: dict[
            str, tuple[str, str, str, str, float]
        ] = {}

    def health_snapshot(self) -> dict[str, int]:
        """Только aggregate marker counts; token values наружу не попадают."""
        return {"auth_blocked_logins": len(self._terminal_refresh_tokens)}

    def _is_terminal_token(self, twitch_login: str, refresh_token: str) -> bool:
        rejected = self._terminal_refresh_tokens.get(twitch_login)
        if rejected is None:
            return False
        if rejected == refresh_token:
            return True
        self._terminal_refresh_tokens.pop(twitch_login, None)
        return False

    async def _persist_pending_locked(
        self, twitch_login: str
    ) -> tuple[str, str] | None:
        pending = self._pending_persistence.get(twitch_login)
        if pending is None:
            return None
        (
            source_refresh_token,
            broadcaster_id,
            access_token,
            refresh_token,
            expires_at,
        ) = pending
        current = await self._db.get_user_token(twitch_login)
        if current is None or current[2] != source_refresh_token:
            # Ручная re-auth или другой процесс уже записал более новую lineage.
            self._pending_persistence.pop(twitch_login, None)
            return None
        await self._db.save_user_token(
            twitch_login,
            broadcaster_id,
            access_token,
            refresh_token,
            expires_at,
        )
        self._pending_persistence.pop(twitch_login, None)
        self._terminal_refresh_tokens.pop(twitch_login, None)
        return broadcaster_id, access_token

    async def _refresh_locked(
        self,
        twitch_login: str,
        *,
        rejected_access_token: str | None = None,
    ) -> tuple[str, str] | None:
        row = await self._db.get_user_token(twitch_login)
        if row is None:
            return None
        broadcaster_id, access_token, refresh_token, expires_at = row
        if self._is_terminal_token(twitch_login, refresh_token):
            return None

        # Другой waiter уже заменил отвергнутый access token и сохранил rotated
        # refresh token. Повторно ходить в token endpoint нельзя.
        if rejected_access_token is not None and access_token != rejected_access_token:
            return broadcaster_id, access_token
        if rejected_access_token is None and time.time() < expires_at:
            return broadcaster_id, access_token

        try:
            new_access_token, new_refresh_token, new_expires_at = await refresh_user_token(
                self._client_id, self._client_secret, refresh_token, self._session
            )
        except OAuthTokenTerminalError:
            self._terminal_refresh_tokens[twitch_login] = refresh_token
            raise

        # Не отдаём новый access token caller-у до успешного атомарного сохранения
        # rotated refresh token. При ошибке DB исключение выйдет наружу, а caller не
        # начнёт работу с неперсистентной refresh lineage.
        try:
            await self._db.save_user_token(
                twitch_login,
                broadcaster_id,
                new_access_token,
                new_refresh_token,
                new_expires_at,
            )
        except BaseException:
            self._pending_persistence[twitch_login] = (
                refresh_token,
                broadcaster_id,
                new_access_token,
                new_refresh_token,
                new_expires_at,
            )
            raise
        self._terminal_refresh_tokens.pop(twitch_login, None)
        return broadcaster_id, new_access_token

    async def get_valid_token(self, twitch_login: str) -> tuple[str, str] | None:
        """Вернёт (broadcaster_id, access_token), обновив токен при необходимости."""
        if twitch_login in self._pending_persistence:
            async with self._refresh_locks[twitch_login]:
                persisted = await self._persist_pending_locked(twitch_login)
                if persisted is not None:
                    return persisted
        row = await self._db.get_user_token(twitch_login)
        if row is None:
            return None

        broadcaster_id, access_token, refresh_token, expires_at = row
        if self._is_terminal_token(twitch_login, refresh_token):
            return None
        if time.time() < expires_at:
            return broadcaster_id, access_token

        async with self._refresh_locks[twitch_login]:
            # пока ждали лок, конкурентный вызов мог уже обновить токен — перечитываем,
            # чтобы не отправить в Twitch уже использованный (и отозванный) refresh_token
            return await self._refresh_locked(twitch_login)

    async def refresh_after_unauthorized(
        self, twitch_login: str, rejected_access_token: str
    ) -> tuple[str, str] | None:
        """Force-refresh ровно один раз для access token, реально получившего 401."""
        async with self._refresh_locks[twitch_login]:
            return await self._refresh_locked(
                twitch_login, rejected_access_token=rejected_access_token
            )

    async def mark_current_token_terminal(
        self, twitch_login: str, rejected_access_token: str
    ) -> None:
        """Не даёт следующему poll снова refresh-ить token после второго 401."""
        async with self._refresh_locks[twitch_login]:
            row = await self._db.get_user_token(twitch_login)
            if row is not None and row[1] == rejected_access_token:
                self._terminal_refresh_tokens[twitch_login] = row[2]

    async def execute_with_token(
        self,
        twitch_login: str,
        operation: Callable[[str, str], Awaitable[T]],
    ) -> T:
        """Выполняет user-token operation с максимум одним refresh после 401."""
        token = await self.get_valid_token(twitch_login)
        if token is None:
            raise TwitchAuthError(
                f"Для Twitch-канала {twitch_login} требуется повторная авторизация"
            )
        broadcaster_id, access_token = token
        try:
            return await operation(broadcaster_id, access_token)
        except TwitchUnauthorizedError:
            refreshed = await self.refresh_after_unauthorized(
                twitch_login, access_token
            )
            if refreshed is None:
                raise TwitchAuthError(
                    f"Для Twitch-канала {twitch_login} требуется повторная авторизация"
                )
            broadcaster_id, access_token = refreshed
            try:
                return await operation(broadcaster_id, access_token)
            except TwitchUnauthorizedError as e:
                await self.mark_current_token_terminal(
                    twitch_login, access_token
                )
                raise TwitchAuthError(
                    f"Twitch повторно отклонил токен для {twitch_login} после refresh"
                ) from e
