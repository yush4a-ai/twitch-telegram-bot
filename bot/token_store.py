from __future__ import annotations

import time

import aiohttp

from .database import Database
from .oauth import refresh_user_token


class TokenStore:
    def __init__(self, db: Database, client_id: str, client_secret: str, session: aiohttp.ClientSession) -> None:
        self._db = db
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session

    async def get_valid_token(self, twitch_login: str) -> tuple[str, str] | None:
        """Вернёт (broadcaster_id, access_token), обновив токен при необходимости."""
        row = await self._db.get_user_token(twitch_login)
        if row is None:
            return None

        broadcaster_id, access_token, refresh_token, expires_at = row
        if time.time() < expires_at:
            return broadcaster_id, access_token

        new_access_token, new_refresh_token, new_expires_at = await refresh_user_token(
            self._client_id, self._client_secret, refresh_token, self._session
        )
        await self._db.save_user_token(
            twitch_login, broadcaster_id, new_access_token, new_refresh_token, new_expires_at
        )
        return broadcaster_id, new_access_token
