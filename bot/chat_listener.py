from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from typing import Callable

import aiohttp

logger = logging.getLogger(__name__)

IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"
ANON_NICK = "justinfan12345"  # анонимный логин Twitch IRC для read-only доступа

# :nickname!nickname@nickname.tmi.twitch.tv JOIN #channel
_JOIN_RE = re.compile(r"^:([a-zA-Z0-9_]+)!\S+ JOIN #")
# :nickname!nickname@nickname.tmi.twitch.tv PRIVMSG #channel :текст сообщения
_PRIVMSG_RE = re.compile(r"^:([a-zA-Z0-9_]+)!\S+ PRIVMSG #")
# @<теги через ;> :tmi.twitch.tv USERNOTICE #channel — официальное системное
# уведомление Twitch (рейды, сабы и т.п.), теги содержат msg-id и параметры
_USERNOTICE_RE = re.compile(r"^@(\S+) :\S+ USERNOTICE #")

# сколько новых JOIN за окно ниже считается вероятным рейдом (эвристика,
# запасной вариант на случай, если Twitch не пришлёт официальный USERNOTICE)
RAID_JOIN_THRESHOLD = 30
RAID_WINDOW_SECONDS = 30


class ChatListener:
    """Слушает публичный Twitch-чат одного канала: считает сообщения по минутным бакетам,
    собирает написавших в чат и ники, зашедшие через JOIN, пока не вызван stop().

    Ограничение Twitch: на крупных каналах (обычно от нескольких тысяч зрителей) Twitch
    отключает индивидуальные JOIN/PART события в IRC ради снижения нагрузки — поэтому
    JOIN-данные годятся только как запасная эвристика для детектора рейдов, а «уникальные
    чатеры» считаются по тем, кто реально писал (см. get_and_clear_chatters)."""

    # сколько минутных интервалов подряд должны содержать сообщения без единого JOIN,
    # чтобы зафиксировать точный момент отключения join/part-рассылки
    STALE_INTERVALS_THRESHOLD = 5

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._tasks: dict[str, asyncio.Task] = {}
        self._buckets: dict[str, dict[int, int]] = defaultdict(dict)
        # ник -> время первого JOIN (unix timestamp)
        self._unique_nicks: dict[str, dict[str, float]] = defaultdict(dict)
        # минутные интервалы: были ли сообщения и был ли хоть один JOIN
        self._interval_has_message: dict[str, dict[int, bool]] = defaultdict(dict)
        self._interval_has_join: dict[str, dict[int, bool]] = defaultdict(dict)
        # интервал (по времени начала стрима), в котором детект впервые сработал
        self._stale_since: dict[str, float | None] = {}
        # ник -> число сообщений за сессию (для топ-чатеров)
        self._message_counts: dict[str, dict[str, int]] = defaultdict(dict)
        # ник -> время первого сообщения. В отличие от JOIN, сообщения Twitch
        # присылает всегда и на любом канале, поэтому счёт «уникальных чатеров»
        # по написавшим надёжен даже на крупных каналах, где join/part отключены
        self._chatter_first_seen: dict[str, dict[str, float]] = defaultdict(dict)
        # временные метки JOIN за скользящее окно — только для детектора рейдов
        self._recent_joins: dict[str, list[float]] = defaultdict(list)
        # (timestamp, join_count, raider_name | None) для каждого зафиксированного
        # всплеска JOIN — raider_name заполнен, если Twitch прислал официальный
        # USERNOTICE msg-id=raid, иначе None (сработала только JOIN-эвристика)
        self._raid_events: dict[str, list[tuple[float, int, str | None]]] = defaultdict(list)
        # callback(login, raider_name, viewer_count), вызывается сразу при получении
        # официального USERNOTICE о рейде — для мгновенного уведомления, не постфактум
        self._on_raid: Callable[[str, str, int], None] | None = None

    def start(self, login: str) -> None:
        if login in self._tasks:
            return
        self._buckets[login] = {}
        self._unique_nicks[login] = {}
        self._interval_has_message[login] = {}
        self._interval_has_join[login] = {}
        self._stale_since[login] = None
        self._message_counts[login] = {}
        self._chatter_first_seen[login] = {}
        self._recent_joins[login] = []
        self._raid_events[login] = []
        self._tasks[login] = asyncio.create_task(self._run(login))

    def is_running(self, login: str) -> bool:
        """Идёт ли сейчас сбор по этому каналу. Нужно, чтобы не забирать буфер
        повторно, когда стрим уже завершён и слушатель остановлен."""
        return login in self._tasks

    async def stop(self, login: str) -> None:
        task = self._tasks.pop(login, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def get_and_clear_activity(self, login: str) -> list[tuple[float, int]]:
        """Вернёт [(minute_start_ts, message_count), ...] и очистит буфер."""
        buckets = self._buckets.pop(login, {})
        return sorted(buckets.items())

    def get_and_clear_unique_viewers(self, login: str) -> list[tuple[str, float]]:
        """Вернёт список (ник, время первого JOIN) уникальных зрителей чата за сессию,
        отсортированный по времени входа, и очистит буфер."""
        nicks = self._unique_nicks.pop(login, {})
        return sorted(nicks.items(), key=lambda item: item[1])

    def get_and_clear_chatters(self, login: str) -> list[tuple[str, float]]:
        """Вернёт список (ник, время первого сообщения) всех, кто писал в чат за сессию,
        отсортированный по времени, и очистит буфер. Это надёжная замена подсчёту через
        JOIN: сообщения Twitch рассылает всегда, а join/part глушит на крупных каналах."""
        nicks = self._chatter_first_seen.pop(login, {})
        return sorted(nicks.items(), key=lambda item: item[1])

    def get_and_clear_top_chatters(self, login: str, limit: int = 5) -> list[tuple[str, int]]:
        """Вернёт топ по числу сообщений за сессию [(ник, count), ...] и очистит буфер."""
        counts = self._message_counts.pop(login, {})
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]

    def set_raid_callback(self, callback: Callable[[str, str, int], None] | None) -> None:
        """callback(login, raider_name, viewer_count) вызывается сразу при получении
        официального USERNOTICE о рейде — используется для мгновенного уведомления."""
        self._on_raid = callback

    def get_and_clear_raid_events(self, login: str) -> list[tuple[float, int, str | None]]:
        """Вернёт [(timestamp, join_count, raider_name), ...] зафиксированных
        рейдов/всплесков JOIN и очистит буфер."""
        self._recent_joins.pop(login, None)
        return self._raid_events.pop(login, [])

    def get_and_clear_join_reliability(self, login: str) -> bool:
        """True, если join/part-рассылка выглядела рабочей весь стрим. False, если в
        какой-то момент STALE_INTERVALS_THRESHOLD минутных интервалов подряд шли
        сообщения без единого JOIN — явный признак отключения рассылки Twitch'ем."""
        has_message = self._interval_has_message.pop(login, {})
        self._interval_has_join.pop(login, {})
        stale_since = self._stale_since.pop(login, None)
        if not has_message:
            return True
        return stale_since is None

    async def _run(self, login: str) -> None:
        backoff = 1
        while True:
            try:
                await self._listen_once(login)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Обрыв соединения с чатом %s, переподключение через %sс", login, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _listen_once(self, login: str) -> None:
        async with self._session.ws_connect(IRC_WS_URL, heartbeat=30) as ws:
            # tags-capability нужна, чтобы Twitch присылал теги (msg-id и т.п.) вместе
            # с USERNOTICE — без неё сообщение о рейде приходит без данных о рейдере
            await ws.send_str("CAP REQ :twitch.tv/tags")
            await ws.send_str(f"NICK {ANON_NICK}")
            await ws.send_str(f"JOIN #{login}")

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                for line in msg.data.split("\r\n"):
                    if not line:
                        continue
                    if line.startswith("PING"):
                        await ws.send_str("PONG :tmi.twitch.tv")
                        continue
                    if "USERNOTICE" in line:
                        self._handle_usernotice(login, line)
                    elif "PRIVMSG" in line:
                        privmsg_match = _PRIVMSG_RE.match(line)
                        self._record_message(login, privmsg_match.group(1) if privmsg_match else None)
                    else:
                        join_match = _JOIN_RE.match(line)
                        if join_match:
                            self._record_join(login, join_match.group(1))

    def _record_message(self, login: str, nick: str | None) -> None:
        now = time.time()
        minute = int(now // 60 * 60)
        buckets = self._buckets[login]
        buckets[minute] = buckets.get(minute, 0) + 1
        self._interval_has_message[login][minute] = True
        self._check_stale(login, minute)
        if nick is not None:
            counts = self._message_counts[login]
            counts[nick] = counts.get(nick, 0) + 1
            self._chatter_first_seen[login].setdefault(nick, now)

    def _record_join(self, login: str, nick: str) -> None:
        now = time.time()
        minute = int(now // 60 * 60)
        self._unique_nicks[login].setdefault(nick, now)
        self._interval_has_join[login][minute] = True
        self._check_raid(login, now)

    def _check_raid(self, login: str, now: float) -> None:
        """Считает JOIN за скользящее окно RAID_WINDOW_SECONDS — если их набирается
        RAID_JOIN_THRESHOLD и больше, фиксирует момент как вероятный рейд (без имени
        рейдера — это запасная эвристика на случай, если официальный USERNOTICE
        не пришёл) и обнуляет окно, чтобы не насчитать несколько событий подряд."""
        recent = self._recent_joins[login]
        recent.append(now)
        cutoff = now - RAID_WINDOW_SECONDS
        while recent and recent[0] < cutoff:
            recent.pop(0)
        if len(recent) >= RAID_JOIN_THRESHOLD:
            self._raid_events[login].append((now, len(recent), None))
            recent.clear()

    def _handle_usernotice(self, login: str, line: str) -> None:
        """Разбирает тегированный USERNOTICE — если это msg-id=raid, Twitch прислал
        официальное подтверждение рейда с именем рейдера и числом зрителей, которых
        он привёл. Это точнее и мгновеннее JOIN-эвристики: не нужно ждать всплеска."""
        match = _USERNOTICE_RE.match(line)
        if not match:
            return
        tags = dict(
            tag.split("=", 1) if "=" in tag else (tag, "") for tag in match.group(1).split(";")
        )
        if tags.get("msg-id") != "raid":
            return

        raider_name = tags.get("msg-param-displayName") or tags.get("msg-param-login") or "неизвестный канал"
        try:
            viewer_count = int(tags.get("msg-param-viewerCount", "0"))
        except ValueError:
            viewer_count = 0

        now = time.time()
        self._raid_events[login].append((now, viewer_count, raider_name))
        self._recent_joins[login].clear()  # не даём той же волне JOIN задвоить событие эвристикой

        if self._on_raid is not None:
            try:
                self._on_raid(login, raider_name, viewer_count)
            except Exception:
                logger.exception("Ошибка в обработчике рейда для %s", login)

    def _check_stale(self, login: str, current_minute: int) -> None:
        """Смотрит последние STALE_INTERVALS_THRESHOLD минутных интервалов: если во всех
        были сообщения, но ни в одном не было JOIN — фиксирует момент отключения рассылки."""
        if self._stale_since.get(login) is not None:
            return  # уже зафиксировано раньше

        has_message = self._interval_has_message[login]
        has_join = self._interval_has_join[login]

        streak = 0
        for i in range(self.STALE_INTERVALS_THRESHOLD):
            minute = current_minute - i * 60
            if not has_message.get(minute):
                return  # интервал без сообщений — недостаточно данных, серию не считаем
            if has_join.get(minute):
                return  # был JOIN в этом интервале — рассылка ещё жива
            streak += 1

        if streak >= self.STALE_INTERVALS_THRESHOLD:
            self._stale_since[login] = current_minute
