from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .chat_listener import ChatListener
from .database import Database
from .report import build_report_html
from .token_store import TokenStore
from .twitch import ClipInfo, TwitchClient

logger = logging.getLogger(__name__)

MESSAGE_TEMPLATE = "🔴 <b>{channel_name}</b>\n{title}\n\n👁 Сейчас смотрят: {viewer_count}"

# сколько ждать после ухода стрима в offline, прежде чем удалить пост
OFFLINE_GRACE_SECONDS = 5 * 60

# если новый stream_id появляется в течение этого времени после ухода в offline,
# считаем это тем же сеансом (быстрый рестарт стрима) — редактируем старый пост
# вместо публикации нового и не спамим повторными уведомлениями
RESTART_MERGE_GRACE_SECONDS = 3 * 60

# сколько хранить сырые данные отчёта (график, ники чатеров) для повторного /report
REPORT_DATA_RETENTION_SECONDS = 24 * 60 * 60

_KEYCAP_DIGITS = {
    "0": "0️⃣", "1": "1️⃣", "2": "2️⃣", "3": "3️⃣", "4": "4️⃣",
    "5": "5️⃣", "6": "6️⃣", "7": "7️⃣", "8": "8️⃣", "9": "9️⃣",
}


def _keycap_number(value: int) -> str:
    return "".join(_KEYCAP_DIGITS[digit] for digit in str(value))


# короткий подпись-префикс перед ссылкой вроде "tg:", "instagram:", "vk:"
_LINK_LABEL_RE = r"(?:[a-zA-Zа-яА-Я0-9_]{1,15}\s*:\s*)?"
# сама ссылка: полный URL или голый домен с распространённым TLD + необязательный путь
_LINK_BODY_RE = (
    r"(?:https?://\S+"
    r"|(?:[a-zA-Z0-9-]+\.)+(?:com|ru|tv|gg|me|net|org|io|co|app)(?:/\S*)?)"
)
# сам разделитель-эмодзи, ближайший к ссылке — убираем максимум один с каждой стороны
_SIDE_EMOJI_RE = r"[\U0001F534\U0001F7E0\U0001F7E1\U0001F7E2\U0001F535\U0001F7E3⚫⚪]"

_LINK_WITH_ONE_SIDE_EMOJI_RE = re.compile(
    rf"(?:{_SIDE_EMOJI_RE}\s*)?{_LINK_LABEL_RE}{_LINK_BODY_RE}(?:\s*{_SIDE_EMOJI_RE})?",
    re.IGNORECASE,
)


def _strip_links(title: str) -> str:
    """Убирает ссылки (URL, t.me/..., голые домены) вместе с ближайшим одним
    эмодзи-разделителем с каждой стороны, если он есть. Остальной текст, включая
    другие разделители и упоминания без ссылок (коллабы и т.п.), не трогает."""
    cleaned = _LINK_WITH_ONE_SIDE_EMOJI_RE.sub(" ", title)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _find_collab_mentions(title: str | None, self_login: str, candidates: dict[str, str | None]) -> list[str]:
    """Ищет в заголовке стрима упоминание других отслеживаемых каналов — по логину
    или отображаемому имени (регистронезависимо, с границей слова) — и возвращает их
    логины. candidates — {twitch_login: display_name} всех отслеживаемых каналов,
    кроме самого self_login."""
    if not title:
        return []
    found = []
    for login, display_name in candidates.items():
        if login == self_login:
            continue
        names = {login, display_name} if display_name else {login}
        for name in names:
            if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", title, re.IGNORECASE):
                found.append(login)
                break
    return found


