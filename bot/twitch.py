from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

# суммарный таймаут запроса к Twitch: цикл опроса должен уложиться в свой интервал,
# зависший запрос не имеет права держать весь бот
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)
HTTP_MAX_RETRIES = 2
HTTP_RETRY_BASE_DELAY = 1.0


def _retry_delay(attempt: int, ratelimit_reset: str | None) -> float:
    """Пауза перед повтором: слушаем подсказку Twitch, если она есть, иначе растём
    экспоненциально. Небольшой случайный разброс разводит повторы по разным каналам,
    чтобы они не били в API одной волной."""
    if ratelimit_reset:
        try:
            wait = float(ratelimit_reset) - time.time()
            if 0 < wait <= 30:
                return wait + random.uniform(0, 0.5)
        except ValueError:
            pass
    return HTTP_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
STREAMS_URL = "https://api.twitch.tv/helix/streams"
USERS_URL = "https://api.twitch.tv/helix/users"
FOLLOWERS_URL = "https://api.twitch.tv/helix/channels/followers"
CLIPS_URL = "https://api.twitch.tv/helix/clips"
VIDEOS_URL = "https://api.twitch.tv/helix/videos"
SEARCH_CHANNELS_URL = "https://api.twitch.tv/helix/search/channels"
FOLLOWED_URL = "https://api.twitch.tv/helix/channels/followed"

# сколько топ-клипов показывать в отчёте
TOP_CLIPS_COUNT = 3

# Helix позволяет запрашивать до 100 логинов за один вызов /streams
MAX_LOGINS_PER_REQUEST = 100


@dataclass
class StreamInfo:
    user_login: str
    stream_id: str
    title: str
    game_name: str
    viewer_count: int
    started_at: str


@dataclass
class ChannelSearchResult:
    login: str
    display_name: str
    is_live: bool


@dataclass
class ClipInfo:
    id: str
    url: str
    title: str
    view_count: int
    thumbnail_url: str
    creator_name: str
    created_at: str


