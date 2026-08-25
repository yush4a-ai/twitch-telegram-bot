from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone

import aiohttp

from .database import Database
from .token_store import TokenStore


logger = logging.getLogger(__name__)

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
EVENTSUB_SUBSCRIPTIONS_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"


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
        self._first_attempt: set[str] = set()
        self._stop_event = asyncio.Event()

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
        try:
            while not self._stop_event.is_set():
                await self._sync_channels()
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.stop()

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
                token_data = await self._token_store.get_valid_token(login)
                if token_data is None:
                    self._first_attempt.add(login)
                    return
                broadcaster_id, access_token = token_data
                await self._connection(login, broadcaster_id, access_token)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
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
            await self._subscribe(session_id, broadcaster_id, access_token)
            self._ready[login] = True
            self._ready_since[login] = time.time()
            self._first_attempt.add(login)
            logger.info("EventSub follow подключён: %s", login)

            while not self._stop_event.is_set():
                message = await ws.receive()
                if message.type == aiohttp.WSMsgType.TEXT:
                    payload = message.json()
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
                        await ws.close()
                        ws = new_ws
                        logger.info("EventSub follow переподключён без разрыва: %s", login)
                    elif kind == "revocation":
                        status = payload.get("payload", {}).get("subscription", {}).get("status")
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
        headers = {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        async with self._session.post(
            EVENTSUB_SUBSCRIPTIONS_URL, json=body, headers=headers
        ) as response:
            if response.status != 202:
                detail = (await response.text())[:500]
                raise RuntimeError(
                    f"Create EventSub subscription: HTTP {response.status}: {detail}"
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