def _is_within_quiet_hours(start_minute: int, end_minute: int, now: datetime) -> bool:
    """start_minute/end_minute — минуты от полуночи UTC. Если start > end, интервал
    переходит через полночь (например, 23:00-08:00)."""
    current_minute = now.hour * 60 + now.minute
    if start_minute <= end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def _format_offset(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# во сколько раз зрителей должно резко прибыть и так же резко убыть на соседнем
# замере, чтобы точку сочли подозрительным всплеском (накруткой), а не органическим
# ростом — органический рост/спад аудитории за минуту почти никогда не выглядит так
SPIKE_RATIO_THRESHOLD = 1.6
# ниже этого числа зрителей всплеск не рассматриваем — на маленьких каналах
# обычные колебания легко дают такое же процентное отклонение
SPIKE_MIN_VIEWERS = 200


def _detect_viewer_spikes(
    samples: list[tuple[float, int, str, str]]
) -> tuple[list[tuple[float, int, str, str]], list[tuple[float, int]]]:
    """Находит одиночные "шипы" на графике зрителей — резкий скачок вверх, тут же
    сменяющийся таким же резким спадом на следующем замере. Возвращает (samples без
    точек-шипов, [(offset_ts, viewer_count), ...] отмеченных точек) — очищенные
    samples используются для честного пересчёта пика/среднего, отмеченные точки
    показываются в отчёте как вероятная накрутка."""
    if len(samples) < 3:
        return samples, []

    spikes: list[tuple[float, int]] = []
    spike_indices: set[int] = set()

    for i in range(1, len(samples) - 1):
        prev_count = samples[i - 1][1]
        curr_count = samples[i][1]
        next_count = samples[i + 1][1]

        if curr_count < SPIKE_MIN_VIEWERS:
            continue

        rose_sharply = prev_count > 0 and curr_count / prev_count >= SPIKE_RATIO_THRESHOLD
        fell_sharply = next_count > 0 and curr_count / next_count >= SPIKE_RATIO_THRESHOLD

        if rose_sharply and fell_sharply:
            spike_indices.add(i)
            spikes.append((samples[i][0], curr_count))

    if not spike_indices:
        return samples, []

    cleaned = [s for idx, s in enumerate(samples) if idx not in spike_indices]
    return cleaned, spikes


def _build_vod_chapters(samples: list[tuple[float, int, str, str]], start_ts: float) -> list[dict]:
    """Таймкоды ключевых моментов для VOD-архива — смена игры и пик зрителей,
    построены на основе тех же поминутных замеров, что и график в HTML-отчёте
    (не требует дополнительных запросов к Twitch)."""
    chapters: list[dict] = []
    last_game: str | None = None
    peak_viewer_count = -1
    peak_offset = 0

    for sampled_at, viewer_count, _title, game_name in samples:
        offset = max(0, int(sampled_at - start_ts))
        if last_game is None:
            chapters.append({"offset": offset, "label": f"Начало — {game_name}"})
        elif game_name != last_game:
            chapters.append({"offset": offset, "label": f"Смена игры → {game_name}"})
        last_game = game_name

        if viewer_count > peak_viewer_count:
            peak_viewer_count = viewer_count
            peak_offset = offset

    if peak_viewer_count >= 0:
        chapters.append({"offset": peak_offset, "label": f"Пик зрителей — {peak_viewer_count}"})

    chapters.sort(key=lambda c: c["offset"])
    return chapters


class StreamPoller:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        twitch: TwitchClient,
        interval_seconds: int,
        token_store: TokenStore | None = None,
        chat_listener: ChatListener | None = None,
        owner_chat_id: int | None = None,
    ) -> None:
        self._bot = bot
        self._db = db
        self._twitch = twitch
        self._interval = interval_seconds
        self._token_store = token_store
        self._chat_listener = chat_listener
        self._owner_chat_id = owner_chat_id
        self._join_reliable_cache: dict[tuple[int, str, str], bool] = {}
        self._top_chatters_cache: dict[tuple[int, str, str], list[tuple[str, int]]] = {}
        self._raid_events_cache: dict[tuple[int, str, str], list[tuple[float, int, str | None]]] = {}
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        logger.info("Поллер запущен, интервал %s сек", self._interval)
        if self._chat_listener is not None:
            self._chat_listener.set_raid_callback(self._on_raid_detected)
        await self._resume_chat_listeners()
        while not self._stop_event.is_set():
            try:
                await self._check_once()
            except Exception:
                logger.exception("Ошибка в цикле опроса Twitch")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _resume_chat_listeners(self) -> None:
        """Сбор чат-активности и уникальных ников — это in-memory состояние, которое
        полностью теряется при падении/перезапуске процесса. Для стримов, уже шедших
        на момент запуска (is_live=1 в БД), нужно явно перезапустить слушателя —
        иначе бот решит, что сбор для них уже идёт (тот же stream_id), и пропустит
        его до самого конца стрима."""
        if self._chat_listener is None:
            return
        live_logins = await self._db.all_distinct_live_logins()
        for login in live_logins:
            self._chat_listener.start(login)
        if live_logins:
            logger.info(
                "Возобновлён сбор чат-активности для %s уже идущих стримов: %s",
                len(live_logins), ", ".join(live_logins),
            )

    async def _check_channel_bans(self) -> None:
        """Раз в цикл проверяет каналы, отслеживаемые из личного чата владельца бота,
        на бан/саспенд/удаление аккаунта на Twitch — шлёт алерт при переходе
        существовал → не существует. Одним батч-запросом на все логины разом,
        чтобы не создавать лишней нагрузки на Twitch API."""
        if self._owner_chat_id is None:
            return
        logins = await self._db.list_channels(self._owner_chat_id)
        if not logins:
            return

        try:
            existing = await self._twitch.get_existing_logins(logins)
        except Exception:
            logger.exception("Не удалось проверить существование каналов на Twitch")
            return

        for login in logins:
            exists_now = login in existing
            existed_before = await self._db.get_existence_status(login)
            if existed_before and not exists_now:
                await self._notify_channel_banned(login)
            await self._db.set_existence_status(login, exists_now)

    async def _notify_channel_banned(self, login: str) -> None:
        text = (
            f"🚨 Канал <b>{html.escape(login)}</b> больше не существует на Twitch — "
            "возможно, аккаунт забанен, приостановлен или удалён."
        )
        try:
            await self._bot.send_message(self._owner_chat_id, text)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Не удалось отправить алерт о бане канала %s: %s", login, e)

    async def _check_once(self) -> None:
        await self._check_streams()
        await self._send_pending_stats()
        await self._cleanup_offline_posts()
        await self._check_channel_bans()
        await self._check_channel_renames()
        await self._check_quiet_hours_end()
        await self._db.purge_old_report_data(time.time() - REPORT_DATA_RETENTION_SECONDS)

    async def _check_quiet_hours_end(self) -> None:
        """Раз в цикл проверяет каждый чат с настроенными тихими часами: если сейчас
        тихие часы уже закончились и накопились отложенные отчёты — присылает сводку
        «кто стримил, пока тебя не было» с кнопками показать/не показывать подробности.
        Сводка отправляется один раз (флаг quiet_hours_digest_sent) — не спамит каждый
        цикл, пока пользователь не ответит на кнопки под уже отправленной сводкой."""
        now = datetime.now(timezone.utc)
        for chat_id in await self._db.all_quiet_hours_chat_ids():
            quiet_hours = await self._db.get_quiet_hours(chat_id)
            if quiet_hours is None:
                continue
            start_minute, end_minute, _utc_offset, notify_after_enabled = quiet_hours
            if _is_within_quiet_hours(start_minute, end_minute, now):
                # снова вошли в тихие часы — сбрасываем флаг, чтобы следующая сводка
                # (по итогам этого захода) снова могла быть отправлена один раз
                await self._db.clear_quiet_hours_digest_sent(chat_id)
                continue
            if not await self._db.has_deferred_reports(chat_id):
                continue
            if await self._db.is_quiet_hours_digest_sent(chat_id):
                continue
            if not notify_after_enabled:
                # пользователь отключил сводку — просто отбрасываем накопленные записи
                await self._db.get_and_clear_deferred_reports(chat_id)
                continue
            await self._send_quiet_hours_digest(chat_id)
            await self._db.mark_quiet_hours_digest_sent(chat_id)

    async def _send_quiet_hours_digest(self, chat_id: int) -> None:
        """Отложенные записи намеренно НЕ удаляются здесь — они остаются в БД до тех
        пор, пока пользователь не нажмёт «Показать»/«Не нужно» под сводкой (переживает
        рестарт бота между отправкой сводки и ответом на неё)."""
        entries = await self._db.peek_deferred_reports(chat_id)
        if not entries:
            return
        logins = sorted({login for _source_chat_id, login, _stream_id, _ended_at in entries})
        lines = "\n".join(f"• {html.escape(login)}" for login in logins)
        text = (
            "🌙 Пока тихие часы были включены, стримили:\n"
            f"{lines}\n\n"
            "Показать подробные отчёты по этим стримам?"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Показать", callback_data=f"quietdigest:show:{chat_id}"),
                    InlineKeyboardButton(text="Не нужно", callback_data=f"quietdigest:skip:{chat_id}"),
                ]
            ]
        )
        try:
            await self._bot.send_message(chat_id, text, reply_markup=keyboard)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Не удалось отправить сводку тихих часов в чат %s: %s", chat_id, e)

    async def _check_channel_renames(self) -> None:
        """Раз в цикл сверяет отображаемое имя (display_name) отслеживаемых каналов
        с последним известным — Twitch позволяет менять регистр/написание ника, и молча
        отслеживать логин недостаточно, если стример переименовался полностью. Уведомляет
        все чаты, которые следят за этим каналом."""
        logins = await self._db.all_distinct_logins()
        if not logins:
            return

        try:
            display_names = await self._twitch.get_display_names(logins)
        except Exception:
            logger.exception("Не удалось проверить отображаемые имена каналов")
            return

        for login, display_name in display_names.items():
            previous = await self._db.get_display_name(login)
            if previous is not None and previous != display_name:
                await self._notify_channel_renamed(login, previous, display_name)
            await self._db.set_display_name(login, display_name)

    async def _notify_channel_renamed(self, login: str, old_name: str, new_name: str) -> None:
        text = (
            f"✏️ Канал <b>{html.escape(login)}</b> сменил отображаемое имя: "
            f"«{html.escape(old_name)}» → «{html.escape(new_name)}»."
        )
        for chat_id in await self._db.chats_for_login(login):
            try:
                await self._bot.send_message(chat_id, text)
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logger.warning("Не удалось отправить алерт о переименовании канала в чат %s: %s", chat_id, e)

    def _on_raid_detected(self, login: str, raider_name: str, viewer_count: int) -> None:
        """Синхронный callback из ChatListener (вызывается прямо из парсинга IRC-строки) —
        оборачиваем в задачу, чтобы не блокировать чтение сокета отправкой в Telegram."""
        asyncio.create_task(self._notify_raid(login, raider_name, viewer_count))

    async def _notify_raid(self, login: str, raider_name: str, viewer_count: int) -> None:
        text = (
            f"⚡ Канал <b>{html.escape(login)}</b> рейдят! "
            f"<b>{html.escape(raider_name)}</b> привёл {viewer_count} зрителей."
        )
        for chat_id in await self._db.chats_for_login(login):
            if not await self._db.get_raid_detection_enabled(chat_id, login):
                continue
            try:
                await self._bot.send_message(chat_id, text)
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logger.warning("Не удалось отправить уведомление о рейде в чат %s: %s", chat_id, e)

    async def _detect_collab(self, login: str, title: str | None) -> list[str]:
        """Если в финальном заголовке стрима упоминается логин или отображаемое имя
        другого отслеживаемого канала — считаем стрим коллабом с ним. display_name
        берём из кэша, который уже поддерживает _check_channel_renames, чтобы не
        делать лишний запрос к Twitch API."""
        if not title:
            return []
        other_logins = [l for l in await self._db.all_distinct_logins() if l != login]
        if not other_logins:
            return []
        candidates = {}
        for other_login in other_logins:
            candidates[other_login] = await self._db.get_display_name(other_login)
        return _find_collab_mentions(title, login, candidates)

    async def _check_streams(self) -> None:
        logins = await self._db.all_distinct_logins()
        if not logins:
            return

        live_streams = await self._twitch.get_live_streams(logins)

        for login in logins:
            chat_ids = await self._db.chats_for_login(login)
            stream = live_streams.get(login)

            for chat_id in chat_ids:
                (
                    was_live,
                    last_stream_id,
                    last_message_id,
                    last_title,
                    offline_since,
                    _stream_started_at,
                    _peak_viewers,
                ) = await self._db.get_live_state(chat_id, login)

                if stream is not None:
                    title = stream.title or "(без названия)"
                    # тот же стрим — либо шёл без перерыва, либо ещё не удалили пост
                    # за время в оффлайне (стрим быстро вернулся)
                    same_stream = last_stream_id == stream.stream_id and last_message_id is not None
                    # быстрый рестарт — другой stream_id, но канал был в оффлайне совсем
                    # недавно (например, стример словил бан-момент, удалил стрим и тут же
                    # начал заново). Считаем это продолжением того же сеанса, чтобы не
                    # заваливать чат новым постом на каждый такой рестарт
                    quick_restart = (
                        not same_stream
                        and last_message_id is not None
                        and offline_since is not None
                        and time.time() - offline_since < RESTART_MERGE_GRACE_SECONDS
                    )
                    notify_enabled = await self._db.get_notify_enabled(chat_id, login)
                    # живой пост о старте стрима всегда публикуется в исходный чат —
                    # привязка к личке (post_recipient) влияет только на финальный отчёт

                    if not same_stream and not quick_restart and self._chat_listener is not None:
                        self._chat_listener.start(login)

                    if not notify_enabled:
                        # уведомления выключены для этого канала в этом чате — статистика
                        # всё равно собирается, но пост не публикуется и не редактируется
                        message_id = None
                    elif same_stream or quick_restart:
                        edited = await self._edit(chat_id, last_message_id, login, title, stream.viewer_count)
                        if edited:
                            message_id = last_message_id
                        else:
                            # старый пост — фото (до перехода на текстовые сообщения),
                            # его нельзя отредактировать в текст; пересоздаём как текст
                            try:
                                await self._bot.delete_message(chat_id, last_message_id)
                            except (TelegramForbiddenError, TelegramBadRequest):
                                pass
                            message_id = await self._notify(chat_id, login, title, stream.viewer_count)
                    else:
                        if last_message_id is not None:
                            # стрим прервался надолго, потом начался заново (новый
                            # stream_id) — без этого старый пост о прошлом запуске
                            # остаётся висеть навсегда, потому что is_live уже снова
                            # стало True и он больше не подпадает под условие
                            # pending_offline_posts()
                            try:
                                await self._bot.delete_message(chat_id, last_message_id)
                            except (TelegramForbiddenError, TelegramBadRequest):
                                pass
                        message_id = await self._notify(chat_id, login, title, stream.viewer_count)

                    # при быстром рестарте держим прежний stream_id как идентификатор
                    # сессии — иначе накопленные viewer_sum/peak_viewers/samples
                    # обнулятся или расколются между двумя stream_id, и итоговый отчёт
                    # не увидит часть до рестарта
                    effective_stream_id = last_stream_id if quick_restart else stream.stream_id
                    effective_started_at = _stream_started_at if quick_restart else stream.started_at

                    await self._db.set_live_state(
                        chat_id,
                        login,
                        True,
                        effective_stream_id,
                        message_id,
                        title,
                        stream_started_at=effective_started_at,
                    )
                    await self._db.record_viewer_sample(chat_id, login, stream.viewer_count)
                    await self._db.add_stream_sample(
                        chat_id,
                        login,
                        effective_stream_id,
                        time.time(),
                        stream.viewer_count,
                        title,
                        stream.game_name or "—",
                    )
                    await self._maybe_snapshot_followers(chat_id, login)
                else:
                    if was_live:
                        # не удаляем пост сразу — вдруг стрим переподключится в течение
                        # OFFLINE_GRACE_SECONDS; помечаем время ухода в оффлайн
                        await self._db.set_live_state(
                            chat_id,
                            login,
                            False,
                            last_stream_id,
                            last_message_id,
                            last_title,
                            offline_since=time.time(),
                            stream_started_at=_stream_started_at,
                            peak_viewers=_peak_viewers,
                        )
                        is_telegram_channel = await self._db.is_telegram_channel(chat_id)
                        if (
                            self._chat_listener is not None
                            and last_stream_id is not None
                            and not is_telegram_channel
                        ):
                            # Telegram-канал не получает итоговый отчёт, поэтому не должен
                            # первым забирать буфер ChatListener у групп, которые его
                            # действительно используют (get_and_clear_* очищает буфер)
                            activity = self._chat_listener.get_and_clear_activity(login)
                            nicks = self._chat_listener.get_and_clear_unique_viewers(login)
                            join_reliable = self._chat_listener.get_and_clear_join_reliability(login)
                            top_chatters = self._chat_listener.get_and_clear_top_chatters(login)
                            raid_events = self._chat_listener.get_and_clear_raid_events(login)
                            await self._chat_listener.stop(login)

                            await self._db.add_chat_activity_samples(
                                chat_id, login, last_stream_id, activity
                            )
                            await self._db.add_chat_unique_nicks(
                                chat_id, login, last_stream_id, nicks
                            )
                            self._join_reliable_cache[(chat_id, login, last_stream_id)] = join_reliable
                            self._top_chatters_cache[(chat_id, login, last_stream_id)] = top_chatters
                            self._raid_events_cache[(chat_id, login, last_stream_id)] = raid_events

    async def _maybe_snapshot_followers(self, chat_id: int, login: str) -> None:
        if self._token_store is None:
            return
        if await self._db.get_followers_at_start(chat_id, login) is not None:
            return
        token = await self._token_store.get_valid_token(login)
        if token is None:
            return
        broadcaster_id, access_token = token
        try:
            count = await self._twitch.get_followers_count(broadcaster_id, access_token)
        except Exception:
            logger.exception("Не удалось получить число фолловеров для %s", login)
            return
        await self._db.set_followers_at_start(chat_id, login, count)

    async def _cleanup_offline_posts(self) -> None:
        now = time.time()
        for chat_id, login, message_id, offline_since in await self._db.pending_offline_posts():
            if now - offline_since < OFFLINE_GRACE_SECONDS:
                continue
            try:
                await self._bot.delete_message(chat_id, message_id)
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                logger.warning("Не удалось удалить сообщение %s в чате %s: %s", message_id, chat_id, e)
            await self._db.clear_message(chat_id, login)

    async def _send_pending_stats(self) -> None:
        now = time.time()
        for (
            chat_id,
            login,
            offline_since,
            title,
            started_at,
            peak_viewers,
            viewer_sum,
            viewer_samples,
            stream_id,
            followers_at_start,
        ) in await self._db.pending_stats():
            if now - offline_since < OFFLINE_GRACE_SECONDS:
                continue
            if await self._db.is_telegram_channel(chat_id):
                # в Telegram-канале нет чата и получателя итогового отчёта — только
                # живой пост о старте стрима, который уже отправлен и отредактирован
                await self._db.mark_stats_sent(chat_id, login)
                continue

            recipient_chat_id = await self._db.resolve_post_recipient(chat_id, login)
            is_exempt = await self._db.get_quiet_hours_exempt(chat_id, login)
            if not is_exempt and await self._is_recipient_in_quiet_hours(recipient_chat_id):
                # получатель сейчас «спит» — не шлём отчёт сразу, а копим его в очередь,
                # чтобы прислать одной сводкой, когда тихие часы закончатся. Каналы,
                # отмеченные как исключение, всегда идут сразу, минуя тихие часы
                await self._db.add_deferred_report(recipient_chat_id, chat_id, login, stream_id, now)
                await self._db.mark_stats_sent(chat_id, login)
                continue

            await self._send_stats(
                chat_id,
                login,
                stream_id,
                title,
                started_at,
                peak_viewers,
                viewer_sum,
                viewer_samples,
                followers_at_start,
            )
            await self._db.mark_stats_sent(chat_id, login)

    async def _is_recipient_in_quiet_hours(self, chat_id: int) -> bool:
        quiet_hours = await self._db.get_quiet_hours(chat_id)
        if quiet_hours is None:
            return False
        start_minute, end_minute, _utc_offset, _notify_after = quiet_hours
        return _is_within_quiet_hours(start_minute, end_minute, datetime.now(timezone.utc))

    async def _send_stats(
        self,
        chat_id: int,
        login: str,
        stream_id: str | None,
        title: str | None,
        started_at: str,
        peak_viewers: int | None,
        viewer_sum: int,
        viewer_samples: int,
        followers_at_start: int | None,
    ) -> None:
        duration_text = self._format_duration(started_at)
        avg_viewers = round(viewer_sum / viewer_samples) if viewer_samples else 0
        peak = peak_viewers or 0
        new_followers_text = await self._compute_new_followers(login, followers_at_start)
        new_followers_num = int(new_followers_text) if new_followers_text is not None else None

        report_html = None
        unique_chatters = 0
        join_reliable = True
        vod_url = None
        top_chatters: list[tuple[str, int]] = []
        raid_events: list[tuple[float, int, str | None]] = []
        viewer_spikes: list[tuple[float, int]] = []
        comparison_lines: list[str] = []
        collab_logins = await self._detect_collab(login, title)
        if stream_id is not None:
            chat_activity = await self._db.get_chat_activity_samples(chat_id, login, stream_id)
            chatter_nicks = await self._db.get_chat_unique_nicks(chat_id, login, stream_id)
            unique_chatters = len(chatter_nicks)
            join_reliable = self._join_reliable_cache.pop((chat_id, login, stream_id), True)
            top_chatters = self._top_chatters_cache.pop((chat_id, login, stream_id), [])
            raid_detection_enabled = await self._db.get_raid_detection_enabled(chat_id, login)
            raw_raid_events = self._raid_events_cache.pop((chat_id, login, stream_id), [])
            raid_events = raw_raid_events if raid_detection_enabled else []
            top_clips = await self._fetch_top_clips(login, started_at)
            vod_url = await self._fetch_and_save_vod(chat_id, login, stream_id, started_at)

            samples = await self._db.get_stream_samples(chat_id, login, stream_id)
            samples, viewer_spikes = _detect_viewer_spikes(samples)
            if viewer_spikes:
                # честный пик/среднее без учёта подозрительных "шипов" — иначе одна
                # накрученная точка искажает статистику всего стрима
                viewer_values = [count for _ts, count, _title, _game in samples]
                if viewer_values:
                    peak = max(viewer_values)
                    avg_viewers = round(sum(viewer_values) / len(viewer_values))

            report_format = await self._db.get_report_format(chat_id, login)
            history = await self._db.get_history_stats(chat_id, login)
            comparison_lines = self._build_comparison(history, peak, avg_viewers)
            if report_format != "brief":
                report_html = build_report_html(
                    login,
                    started_at,
                    duration_text,
                    peak,
                    avg_viewers,
                    samples,
                    new_followers=new_followers_text,
                    chat_activity=chat_activity,
                    unique_chatters=unique_chatters,
                    unique_chatters_reliable=join_reliable,
                    chatter_nicks=chatter_nicks,
                    top_clips=top_clips,
                    vod_url=vod_url,
                    top_chatters=top_chatters,
                    raid_events=raid_events,
                    collab_logins=collab_logins,
                    viewer_spikes=viewer_spikes,
                )

            duration_seconds = self._duration_seconds(started_at)
            await self._db.add_stream_history(
                chat_id, login, stream_id, time.time(), duration_seconds,
                peak, avg_viewers, new_followers_num,
                started_at=started_at, title=title,
                new_followers_text=new_followers_text,
                unique_chatters=unique_chatters, join_reliable=join_reliable,
                top_chatters_json=json.dumps(top_chatters) if top_chatters else None,
                raid_events_json=json.dumps(raid_events) if raid_events else None,
                collab_json=json.dumps(collab_logins) if collab_logins else None,
            )

        collab_label = f" 🤝 (коллаб с {', '.join(html.escape(c) for c in collab_logins)})" if collab_logins else ""
        text = (
            f"📊 Стрим <b>{html.escape(login)}</b> завершён{collab_label}\n\n"
            f"{html.escape(title or '(без названия)')}\n\n"
            f"Длительность: {duration_text}\n"
            f"Пик зрителей: {peak}\n"
            f"Среднее число зрителей: {avg_viewers}"
        )
        if new_followers_text is not None:
            text += f"\nНовых фолловеров: {new_followers_text}"
        if unique_chatters:
            reliability_note = "" if join_reliable else " (возможно занижено)"
            text += f"\nУникальных чатеров: {unique_chatters}{reliability_note}"
        if comparison_lines:
            text += "\n\n" + "\n".join(comparison_lines)
        if top_chatters:
            top_lines = "\n".join(
                f"{i}. {html.escape(nick)} — {count}" for i, (nick, count) in enumerate(top_chatters, 1)
            )
            text += f"\n\n💬 Топ чатеров:\n{top_lines}"
        if raid_events:
            named = [name for _ts, _count, name in raid_events if name]
            if named:
                text += f"\n\n⚡ Рейды: {', '.join(html.escape(n) for n in named)}"
                if len(named) < len(raid_events):
                    text += f" (+{len(raid_events) - len(named)} неопознанных всплесков)"
            else:
                text += f"\n\n⚡ Вероятных рейдов: {len(raid_events)}"
        if viewer_spikes:
            text += (
                f"\n\n🚩 Обнаружен резкий всплеск и такой же резкий спад зрителей "
                f"({len(viewer_spikes)} момент(ов), похоже на накрутку) — не учтён в пике и среднем."
            )
        if vod_url is not None:
            text += f"\n\n🎬 Запись: {html.escape(vod_url)}"

        recipient_chat_id = await self._db.resolve_post_recipient(chat_id, login)
        try:
            await self._bot.send_message(recipient_chat_id, text)
            if report_html is not None:
                file = BufferedInputFile(
                    report_html.encode("utf-8"), filename=f"stream_{login}_{stream_id}.html"
                )
                await self._bot.send_document(recipient_chat_id, file)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Не удалось отправить статистику в чат %s: %s", recipient_chat_id, e)

    @staticmethod
    def _build_comparison(
        history: tuple[int, float, float, int, int] | None, peak: int, avg_viewers: int
    ) -> list[str]:
        if history is None:
            return []
        count, avg_peak, avg_avg, best_peak, best_avg = history

        lines = []
        if avg_peak:
            diff_pct = round((peak - avg_peak) / avg_peak * 100)
            sign = "+" if diff_pct >= 0 else ""
            lines.append(f"Пик зрителей {sign}{diff_pct}% от среднего по прошлым {count} стримам")
        if peak > best_peak:
            lines.append("🏆 Новый рекорд по пиковым зрителям!")
        if avg_viewers > best_avg:
            lines.append("🏆 Новый рекорд по среднему числу зрителей!")
        return lines

    @staticmethod
    def _duration_seconds(started_at: str) -> int:
        try:
            start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return 0
        return int((datetime.now(timezone.utc) - start).total_seconds())

    async def _compute_new_followers(self, login: str, followers_at_start: int | None) -> str | None:
        if self._token_store is None or followers_at_start is None:
            return None
        token = await self._token_store.get_valid_token(login)
        if token is None:
            return None
        broadcaster_id, access_token = token
        try:
            current_count = await self._twitch.get_followers_count(broadcaster_id, access_token)
        except Exception:
            logger.exception("Не удалось получить итоговое число фолловеров для %s", login)
            return None
        diff = current_count - followers_at_start
        return f"+{diff}" if diff >= 0 else str(diff)

    async def _fetch_and_save_vod(
        self, chat_id: int, login: str, stream_id: str, started_at: str
    ) -> str | None:
        """Ищет VOD только что завершённого стрима и сохраняет его вместе с таймкодами
        ключевых моментов (смена игры, пик зрителей) — если у канала запись стрима
        не включена, Twitch просто не отдаст видео с этим stream_id, тогда None."""
        try:
            broadcaster_id = await self._twitch.get_user_id(login)
            if broadcaster_id is None:
                return None
            vod_url = await self._twitch.get_latest_vod_url(broadcaster_id, stream_id)
        except Exception:
            logger.exception("Не удалось получить VOD для %s", login)
            return None
        if vod_url is None:
            return None

        try:
            start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            samples = await self._db.get_stream_samples(chat_id, login, stream_id)
            chapters = _build_vod_chapters(samples, start.timestamp())
        except ValueError:
            chapters = []

        await self._db.save_vod(chat_id, login, stream_id, vod_url, None, json.dumps(chapters))
        return vod_url

    async def _fetch_top_clips(self, login: str, started_at: str) -> list[ClipInfo]:
        """Топ-3 клипа канала за время стрима. Клипы создаются зрителями по горячим
        моментам — их появление на Twitch может немного отставать от реального
        времени, поэтому берём небольшой запас после текущего момента не требуется:
        started_at..сейчас уже покрывает всё, что успели нарезать во время эфира."""
        try:
            broadcaster_id = await self._twitch.get_user_id(login)
            if broadcaster_id is None:
                return []
            ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return await self._twitch.get_top_clips(broadcaster_id, started_at, ended_at)
        except Exception:
            logger.exception("Не удалось получить клипы для %s", login)
            return []

    @staticmethod
    def _format_duration(started_at: str) -> str:
        try:
            start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return "неизвестно"
        seconds = int((datetime.now(timezone.utc) - start).total_seconds())
        hours, remainder = divmod(max(seconds, 0), 3600)
        minutes = remainder // 60
        if hours:
            return f"{hours} ч {minutes} мин"
        return f"{minutes} мин"

    async def _build_keyboard(self, login: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Смотреть на Twitch", url=f"https://twitch.tv/{login}")]
            ]
        )

    async def _channel_display_name(self, login: str) -> str:
        display_name = await self._db.get_display_name(login)
        return display_name or login

    async def _notify(self, chat_id: int, login: str, title: str, viewer_count: int) -> int | None:
        channel_name = await self._channel_display_name(login)
        text = MESSAGE_TEMPLATE.format(
            channel_name=html.escape(channel_name),
            title=html.escape(_strip_links(title)), viewer_count=_keycap_number(viewer_count)
        )
        keyboard = await self._build_keyboard(login)
        try:
            message = await self._bot.send_message(chat_id, text, reply_markup=keyboard)
            return message.message_id
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, e)
            return None

    async def _edit(
        self, chat_id: int, message_id: int, login: str, title: str, viewer_count: int
    ) -> bool:
        """Возвращает False, если сообщение нельзя отредактировать как текст
        (например, старый пост — фото из прошлой версии бота) — в этом случае
        вызывающий код должен пересоздать пост заново."""
        channel_name = await self._channel_display_name(login)
        text = MESSAGE_TEMPLATE.format(
            channel_name=html.escape(channel_name),
            title=html.escape(_strip_links(title)), viewer_count=_keycap_number(viewer_count)
        )
        keyboard = await self._build_keyboard(login)
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard
            )
            return True
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return True
            if "there is no text in the message to edit" in str(e):
                return False
            if "message to edit not found" in str(e):
                # получатель поста сменился (привязку к личке добавили/убрали посреди
                # стрима) — старого сообщения там нет, пересоздаём на новом месте
                return False
            logger.warning("Не удалось отредактировать сообщение %s в чате %s: %s", message_id, chat_id, e)
            return True
        except TelegramForbiddenError as e:
            logger.warning("Не удалось отредактировать сообщение %s в чате %s: %s", message_id, chat_id, e)
            return True
