from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone

import aiohttp

from .database import Database
from .oauth import OAuthTokenTerminalError
from .token_store import TokenStore
from .twitch import TwitchAuthError, user_token_request


logger = logging.getLogger(__name__)

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
EVENTSUB_SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"
EVENTSUB_KEEPALIVE_GRACE_SECONDS = 10


class FollowEventListener:
    """Слушает точные события channel.follow для подключённых Twitch-аккаунтов."""

    def __init__(
        self,
        db: Database,
        token_store: TokenStore,
        client_id: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self._db = db
        self._token_store = token_store
        self._client_id = client_id
        self._session = session
        self._tasks: dict[str, asyncio.Task] = {}
        self._ready: dict[str, bool] = {}
        self._ready_since: dict[str, float] = {}
        self._last_message_at: dict[str, float] = {}
        self._first_attempt: set[str] = set()
        self._stop_event = asyncio.Event()
        self._running = False
        self._last_error: str | None = None
        self._last_error_at: float | None = None

    def is_ready(self, twitch_login: str) -> bool:
        return self._ready.get(twitch_login.lower(), False)

    def is_configured(self, twitch_login: str) -> bool:
        return twitch_login.lower() in self._tasks

    def covers_stream_start(self, twitch_login: str, started_at: str) -> bool:
        """True, только если подписка работала уже в момент начала стрима."""
        ready_since = self._ready_since.get(twitch_login.lower())
        if ready_since is None:
            return False
        try:
            stream_start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except ValueError:
            return False
        return ready_since <= stream_start

    async def wait_initial_ready(self, timeout: float = 8.0) -> None:
        """Даёт первоначальным подпискам короткое окно подняться до первого опроса."""
        logins = {login.lower() for login in await self._db.all_token_logins()}
        if not logins:
            return

        async def _wait() -> None:
            while not logins.issubset(self._first_attempt):
                await asyncio.sleep(0.1)

        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_wait(), timeout)

    async def run(self) -> None:
        self._running = True
        try:
            while not self._stop_event.is_set():
                await self._sync_channels()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
        finally:
            try:
                await self.stop()
            finally:
                self._running = False

    def health_snapshot(self, now: float | None = None) -> dict[str, object]:
        """Aggregate EventSub state без логинов, токенов и per-channel строк."""
        snapshot_at = time.time() if now is None else now
        ready_logins = {login for login, ready in self._ready.items() if ready}
        ready_message_times = [
            self._last_message_at[login]
            for login in ready_logins
            if login in self._last_message_at
        ]
        return {
            "running": self._running,
            "stopping": self._stop_event.is_set(),
            "configured_logins": len(self._tasks),
            "ready_logins": len(ready_logins),
            "stalest_message_age_seconds": (
                max(0.0, snapshot_at - min(ready_message_times))
                if ready_message_times
                else None
            ),
            # Только класс исключения: текст внешней ошибки теоретически может
            # содержать URL или credential и не должен попадать в Telegram.
            "last_error": self._last_error,
            "last_error_age_seconds": (
                max(0.0, snapshot_at - self._last_error_at)
                if self._last_error_at is not None
                else None
            ),
        }

    async def _sync_channels(self) -> None:
        for raw_login in await self._db.all_token_logins():
            login = raw_login.lower()
            task = self._tasks.get(login)
            if task is None or task.done():
                if task is not None and not task.cancelled():
                    with suppress(Exception):
                        task.result()
                self._tasks[login] = asyncio.create_task(
                    self._listen(login), name=f"follow-eventsub:{login}"
                )

    async def stop(self) -> None:
        self._stop_event.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._ready.clear()
        self._ready_since.clear()

    async def _listen(self, login: str) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            try:
                await self._token_store.execute_with_token(
                    login,
                    lambda broadcaster_id, access_token: self._connection(
                        login, broadcaster_id, access_token
                    ),
                )
                backoff = 1
            except asyncio.CancelledError:
                raise
            except (TwitchAuthError, OAuthTokenTerminalError) as e:
                self._last_error = type(e).__name__
                self._last_error_at = time.time()
                logger.warning(
                    "EventSub follow требует повторной авторизации: %s", login
                )
                return
            except Exception as e:
                self._last_error = type(e).__name__
                self._last_error_at = time.time()
                logger.exception("EventSub follow: соединение для %s оборвалось", login)
            finally:
                self._first_attempt.add(login)
                if self._ready.pop(login, False):
                    await self._db.mark_live_follow_counts_unreliable(login)
                self._ready_since.pop(login, None)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 60)

    async def _connection(
        self, login: str, broadcaster_id: str, access_token: str
    ) -> None:
        ws = await self._session.ws_connect(EVENTSUB_WS_URL, heartbeat=20)
        try:
            welcome = await asyncio.wait_for(ws.receive_json(), timeout=10)
            if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                raise RuntimeError("Twitch не прислал session_welcome")
            session_id = welcome["payload"]["session"]["id"]
            receive_timeout = (
                float(welcome["payload"]["session"].get("keepalive_timeout_seconds", 30))
                + EVENTSUB_KEEPALIVE_GRACE_SECONDS
            )
            await self._subscribe(session_id, broadcaster_id, access_token)
            self._ready[login] = True
            connected_at = time.time()
            self._ready_since[login] = connected_at
            self._last_message_at[login] = connected_at
            self._first_attempt.add(login)
            logger.info("EventSub follow подключён: %s", login)

            while not self._stop_event.is_set():
                # Twitch шлёт keepalive не как WebSocket ping, а как JSON-сообщение.
                # Поэтому один heartbeat aiohttp не замечает логически зависшую
                # EventSub-сессию: ставим deadline по значению из session_welcome.
                message = await asyncio.wait_for(ws.receive(), timeout=receive_timeout)
                if message.type == aiohttp.WSMsgType.TEXT:
                    payload = message.json()
                    self._last_message_at[login] = time.time()
                    kind = payload.get("metadata", {}).get("message_type")
                    if kind == "notification":
                        await self._handle_notification(login, payload)
                    elif kind == "session_reconnect":
                        reconnect_url = payload["payload"]["session"]["reconnect_url"]
                        new_ws = await self._session.ws_connect(reconnect_url, heartbeat=20)
                        new_welcome = await asyncio.wait_for(new_ws.receive_json(), timeout=10)
                        if new_welcome.get("metadata", {}).get("message_type") != "session_welcome":
                            await new_ws.close()
                            raise RuntimeError("Twitch не подтвердил EventSub reconnect")
                        receive_timeout = (
                            float(
                                new_welcome["payload"]["session"].get(
                                    "keepalive_timeout_seconds", 30
                                )
                            )
                            + EVENTSUB_KEEPALIVE_GRACE_SECONDS
                        )
                        await ws.close()
                        ws = new_ws
                        self._last_message_at[login] = time.time()
                        logger.info("EventSub follow переподключён без разрыва: %s", login)
                    elif kind == "revocation":
                        status = payload.get("payload", {}).get("subscription", {}).get("status")
                        if status in {"authorization_revoked", "user_removed"}:
                            raise TwitchAuthError(
                                f"Twitch EventSub authorization больше не действует: {login}"
                            )
                        raise RuntimeError(f"Twitch отозвал EventSub-подписку: {status}")
                elif message.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                }:
                    raise ConnectionError(f"EventSub WebSocket закрыт: {message.type.name}")
        finally:
            await ws.close()

    async def _subscribe(
        self, session_id: str, broadcaster_id: str, access_token: str
    ) -> None:
        body = {
            "type": "channel.follow",
            "version": "2",
            "condition": {
                "broadcaster_user_id": broadcaster_id,
                "moderator_user_id": broadcaster_id,
            },
            "transport": {"method": "websocket", "session_id": session_id},
        }
        await user_token_request(
            self._session,
            self._client_id,
            "POST",
            EVENTSUB_SUBSCRIPTIONS_URL,
            access_token,
            json_body=body,
            expected_status=202,
            parse_json=False,
        )

    async def _handle_notification(self, login: str, payload: dict) -> None:
        subscription = payload.get("payload", {}).get("subscription", {})
        if subscription.get("type") != "channel.follow":
            return
        message_id = payload.get("metadata", {}).get("message_id")
        if not message_id:
            logger.warning("EventSub follow без message_id для %s", login)
            return
        await self._db.record_follow_event(login, message_id)