class TwitchClient:
    def __init__(self, client_id: str, client_secret: str, session: aiohttp.ClientSession) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with self._session.post(
            TOKEN_URL,
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            timeout=HTTP_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._token = data["access_token"]
        # обновим чуть раньше реального истечения, с запасом в 60 секунд
        self._token_expires_at = time.monotonic() + max(data.get("expires_in", 3600) - 60, 60)
        return self._token

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {"Client-Id": self._client_id, "Authorization": f"Bearer {token}"}

    async def _request(self, url: str, params: list[tuple[str, str]]) -> dict:
        """Запрос к Helix с ретраями.

        Без явного таймаута зависший ответ Twitch держал бы весь цикл опроса
        (по умолчанию aiohttp ждёт 5 минут), а разовые 429/5xx роняли бы обработку
        всех каналов разом — поэтому здесь ограниченное число повторов с паузой."""
        headers = await self._headers()
        last_error: Exception | None = None

        for attempt in range(HTTP_MAX_RETRIES + 1):
            try:
                async with self._session.get(
                    url, headers=headers, params=params, timeout=HTTP_TIMEOUT
                ) as resp:
                    if resp.status == 401:
                        # токен мог быть отозван — обновляем и повторяем сразу
                        self._token = None
                        headers = await self._headers()
                        continue
                    if resp.status == 429 or resp.status >= 500:
                        last_error = aiohttp.ClientResponseError(
                            resp.request_info, resp.history,
                            status=resp.status, message=resp.reason or "",
                        )
                        if attempt < HTTP_MAX_RETRIES:
                            delay = _retry_delay(attempt, resp.headers.get("Ratelimit-Reset"))
                            logger.warning(
                                "Twitch ответил %s на %s, повтор через %.1fс",
                                resp.status, url, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise last_error
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt >= HTTP_MAX_RETRIES:
                    raise
                delay = HTTP_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Сеть недоступна при запросе к %s (%s), повтор через %.1fс", url, e, delay)
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Не удалось выполнить запрос к {url}")

    async def get_live_streams(self, logins: list[str]) -> dict[str, StreamInfo]:
        """Вернёт {twitch_login: StreamInfo} только для тех каналов, что сейчас в эфире."""
        result: dict[str, StreamInfo] = {}
        if not logins:
            return result

        for i in range(0, len(logins), MAX_LOGINS_PER_REQUEST):
            chunk = logins[i : i + MAX_LOGINS_PER_REQUEST]
            params = [("user_login", login) for login in chunk]
            data = await self._request(STREAMS_URL, params)
            for item in data.get("data", []):
                login = item["user_login"].lower()
                result[login] = StreamInfo(
                    user_login=login,
                    stream_id=item["id"],
                    title=item.get("title", ""),
                    game_name=item.get("game_name", "—"),
                    viewer_count=item.get("viewer_count", 0),
                    started_at=item.get("started_at", ""),
                )
        return result

    async def channel_exists(self, login: str) -> bool:
        data = await self._request(USERS_URL, [("login", login)])
        return len(data.get("data", [])) > 0

    async def get_existing_logins(self, logins: list[str]) -> set[str]:
        """Вернёт подмножество logins, реально существующих на Twitch (не забанены/не
        удалены) — одним батч-запросом на каждые 100 логинов, а не по одному."""
        if not logins:
            return set()
        result: set[str] = set()
        for i in range(0, len(logins), MAX_LOGINS_PER_REQUEST):
            chunk = logins[i : i + MAX_LOGINS_PER_REQUEST]
            params = [("login", login) for login in chunk]
            data = await self._request(USERS_URL, params)
            result.update(item["login"].lower() for item in data.get("data", []))
        return result

    async def search_channels(self, query: str, limit: int = 6) -> list[ChannelSearchResult]:
        """Поиск каналов по части имени — позволяет добавить канал, не зная точного
        логина (Twitch ищет и по отображаемому имени, в том числе на кириллице)."""
        data = await self._request(
            SEARCH_CHANNELS_URL, [("query", query), ("first", str(limit))]
        )
        return [
            ChannelSearchResult(
                login=item["broadcaster_login"].lower(),
                display_name=item.get("display_name") or item["broadcaster_login"],
                is_live=bool(item.get("is_live")),
            )
            for item in data.get("data", [])
            if item.get("broadcaster_login")
        ]

    async def get_followed_channels(self, user_id: str, user_access_token: str) -> list[str]:
        """Логины каналов, на которые подписан пользователь. Требует пользовательский
        токен со scope user:read:follows — client-credentials здесь не подходит."""
        headers = {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {user_access_token}",
        }
        logins: list[str] = []
        cursor: str | None = None
        while True:
            params = [("user_id", user_id), ("first", "100")]
            if cursor:
                params.append(("after", cursor))
            async with self._session.get(
                FOLLOWED_URL, headers=headers, params=params, timeout=HTTP_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
            logins.extend(
                item["broadcaster_login"].lower()
                for item in data.get("data", [])
                if item.get("broadcaster_login")
            )
            cursor = data.get("pagination", {}).get("cursor")
            if not cursor:
                return logins

    async def get_user_id(self, login: str) -> str | None:
        data = await self._request(USERS_URL, [("login", login)])
        items = data.get("data", [])
        return items[0]["id"] if items else None

    async def get_display_names(self, logins: list[str]) -> dict[str, str]:
        """Вернёт {twitch_login: display_name} — отображаемое имя (с учётом регистра/кириллицы)
        для каждого логина, батчем по 100 за запрос."""
        result: dict[str, str] = {}
        if not logins:
            return result
        for i in range(0, len(logins), MAX_LOGINS_PER_REQUEST):
            chunk = logins[i : i + MAX_LOGINS_PER_REQUEST]
            params = [("login", login) for login in chunk]
            data = await self._request(USERS_URL, params)
            for item in data.get("data", []):
                result[item["login"].lower()] = item.get("display_name", item["login"])
        return result

    async def get_top_clips(
        self, broadcaster_id: str, started_at: str, ended_at: str, count: int = TOP_CLIPS_COUNT
    ) -> list[ClipInfo]:
        """Топ-клипы канала за период [started_at, ended_at) (RFC3339), по просмотрам —
        Helix уже возвращает clips отсортированными по view_count при таком запросе."""
        params = [
            ("broadcaster_id", broadcaster_id),
            ("started_at", started_at),
            ("ended_at", ended_at),
            ("first", str(count)),
        ]
        data = await self._request(CLIPS_URL, params)
        return [
            ClipInfo(
                id=item["id"],
                url=item["url"],
                title=item.get("title", ""),
                view_count=item.get("view_count", 0),
                thumbnail_url=item.get("thumbnail_url", ""),
                creator_name=item.get("creator_name", ""),
                created_at=item.get("created_at", ""),
            )
            for item in data.get("data", [])
        ]

    async def get_latest_vod_url(self, broadcaster_id: str, stream_id: str) -> str | None:
        """Ищет VOD только что завершённого стрима среди последних архивных видео канала.
        Если запись стрима не включена, у канала может не быть недавних видео — тогда None."""
        params = [
            ("user_id", broadcaster_id),
            ("type", "archive"),
            ("first", "5"),
        ]
        data = await self._request(VIDEOS_URL, params)
        for item in data.get("data", []):
            if item.get("stream_id") == stream_id:
                return item.get("url")
        return None

    async def get_followers_count(self, broadcaster_id: str, user_access_token: str) -> int:
        """Требует user access token с правом moderator:read:followers (self-moderation тоже подходит)."""
        headers = {"Client-Id": self._client_id, "Authorization": f"Bearer {user_access_token}"}
        async with self._session.get(
            FOLLOWERS_URL,
            headers=headers,
            params={"broadcaster_id": broadcaster_id, "first": "1"},
            timeout=HTTP_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("total", 0)
