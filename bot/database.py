from __future__ import annotations

import asyncio
import functools
import time
from dataclasses import dataclass

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken


_ENCRYPTED_TOKEN_PREFIX = "fernet:v1:"


@dataclass(frozen=True)
class ReportDelivery:
    source_chat_id: int
    twitch_login: str
    stream_id: str
    recipient_chat_id: int
    report_format: str
    text_payload: str
    html_payload: str | None
    text_sent: bool
    html_sent: bool
    terminal_failed: bool
    terminal_reason: str | None
    created_at: float
    updated_at: float

    @property
    def complete(self) -> bool:
        return self.text_sent and (
            self.report_format == "brief" or self.html_sent
        )


@dataclass(frozen=True)
class StreamHistoryRecord:
    chat_id: int
    twitch_login: str
    stream_id: str
    ended_at: float
    duration_seconds: int
    peak_viewers: int
    avg_viewers: int
    new_followers: int | None
    started_at: str | None = None
    title: str | None = None
    new_followers_text: str | None = None
    unique_chatters: int | None = None
    join_reliable: bool | None = None
    top_chatters_json: str | None = None
    raid_events_json: str | None = None
    collab_json: str | None = None


def _report_delivery_from_row(row: tuple) -> ReportDelivery:
    return ReportDelivery(
        source_chat_id=row[0],
        twitch_login=row[1],
        stream_id=row[2],
        recipient_chat_id=row[3],
        report_format=row[4],
        text_payload=row[5],
        html_payload=row[6],
        text_sent=bool(row[7]),
        html_sent=bool(row[8]),
        terminal_failed=bool(row[9]),
        terminal_reason=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


def _serialized(func):
    """Пропускает запись в БД строго по одной и откатывает незавершённую при ошибке.

    Соединение с SQLite здесь одно на всё приложение, а поллер и обработчики команд
    работают в общем событийном цикле. Без этой блокировки commit() одной операции
    фиксировал бы наполовину выполненную работу другой: например, между удалением
    каналов и очисткой настроек чата успевал вклиниться чужой commit, и при сбое
    откатить уже было нечего. Блокировка не reentrant — вызывать один метод записи
    из другого нельзя (сейчас таких вызовов нет)."""

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        async with self._write_lock:
            try:
                return await func(self, *args, **kwargs)
            except Exception:
                try:
                    await self.conn.rollback()
                except Exception:
                    pass
                raise

    return wrapper

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_channels (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    is_live INTEGER NOT NULL DEFAULT 0,
    last_stream_id TEXT,
    last_message_id INTEGER,
    last_title TEXT,
    offline_since REAL,
    stream_started_at TEXT,
    last_seen_live_at REAL,
    last_stream_ended_at REAL,
    peak_viewers INTEGER,
    viewer_sum INTEGER NOT NULL DEFAULT 0,
    viewer_samples INTEGER NOT NULL DEFAULT 0,
    stats_sent INTEGER NOT NULL DEFAULT 0,
    followers_at_start INTEGER,
    notify_enabled INTEGER NOT NULL DEFAULT 1,
    post_recipient_chat_id INTEGER,
    report_format TEXT NOT NULL DEFAULT 'full',
    raid_detection_enabled INTEGER NOT NULL DEFAULT 1,
    quiet_hours_exempt INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, twitch_login)
);

-- поллер каждую минуту спрашивает «в каких чатах следят за этим логином»;
-- без индекса это полное сканирование таблицы на каждый канал
CREATE INDEX IF NOT EXISTS idx_tracked_channels_login
    ON tracked_channels (twitch_login);

CREATE TABLE IF NOT EXISTS twitch_user_tokens (
    twitch_login TEXT PRIMARY KEY,
    broadcaster_id TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at REAL NOT NULL
);

-- Точный счётчик подписок за стрим. EventSub доставляет события как минимум один
-- раз, поэтому message_id хранится отдельно и защищает счётчик от дублей.
CREATE TABLE IF NOT EXISTS follow_event_counts (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    reliable INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (chat_id, twitch_login, stream_id)
);
CREATE INDEX IF NOT EXISTS idx_follow_event_counts_retention
    ON follow_event_counts (created_at);

CREATE TABLE IF NOT EXISTS follow_event_ids (
    message_id TEXT PRIMARY KEY,
    received_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_follow_event_ids_retention
    ON follow_event_ids (received_at);

CREATE TABLE IF NOT EXISTS stream_samples (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    sampled_at REAL NOT NULL,
    viewer_count INTEGER NOT NULL,
    title TEXT NOT NULL,
    game_name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stream_samples_lookup
    ON stream_samples (chat_id, twitch_login, stream_id, sampled_at);
CREATE INDEX IF NOT EXISTS idx_stream_samples_retention
    ON stream_samples (sampled_at);

CREATE TABLE IF NOT EXISTS stream_history (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    ended_at REAL NOT NULL,
    duration_seconds INTEGER NOT NULL,
    peak_viewers INTEGER NOT NULL,
    avg_viewers INTEGER NOT NULL,
    new_followers INTEGER,
    started_at TEXT,
    title TEXT,
    new_followers_text TEXT,
    unique_chatters INTEGER,
    join_reliable INTEGER,
    top_chatters_json TEXT,
    raid_events_json TEXT,
    collab_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_stream_history_lookup
    ON stream_history (chat_id, twitch_login, ended_at);

-- Persistent outbox для автоматических итоговых отчётов. Payload хранится здесь,
-- чтобы после рестарта повторить только недоставленную часть, не полагаясь на уже
-- очищенное состояние активной Twitch-сессии.
CREATE TABLE IF NOT EXISTS report_deliveries (
    source_chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    recipient_chat_id INTEGER NOT NULL,
    report_format TEXT NOT NULL CHECK (report_format IN ('brief', 'full')),
    text_payload TEXT NOT NULL,
    html_payload TEXT,
    text_sent INTEGER NOT NULL DEFAULT 0,
    html_sent INTEGER NOT NULL DEFAULT 0,
    terminal_failed INTEGER NOT NULL DEFAULT 0,
    terminal_reason TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source_chat_id, twitch_login, stream_id)
);
CREATE INDEX IF NOT EXISTS idx_report_deliveries_cleanup
    ON report_deliveries (updated_at)
    WHERE terminal_failed = 1
       OR (text_sent = 1 AND (report_format = 'brief' OR html_sent = 1));

CREATE TABLE IF NOT EXISTS chat_activity_samples (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    minute_ts REAL NOT NULL,
    message_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_activity_lookup
    ON chat_activity_samples (chat_id, twitch_login, stream_id);
CREATE INDEX IF NOT EXISTS idx_chat_activity_retention
    ON chat_activity_samples (minute_ts);

CREATE TABLE IF NOT EXISTS chat_unique_nicks (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    nick TEXT NOT NULL,
    joined_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_unique_nicks_lookup
    ON chat_unique_nicks (chat_id, twitch_login, stream_id);
CREATE INDEX IF NOT EXISTS idx_chat_unique_nicks_retention
    ON chat_unique_nicks (joined_at);

-- ники чатеров привязаны к стриму, а не к чату: за одним стримером могут следить
-- несколько чатов, и раньше один и тот же список писался для каждого из них —
-- на крупном канале это сотни тысяч лишних строк за один эфир
CREATE TABLE IF NOT EXISTS stream_chatters (
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    nick TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    PRIMARY KEY (twitch_login, stream_id, nick)
);
CREATE INDEX IF NOT EXISTS idx_stream_chatters_retention
    ON stream_chatters (first_seen_at);

-- данные чата за завершённый стрим, ждущие отправки итогового отчёта.
-- Раньше лежали в словарях в памяти поллера: терялись при рестарте и не
-- освобождались вовсе, если отчёт откладывался тихими часами
CREATE TABLE IF NOT EXISTS stream_chat_meta (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    join_reliable INTEGER,
    top_chatters_json TEXT,
    raid_events_json TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (chat_id, twitch_login, stream_id)
);
CREATE INDEX IF NOT EXISTS idx_stream_chat_meta_retention
    ON stream_chat_meta (created_at);

CREATE TABLE IF NOT EXISTS stats_recipients (
    chat_id INTEGER PRIMARY KEY,
    stats_chat_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS known_private_users (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS channel_existence_status (
    twitch_login TEXT PRIMARY KEY,
    exists_on_twitch INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS channel_display_names (
    twitch_login TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vod_archive (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    vod_url TEXT NOT NULL,
    vod_title TEXT,
    chapters_json TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (chat_id, twitch_login, stream_id)
);

CREATE TABLE IF NOT EXISTS telegram_channels (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiet_hours (
    chat_id INTEGER PRIMARY KEY,
    start_minute INTEGER NOT NULL,
    end_minute INTEGER NOT NULL,
    utc_offset_minutes INTEGER NOT NULL DEFAULT 0,
    notify_after_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS deferred_reports (
    chat_id INTEGER NOT NULL,
    source_chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL DEFAULT '',
    ended_at REAL NOT NULL,
    PRIMARY KEY (chat_id, source_chat_id, twitch_login, stream_id)
);

CREATE TABLE IF NOT EXISTS quiet_hours_digest_sent (
    chat_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS user_timezones (
    chat_id INTEGER PRIMARY KEY,
    utc_offset_minutes INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str, token_encryption_key: str | None = None) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        try:
            self._token_cipher = Fernet(token_encryption_key) if token_encryption_key else None
        except (ValueError, TypeError) as e:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY не является корректным Fernet-ключом") from e

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    @_serialized
    async def _migrate(self) -> None:
        """Добавляет колонки, появившиеся в схеме уже после первого релиза
        (CREATE TABLE IF NOT EXISTS не меняет существующие таблицы)."""
        await self._add_missing_columns(
            "stream_history",
            {
                "started_at": "TEXT",
                "title": "TEXT",
                "new_followers_text": "TEXT",
                "unique_chatters": "INTEGER",
                "join_reliable": "INTEGER",
                "top_chatters_json": "TEXT",
                "raid_events_json": "TEXT",
                "collab_json": "TEXT",
            },
        )
        await self._dedupe_stream_history()
        await self._add_missing_columns(
            "tracked_channels",
            {
                "notify_enabled": "INTEGER NOT NULL DEFAULT 1",
                "post_recipient_chat_id": "INTEGER",
                "report_format": "TEXT NOT NULL DEFAULT 'full'",
                "raid_detection_enabled": "INTEGER NOT NULL DEFAULT 1",
                "quiet_hours_exempt": "INTEGER NOT NULL DEFAULT 0",
                "last_stream_ended_at": "REAL",
                "last_seen_live_at": "REAL",
            },
        )
        # Для БД, созданной до появления last_seen_live_at, восстанавливаем момент
        # последнего успешного live-poll из уже сохранённых samples. Это позволяет
        # корректно распознать reconnect прямо на первом запуске новой версии.
        await self.conn.execute(
            "UPDATE tracked_channels SET last_seen_live_at = ("
            "SELECT MAX(sampled_at) FROM stream_samples s "
            "WHERE s.chat_id = tracked_channels.chat_id "
            "AND s.twitch_login = tracked_channels.twitch_login "
            "AND s.stream_id = tracked_channels.last_stream_id) "
            "WHERE last_seen_live_at IS NULL AND last_stream_id IS NOT NULL"
        )
        await self._migrate_user_tokens_encryption()
        await self._migrate_deferred_reports_key()
        # До этого релиза интерфейс позволял выбрать группу получателем итогов.
        # Удаляем только маршруты доставки и отложенные групповые отправки; сама
        # история стримов остаётся нетронутой и доступна через /report в личке.
        await self.conn.execute(
            "UPDATE tracked_channels SET post_recipient_chat_id = NULL "
            "WHERE post_recipient_chat_id < 0 AND NOT EXISTS ("
            "SELECT 1 FROM telegram_channels c WHERE c.chat_id = tracked_channels.chat_id)"
        )
        await self.conn.execute(
            "DELETE FROM stats_recipients WHERE stats_chat_id < 0 AND NOT EXISTS ("
            "SELECT 1 FROM telegram_channels c WHERE c.chat_id = stats_recipients.chat_id)"
        )
        await self.conn.execute(
            "DELETE FROM deferred_reports WHERE chat_id < 0 AND NOT EXISTS ("
            "SELECT 1 FROM telegram_channels c WHERE c.chat_id = deferred_reports.chat_id)"
        )
        await self.conn.execute(
            "DELETE FROM quiet_hours_digest_sent WHERE chat_id < 0 AND NOT EXISTS ("
            "SELECT 1 FROM telegram_channels c "
            "WHERE c.chat_id = quiet_hours_digest_sent.chat_id)"
        )
        await self.conn.commit()

    def _encrypt_token(self, value: str) -> str:
        if self._token_cipher is None or value.startswith(_ENCRYPTED_TOKEN_PREFIX):
            return value
        encrypted = self._token_cipher.encrypt(value.encode("utf-8")).decode("ascii")
        return _ENCRYPTED_TOKEN_PREFIX + encrypted

    def _decrypt_token(self, value: str) -> str:
        if not value.startswith(_ENCRYPTED_TOKEN_PREFIX):
            return value
        if self._token_cipher is None:
            raise RuntimeError(
                "В базе есть зашифрованные Twitch-токены, но TOKEN_ENCRYPTION_KEY не задан"
            )
        payload = value[len(_ENCRYPTED_TOKEN_PREFIX):]
        try:
            return self._token_cipher.decrypt(payload.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as e:
            raise RuntimeError(
                "Не удалось расшифровать Twitch-токены: проверь TOKEN_ENCRYPTION_KEY"
            ) from e

    async def _migrate_user_tokens_encryption(self) -> None:
        cursor = await self.conn.execute(
            "SELECT twitch_login, access_token, refresh_token FROM twitch_user_tokens"
        )
        rows = await cursor.fetchall()
        if self._token_cipher is None:
            if any(
                access.startswith(_ENCRYPTED_TOKEN_PREFIX)
                or refresh.startswith(_ENCRYPTED_TOKEN_PREFIX)
                for _login, access, refresh in rows
            ):
                raise RuntimeError(
                    "В базе есть зашифрованные Twitch-токены, но TOKEN_ENCRYPTION_KEY не задан"
                )
            return
        for login, access_token, refresh_token in rows:
            encrypted_access = self._encrypt_token(access_token)
            encrypted_refresh = self._encrypt_token(refresh_token)
            if encrypted_access != access_token or encrypted_refresh != refresh_token:
                await self.conn.execute(
                    "UPDATE twitch_user_tokens SET access_token = ?, refresh_token = ? "
                    "WHERE twitch_login = ?",
                    (encrypted_access, encrypted_refresh, login),
                )

    async def _dedupe_stream_history(self) -> None:
        """Убирает задвоенные записи об одном и том же стриме и запрещает их впредь.

        Отчёт отправлялся, а отметка «отправлено» ставилась следующей строкой: если
        процесс умирал между ними (а Railway перезапускает бота на каждом деплое),
        после старта отчёт уходил повторно и в историю падала вторая запись. Дубликаты
        тихо искажали «% от среднего» и «новый рекорд», поэтому старые чистим, а
        уникальный индекс не даёт появиться новым."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_stream_history_unique'"
        )
        if (await cursor.fetchone())[0]:
            return

        # из каждой группы дублей оставляем самую раннюю запись — она соответствует
        # первой, настоящей отправке отчёта
        await self.conn.execute(
            "DELETE FROM stream_history WHERE rowid NOT IN ("
            "  SELECT MIN(rowid) FROM stream_history"
            "  GROUP BY chat_id, twitch_login, stream_id"
            ")"
        )
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_stream_history_unique "
            "ON stream_history (chat_id, twitch_login, stream_id)"
        )

    async def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        cursor = await self.conn.execute(f"PRAGMA table_info({table})")
        existing_columns = {row[1] for row in await cursor.fetchall()}
        for name, sql_type in columns.items():
            if name not in existing_columns:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() ещё не вызван"
        return self._conn

    @_serialized
    async def add_channel(self, chat_id: int, twitch_login: str) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO tracked_channels (chat_id, twitch_login) VALUES (?, ?)",
                (chat_id, twitch_login),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            # Ограничение уникальности отменяет только сам INSERT, но оставляет
            # транзакцию открытой. Явно закрываем её до следующей операции.
            await self.conn.rollback()
            return False

    @_serialized
    async def remove_channel(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @_serialized
    async def remove_all_channels(self, chat_id: int) -> int:
        """Снимает с отслеживания всё в этом чате и убирает связанные с ним настройки —
        вызывается, когда бота удалили из группы. Возвращает число снятых каналов."""
        cursor = await self.conn.execute(
            "DELETE FROM tracked_channels WHERE chat_id = ?", (chat_id,)
        )
        removed = cursor.rowcount
        for table in (
            "stats_recipients", "quiet_hours", "deferred_reports",
            "quiet_hours_digest_sent", "stream_chat_meta",
        ):
            await self.conn.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()
        return removed

    async def list_channels(self, chat_id: int) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT twitch_login FROM tracked_channels WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def list_channels_with_notify(self, chat_id: int) -> list[tuple[str, bool, bool]]:
        """(twitch_login, notify_enabled, is_live) для всех каналов чата."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, notify_enabled, is_live FROM tracked_channels "
            "WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [(row[0], bool(row[1]), bool(row[2])) for row in rows]

    async def list_live_channels(self, chat_id: int) -> list[tuple[str, str, int | None, str | None]]:
        """(twitch_login, title, viewer_count, game_name) для каналов чата, которые сейчас
        в эфире. viewer_count/game_name — из последнего опроса, None если сэмплов ещё не было."""
        cursor = await self.conn.execute(
            "SELECT tc.twitch_login, tc.last_title, "
            "(SELECT ss.viewer_count FROM stream_samples ss "
            " WHERE ss.chat_id = tc.chat_id AND ss.twitch_login = tc.twitch_login "
            " AND ss.stream_id = tc.last_stream_id "
            " ORDER BY ss.sampled_at DESC LIMIT 1) AS viewer_count, "
            "(SELECT ss.game_name FROM stream_samples ss "
            " WHERE ss.chat_id = tc.chat_id AND ss.twitch_login = tc.twitch_login "
            " AND ss.stream_id = tc.last_stream_id "
            " ORDER BY ss.sampled_at DESC LIMIT 1) AS game_name "
            "FROM tracked_channels tc "
            "WHERE tc.chat_id = ? AND tc.is_live = 1 "
            "ORDER BY tc.twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [(row[0], row[1] or "", row[2], row[3] if row[3] != "—" else None) for row in rows]

    async def get_last_stream_end(self, chat_id: int, twitch_login: str) -> float | None:
        """Когда закончился прошлый стрим этого канала в этом чате (unix ts).
        None, если история пуста — канал добавили недавно и он ещё не стримил."""
        cursor = await self.conn.execute(
            "SELECT MAX(ended_at) FROM stream_history WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    async def list_channels_with_routing(
        self, chat_id: int
    ) -> list[tuple[str, bool, int | None, str, bool, bool, bool]]:
        """(twitch_login, notify_enabled, post_recipient_chat_id, report_format,
        raid_detection_enabled, quiet_hours_exempt, is_live) для всех каналов чата."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, notify_enabled, post_recipient_chat_id, report_format, "
            "raid_detection_enabled, quiet_hours_exempt, is_live FROM tracked_channels "
            "WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [
            (row[0], bool(row[1]), row[2], row[3] or "full", bool(row[4]), bool(row[5]), bool(row[6]))
            for row in rows
        ]

    @_serialized
    async def set_notify_enabled(self, chat_id: int, twitch_login: str, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET notify_enabled = ? WHERE chat_id = ? AND twitch_login = ?",
            (int(enabled), chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_notify_enabled(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT notify_enabled FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else True

    @_serialized
    async def set_report_format(self, chat_id: int, twitch_login: str, report_format: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET report_format = ? WHERE chat_id = ? AND twitch_login = ?",
            (report_format, chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_report_format(self, chat_id: int, twitch_login: str) -> str:
        """'full' (текст + HTML-отчёт) или 'brief' (только текст). По умолчанию 'full'."""
        cursor = await self.conn.execute(
            "SELECT report_format FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else "full"

    @_serialized
    async def set_raid_detection_enabled(self, chat_id: int, twitch_login: str, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET raid_detection_enabled = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (int(enabled), chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_raid_detection_enabled(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT raid_detection_enabled FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else True

    @_serialized
    async def set_quiet_hours_exempt(self, chat_id: int, twitch_login: str, exempt: bool) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET quiet_hours_exempt = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (int(exempt), chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_quiet_hours_exempt(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT quiet_hours_exempt FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    @_serialized
    async def set_post_recipient(
        self, chat_id: int, twitch_login: str, recipient_chat_id: int | None
    ) -> None:
        """recipient_chat_id=None сбрасывает явную привязку — канал возвращается
        к общей личной привязке чата (stats_recipients). Отрицательные получатели
        сохраняться могут только из старого клиента, но доставкой игнорируются."""
        await self.conn.execute(
            "UPDATE tracked_channels SET post_recipient_chat_id = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (recipient_chat_id, chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_post_recipient(self, chat_id: int, twitch_login: str) -> int | None:
        """Явная привязка получателя постов для конкретного канала, если задана."""
        cursor = await self.conn.execute(
            "SELECT post_recipient_chat_id FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    async def resolve_post_recipient(self, chat_id: int, twitch_login: str) -> int | None:
        """Получатель итогового отчёта — только личный Telegram-чат.

        Положительный chat_id означает личку. Обычная группа никогда не становится
        получателем, но зарегистрированный Telegram-канал сохраняет отдельное
        поведение: живой пост удаляется отдельно, а итог публикуется новым сообщением.
        """
        per_channel = await self.get_post_recipient(chat_id, twitch_login)
        if per_channel is not None and per_channel > 0:
            return per_channel
        default_recipient = await self.get_stats_recipient(chat_id)
        if default_recipient is not None and default_recipient > 0:
            return default_recipient
        if await self.is_telegram_channel(chat_id):
            return chat_id
        return chat_id if chat_id > 0 else None

    async def count_channels(self, chat_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM tracked_channels WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def all_distinct_logins(self) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT DISTINCT twitch_login FROM tracked_channels"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def all_distinct_live_logins(self) -> list[str]:
        """Логины, отмеченные в БД как is_live=1 — используется при старте бота,
        чтобы возобновить сбор чат-активности для стримов, уже шедших до перезапуска."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT twitch_login FROM tracked_channels WHERE is_live = 1"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_existence_status(self, twitch_login: str) -> bool:
        """Последний известный статус «канал существует на Twitch» (не забанен/удалён).
        По умолчанию True — первая проверка не должна считаться переходом в бан."""
        cursor = await self.conn.execute(
            "SELECT exists_on_twitch FROM channel_existence_status WHERE twitch_login = ?",
            (twitch_login,),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else True

    @_serialized
    async def set_existence_status(self, twitch_login: str, exists: bool) -> None:
        await self.conn.execute(
            "INSERT INTO channel_existence_status (twitch_login, exists_on_twitch) VALUES (?, ?) "
            "ON CONFLICT(twitch_login) DO UPDATE SET exists_on_twitch = excluded.exists_on_twitch",
            (twitch_login, int(exists)),
        )
        await self.conn.commit()

    async def chats_for_login(self, twitch_login: str) -> list[int]:
        cursor = await self.conn.execute(
            "SELECT chat_id FROM tracked_channels WHERE twitch_login = ?",
            (twitch_login,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def all_distinct_group_chat_ids(self) -> list[int]:
        """chat_id всех групп/каналов (не личных чатов) с хотя бы одним отслеживаемым
        каналом — в Telegram id групп и каналов отрицательные, личных чатов положительные."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT chat_id FROM tracked_channels WHERE chat_id < 0"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    @_serialized
    async def register_telegram_channel(self, chat_id: int, title: str) -> None:
        """Запоминает Telegram-канал (не группу), куда бот добавлен админом —
        срабатывает на my_chat_member update, поскольку в канале нет способа
        узнать о боте иначе (читатели канала не пишут сообщений боту)."""
        await self.conn.execute(
            "INSERT INTO telegram_channels (chat_id, title) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title",
            (chat_id, title),
        )
        await self.conn.commit()

    @_serialized
    async def unregister_telegram_channel(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM telegram_channels WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def is_telegram_channel(self, chat_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM telegram_channels WHERE chat_id = ?", (chat_id,)
        )
        return await cursor.fetchone() is not None

    async def all_telegram_channels(self) -> list[tuple[int, str]]:
        """(chat_id, title) всех известных Telegram-каналов, где бот когда-то был админом."""
        cursor = await self.conn.execute("SELECT chat_id, title FROM telegram_channels")
        return await cursor.fetchall()

    @_serialized
    async def set_quiet_hours(
        self, chat_id: int, start_minute: int, end_minute: int, utc_offset_minutes: int
    ) -> None:
        """start_minute/end_minute — минуты от полуночи UTC (0..1439). Если интервал
        переходит через полночь (например, 23:00-08:00), start_minute > end_minute —
        это разрешено, проверка diapазона учитывает такой случай."""
        await self.conn.execute(
            "INSERT INTO quiet_hours (chat_id, start_minute, end_minute, utc_offset_minutes) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "start_minute = excluded.start_minute, end_minute = excluded.end_minute, "
            "utc_offset_minutes = excluded.utc_offset_minutes",
            (chat_id, start_minute, end_minute, utc_offset_minutes),
        )
        await self.conn.commit()

    @_serialized
    async def clear_quiet_hours(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM quiet_hours WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def get_quiet_hours(self, chat_id: int) -> tuple[int, int, int, bool] | None:
        """(start_minute, end_minute, utc_offset_minutes, notify_after_enabled) или None,
        если тихие часы не настроены."""
        cursor = await self.conn.execute(
            "SELECT start_minute, end_minute, utc_offset_minutes, notify_after_enabled "
            "FROM quiet_hours WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2], bool(row[3])

    @_serialized
    async def set_quiet_hours_notify_after(self, chat_id: int, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE quiet_hours SET notify_after_enabled = ? WHERE chat_id = ?",
            (int(enabled), chat_id),
        )
        await self.conn.commit()

    async def all_quiet_hours_chat_ids(self) -> list[int]:
        cursor = await self.conn.execute("SELECT chat_id FROM quiet_hours")
        return [row[0] for row in await cursor.fetchall()]

    @_serialized
    async def add_deferred_report(
        self, chat_id: int, source_chat_id: int, twitch_login: str, stream_id: str | None, ended_at: float
    ) -> None:
        await self.conn.execute(
            "INSERT INTO deferred_reports (chat_id, source_chat_id, twitch_login, stream_id, ended_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, source_chat_id, twitch_login, stream_id) DO UPDATE SET "
            "ended_at = excluded.ended_at",
            (chat_id, source_chat_id, twitch_login, stream_id or "", ended_at),
        )
        await self.conn.execute(
            "DELETE FROM quiet_hours_digest_sent WHERE chat_id = ?", (chat_id,)
        )
        await self.conn.commit()

    @_serialized
    async def relocate_deferred_report(
        self,
        old_chat_id: int,
        new_chat_id: int,
        source_chat_id: int,
        twitch_login: str,
        stream_id: str | None,
        ended_at: float,
    ) -> None:
        normalized_stream_id = stream_id or ""
        await self.conn.execute(
            "INSERT INTO deferred_reports "
            "(chat_id, source_chat_id, twitch_login, stream_id, ended_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, source_chat_id, twitch_login, stream_id) "
            "DO UPDATE SET ended_at = excluded.ended_at",
            (
                new_chat_id,
                source_chat_id,
                twitch_login,
                normalized_stream_id,
                ended_at,
            ),
        )
        await self.conn.execute(
            "DELETE FROM deferred_reports WHERE chat_id = ? AND source_chat_id = ? "
            "AND twitch_login = ? AND stream_id = ?",
            (old_chat_id, source_chat_id, twitch_login, normalized_stream_id),
        )
        await self.conn.execute(
            "DELETE FROM quiet_hours_digest_sent WHERE chat_id IN (?, ?)",
            (old_chat_id, new_chat_id),
        )
        await self.conn.commit()

    @_serialized
    async def delete_deferred_report(
        self,
        chat_id: int,
        source_chat_id: int,
        twitch_login: str,
        stream_id: str | None,
    ) -> None:
        await self.conn.execute(
            "DELETE FROM deferred_reports WHERE chat_id = ? AND source_chat_id = ? "
            "AND twitch_login = ? AND stream_id = ?",
            (chat_id, source_chat_id, twitch_login, stream_id or ""),
        )
        await self.conn.commit()

    @_serialized
    async def get_and_clear_deferred_reports(
        self, chat_id: int
    ) -> list[tuple[int, str, str | None, float]]:
        """(source_chat_id, twitch_login, stream_id, ended_at) — очищает очередь."""
        cursor = await self.conn.execute(
            "SELECT source_chat_id, twitch_login, stream_id, ended_at "
            "FROM deferred_reports WHERE chat_id = ? ORDER BY ended_at ASC",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        await self.conn.execute("DELETE FROM deferred_reports WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()
        return rows

    async def has_deferred_reports(self, chat_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM deferred_reports WHERE chat_id = ? LIMIT 1", (chat_id,)
        )
        return await cursor.fetchone() is not None

    async def peek_deferred_reports(self, chat_id: int) -> list[tuple[int, str, str | None, float]]:
        """Как get_and_clear_deferred_reports, но не удаляет записи — используется
        для показа сводки, оставляя данные в БД до ответа пользователя на кнопки."""
        cursor = await self.conn.execute(
            "SELECT source_chat_id, twitch_login, stream_id, ended_at "
            "FROM deferred_reports WHERE chat_id = ? ORDER BY ended_at ASC",
            (chat_id,),
        )
        return [
            (source_chat_id, login, stream_id or None, ended_at)
            for source_chat_id, login, stream_id, ended_at in await cursor.fetchall()
        ]

    async def all_deferred_recipient_chat_ids(self) -> list[int]:
        cursor = await self.conn.execute(
            "SELECT DISTINCT chat_id FROM deferred_reports ORDER BY chat_id"
        )
        return [row[0] for row in await cursor.fetchall()]

    async def is_quiet_hours_digest_sent(self, chat_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM quiet_hours_digest_sent WHERE chat_id = ?", (chat_id,)
        )
        return await cursor.fetchone() is not None

    @_serialized
    async def mark_quiet_hours_digest_sent(self, chat_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO quiet_hours_digest_sent (chat_id) VALUES (?)", (chat_id,)
        )
        await self.conn.commit()

    @_serialized
    async def clear_quiet_hours_digest_sent(self, chat_id: int) -> None:
        await self.conn.execute("DELETE FROM quiet_hours_digest_sent WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def get_utc_offset(self, chat_id: int) -> int | None:
        """Смещение от UTC в минутах, заданное пользователем один раз при первой
        настройке тихих часов — используется, чтобы показывать/принимать время
        в его локальном часовом поясе."""
        cursor = await self.conn.execute(
            "SELECT utc_offset_minutes FROM user_timezones WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    @_serialized
    async def set_utc_offset(self, chat_id: int, utc_offset_minutes: int) -> None:
        await self.conn.execute(
            "INSERT INTO user_timezones (chat_id, utc_offset_minutes) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET utc_offset_minutes = excluded.utc_offset_minutes",
            (chat_id, utc_offset_minutes),
        )
        await self.conn.commit()

    async def snapshot_tracked_state(
        self,
    ) -> tuple[dict[str, list[int]], dict[tuple[int, str], tuple]]:
        """Состояние всех отслеживаемых каналов одним запросом.

        Цикл опроса раньше дёргал БД отдельно на каждую пару «канал × чат»
        (состояние, флаг уведомлений, число фолловеров) плюс на каждый логин
        за списком чатов — при тысячах подписок это тысячи запросов в минуту.
        Возвращает ({login: [chat_id, ...]}, {(chat_id, login): состояние})."""
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, is_live, last_stream_id, last_message_id, "
            "last_title, offline_since, stream_started_at, last_seen_live_at, peak_viewers, "
            "notify_enabled, followers_at_start, stats_sent "
            "FROM tracked_channels ORDER BY twitch_login, chat_id"
        )
        chats_by_login: dict[str, list[int]] = {}
        states: dict[tuple[int, str], tuple] = {}
        for row in await cursor.fetchall():
            chat_id, login = row[0], row[1]
            chats_by_login.setdefault(login, []).append(chat_id)
            states[(chat_id, login)] = (
                bool(row[2]), row[3], row[4], row[5], row[6], row[7], row[8], row[9],
                bool(row[10]), row[11], bool(row[12]),
            )
        return chats_by_login, states

    async def snapshot_last_stream_ends(self) -> dict[tuple[int, str], float]:
        """Когда бот в последний раз видел завершение стрима.

        Не подмешиваем ``stream_history``: у существующих Telegram-каналов она
        неполная, потому что старый код намеренно не создавал для них отчёты.
        Лучше один раз не показать метку после миграции, чем снова заявить о
        многодневном перерыве на основании заведомо устаревшей истории.
        """
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, last_stream_ended_at "
            "FROM tracked_channels WHERE last_stream_ended_at IS NOT NULL"
        )
        return {(row[0], row[1]): row[2] for row in await cursor.fetchall() if row[2] is not None}

    async def telegram_channel_ids(self) -> set[int]:
        cursor = await self.conn.execute("SELECT chat_id FROM telegram_channels")
        return {row[0] for row in await cursor.fetchall()}

    async def get_live_state(
        self, chat_id: int, twitch_login: str
    ) -> tuple[bool, str | None, int | None, str | None, float | None, str | None, int | None]:
        cursor = await self.conn.execute(
            "SELECT is_live, last_stream_id, last_message_id, last_title, offline_since, "
            "stream_started_at, peak_viewers "
            "FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        if row is None:
            return False, None, None, None, None, None, None
        return bool(row[0]), row[1], row[2], row[3], row[4], row[5], row[6]

    @_serialized
    async def set_live_state(
        self,
        chat_id: int,
        twitch_login: str,
        is_live: bool,
        stream_id: str | None,
        message_id: int | None = None,
        title: str | None = None,
        offline_since: float | None = None,
        stream_started_at: str | None = None,
        peak_viewers: int | None = None,
        last_seen_live_at: float | None = None,
    ) -> None:
        # при старте нового стрима (is_live=True и меняется stream_id) обнуляем накопленную
        # сумму зрителей — CASE проверяет, отличается ли stream_id от того, что уже в базе.
        # peak_viewers обновляется, только если явно передан (не None) — иначе, при вызове
        # без этого параметра на каждой итерации опроса, он бы затирался в NULL прямо перед
        # тем, как record_viewer_sample успевает честно накопить в нём максимум за стрим.
        await self.conn.execute(
            "UPDATE tracked_channels SET is_live = ?, last_stream_id = ?, "
            "last_message_id = ?, last_title = ?, offline_since = ?, "
            "stream_started_at = ?, "
            "last_seen_live_at = CASE "
            "    WHEN ? IS NOT NULL THEN ? "
            "    WHEN ? AND (last_stream_id IS NULL OR last_stream_id != ?) THEN NULL "
            "    ELSE last_seen_live_at END, "
            "peak_viewers = CASE "
            "    WHEN ? IS NOT NULL THEN ? "
            "    WHEN ? AND (last_stream_id IS NULL OR last_stream_id != ?) THEN NULL "
            "    ELSE peak_viewers END, "
            "stats_sent = CASE WHEN ? THEN 0 ELSE stats_sent END, "
            "viewer_sum = CASE WHEN ? AND (last_stream_id IS NULL OR last_stream_id != ?) "
            "    THEN 0 ELSE viewer_sum END, "
            "viewer_samples = CASE WHEN ? AND (last_stream_id IS NULL OR last_stream_id != ?) "
            "    THEN 0 ELSE viewer_samples END, "
            "followers_at_start = CASE WHEN ? AND (last_stream_id IS NULL OR last_stream_id != ?) "
            "    THEN NULL ELSE followers_at_start END "
            "WHERE chat_id = ? AND twitch_login = ?",
            (
                int(is_live), stream_id, message_id, title, offline_since,
                stream_started_at,
                last_seen_live_at, last_seen_live_at, int(is_live), stream_id,
                peak_viewers, peak_viewers, int(is_live), stream_id,
                int(is_live),
                int(is_live), stream_id, int(is_live), stream_id,
                int(is_live), stream_id,
                chat_id, twitch_login,
            ),
        )
        await self.conn.commit()

    @_serialized
    async def record_viewer_sample(self, chat_id: int, twitch_login: str, viewer_count: int) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET "
            "peak_viewers = MAX(COALESCE(peak_viewers, 0), ?), "
            "viewer_sum = viewer_sum + ?, "
            "viewer_samples = viewer_samples + 1 "
            "WHERE chat_id = ? AND twitch_login = ?",
            (viewer_count, viewer_count, chat_id, twitch_login),
        )
        await self.conn.commit()

    async def pending_offline_posts(self) -> list[tuple[int, str, int, float, bool]]:
        """Live-посты офлайн-каналов, ожидающие удаления.

        Удаление Telegram-сообщения не зависит от готовности итогового отчёта:
        ``stats_sent`` возвращается только для выбора между очисткой ссылки на пост
        и окончательной очисткой уже финализированной сессии.
        """
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, last_message_id, offline_since, stats_sent "
            "FROM tracked_channels "
            "WHERE is_live = 0 AND offline_since IS NOT NULL "
            "AND last_message_id IS NOT NULL"
        )
        return [(*row[:4], bool(row[4])) for row in await cursor.fetchall()]

    async def pending_stats(
        self, limit: int | None = None
    ) -> list[tuple[int, str, float, str, str, int, int, int, str, int | None]]:
        """(chat_id, twitch_login, offline_since, title, started_at, peak_viewers,
        viewer_sum, viewer_samples, last_stream_id, followers_at_start) для неотправленной
        статистики. limit ограничивает порцию за один круг опроса: каждый отчёт — это
        запросы к Twitch за клипами и записью плюс сборка HTML, и без ограничения
        десяток одновременно закончившихся стримов занимал бы цикл на минуты, а живые
        посты в это время не обновлялись бы. Самые старые идут первыми."""
        sql = (
            "SELECT chat_id, twitch_login, offline_since, last_title, stream_started_at, "
            "peak_viewers, viewer_sum, viewer_samples, last_stream_id, followers_at_start "
            "FROM tracked_channels "
            "WHERE is_live = 0 AND offline_since IS NOT NULL AND stats_sent = 0 "
            "AND stream_started_at IS NOT NULL "
            "ORDER BY offline_since ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await self.conn.execute(sql, params)
        return await cursor.fetchall()

    @_serialized
    async def mark_stats_sent(self, chat_id: int, twitch_login: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET stats_sent = 1, "
            "last_stream_ended_at = COALESCE(offline_since, last_stream_ended_at) "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

    async def _insert_report_delivery(
        self,
        source_chat_id: int,
        twitch_login: str,
        stream_id: str,
        recipient_chat_id: int,
        report_format: str,
        text_payload: str,
        html_payload: str | None,
        created_at: float,
    ) -> ReportDelivery:
        """Вставляет automatic delivery внутри уже открытой write-транзакции.

        Recipient и payload замораживаются первой строкой для logical report. Повторная
        попытка с изменившимся routing возвращает исходную строку, а не создаёт вторую.
        """
        if report_format not in {"brief", "full"}:
            raise ValueError(f"Неизвестный формат отчёта: {report_format}")
        if report_format == "full" and html_payload is None:
            raise ValueError("Для full delivery требуется сохранённый HTML payload")
        await self.conn.execute(
            "INSERT OR IGNORE INTO report_deliveries ("
            "source_chat_id, twitch_login, stream_id, recipient_chat_id, "
            "report_format, text_payload, html_payload, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_chat_id,
                twitch_login,
                stream_id,
                recipient_chat_id,
                report_format,
                text_payload,
                html_payload,
                created_at,
                created_at,
            ),
        )
        cursor = await self.conn.execute(
            "SELECT source_chat_id, twitch_login, stream_id, recipient_chat_id, "
            "report_format, text_payload, html_payload, text_sent, html_sent, "
            "terminal_failed, terminal_reason, created_at, updated_at "
            "FROM report_deliveries WHERE source_chat_id = ? AND twitch_login = ? "
            "AND stream_id = ?",
            (source_chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Не удалось создать persistent delivery")
        return _report_delivery_from_row(tuple(row))

    @_serialized
    async def create_report_delivery(
        self,
        source_chat_id: int,
        twitch_login: str,
        stream_id: str,
        recipient_chat_id: int,
        report_format: str,
        text_payload: str,
        html_payload: str | None,
        created_at: float,
    ) -> ReportDelivery:
        """Создаёт logical automatic delivery один раз и фиксирует её recipient."""
        delivery = await self._insert_report_delivery(
            source_chat_id,
            twitch_login,
            stream_id,
            recipient_chat_id,
            report_format,
            text_payload,
            html_payload,
            created_at,
        )
        await self.conn.commit()
        return delivery

    async def get_report_delivery(
        self,
        source_chat_id: int,
        twitch_login: str,
        stream_id: str,
        recipient_chat_id: int,
    ) -> ReportDelivery | None:
        cursor = await self.conn.execute(
            "SELECT source_chat_id, twitch_login, stream_id, recipient_chat_id, "
            "report_format, text_payload, html_payload, text_sent, html_sent, "
            "terminal_failed, terminal_reason, created_at, updated_at "
            "FROM report_deliveries WHERE source_chat_id = ? AND twitch_login = ? "
            "AND stream_id = ? AND recipient_chat_id = ?",
            (source_chat_id, twitch_login, stream_id, recipient_chat_id),
        )
        row = await cursor.fetchone()
        return _report_delivery_from_row(tuple(row)) if row else None

    async def get_report_delivery_for_stream(
        self, source_chat_id: int, twitch_login: str, stream_id: str
    ) -> ReportDelivery | None:
        """Возвращает уже выбранный automatic destination после рестарта.

        Recipient намеренно берётся из outbox, а не вычисляется заново между text
        и HTML: обе части одного отчёта должны относиться к одной доставке. Перед
        каждым retry сохранённый адрес всё равно заново проходит safety guard.
        """
        cursor = await self.conn.execute(
            "SELECT source_chat_id, twitch_login, stream_id, recipient_chat_id, "
            "report_format, text_payload, html_payload, text_sent, html_sent, "
            "terminal_failed, terminal_reason, created_at, updated_at "
            "FROM report_deliveries WHERE source_chat_id = ? AND twitch_login = ? "
            "AND stream_id = ?",
            (source_chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        return _report_delivery_from_row(tuple(row)) if row else None

    async def pending_report_deliveries(
        self, limit: int | None = None
    ) -> list[ReportDelivery]:
        """Незавершённые automatic outbox rows, включая оставшиеся без tracking.

        Startup reconciliation может удалить stale Telegram-channel и его
        ``tracked_channels`` раньше первого poll. Delivery при этом обязана дойти
        до final guard и стать terminal, а не зависнуть сиротой навсегда.
        """
        sql = (
            "SELECT source_chat_id, twitch_login, stream_id, recipient_chat_id, "
            "report_format, text_payload, html_payload, text_sent, html_sent, "
            "terminal_failed, terminal_reason, created_at, updated_at "
            "FROM report_deliveries WHERE terminal_failed = 0 AND ("
            "text_sent = 0 OR (report_format = 'full' AND html_sent = 0)) "
            "ORDER BY created_at ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await self.conn.execute(sql, params)
        return [_report_delivery_from_row(tuple(row)) for row in await cursor.fetchall()]

    @_serialized
    async def mark_report_text_sent(
        self, delivery: ReportDelivery, updated_at: float
    ) -> None:
        await self.conn.execute(
            "UPDATE report_deliveries SET text_sent = 1, updated_at = ? "
            "WHERE source_chat_id = ? AND twitch_login = ? AND stream_id = ? "
            "AND recipient_chat_id = ? AND terminal_failed = 0",
            (
                updated_at,
                delivery.source_chat_id,
                delivery.twitch_login,
                delivery.stream_id,
                delivery.recipient_chat_id,
            ),
        )
        await self.conn.commit()

    @_serialized
    async def mark_report_html_sent(
        self, delivery: ReportDelivery, updated_at: float
    ) -> None:
        await self.conn.execute(
            "UPDATE report_deliveries SET html_sent = 1, updated_at = ? "
            "WHERE source_chat_id = ? AND twitch_login = ? AND stream_id = ? "
            "AND recipient_chat_id = ? AND terminal_failed = 0 AND text_sent = 1",
            (
                updated_at,
                delivery.source_chat_id,
                delivery.twitch_login,
                delivery.stream_id,
                delivery.recipient_chat_id,
            ),
        )
        await self.conn.commit()

    @_serialized
    async def mark_report_delivery_terminal(
        self, delivery: ReportDelivery, reason: str, updated_at: float
    ) -> None:
        await self.conn.execute(
            "UPDATE report_deliveries SET terminal_failed = 1, "
            "terminal_reason = ?, updated_at = ? "
            "WHERE source_chat_id = ? AND twitch_login = ? AND stream_id = ? "
            "AND recipient_chat_id = ?",
            (
                reason,
                updated_at,
                delivery.source_chat_id,
                delivery.twitch_login,
                delivery.stream_id,
                delivery.recipient_chat_id,
            ),
        )
        await self.conn.commit()

    async def _migrate_deferred_reports_key(self) -> None:
        """Разрешает хранить несколько завершённых стримов одного канала в очереди."""
        cursor = await self.conn.execute("PRAGMA table_info(deferred_reports)")
        columns = await cursor.fetchall()
        primary_key = [
            row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]
        ]
        if primary_key == ["chat_id", "source_chat_id", "twitch_login", "stream_id"]:
            return

        await self.conn.execute(
            "ALTER TABLE deferred_reports RENAME TO deferred_reports_legacy"
        )
        await self.conn.execute(
            "CREATE TABLE deferred_reports ("
            "chat_id INTEGER NOT NULL, source_chat_id INTEGER NOT NULL, "
            "twitch_login TEXT NOT NULL, stream_id TEXT NOT NULL DEFAULT '', "
            "ended_at REAL NOT NULL, "
            "PRIMARY KEY (chat_id, source_chat_id, twitch_login, stream_id))"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO deferred_reports "
            "(chat_id, source_chat_id, twitch_login, stream_id, ended_at) "
            "SELECT chat_id, source_chat_id, twitch_login, "
            "COALESCE(stream_id, ''), ended_at FROM deferred_reports_legacy"
        )
        await self.conn.execute("DROP TABLE deferred_reports_legacy")

    @_serialized
    async def clear_live_message(self, chat_id: int, twitch_login: str) -> None:
        """Забывает только Telegram live-пост, сохраняя логическую Twitch-сессию."""
        await self.conn.execute(
            "UPDATE tracked_channels SET last_message_id = NULL "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

    @_serialized
    async def clear_finished_session(self, chat_id: int, twitch_login: str) -> None:
        """Очищает финализированную сессию, если её live-пост уже удалён.

        Если Telegram временно не дал удалить сообщение, идентификатор и остальное
        состояние остаются для следующей попытки очистки.
        """
        await self.conn.execute(
            "UPDATE tracked_channels SET last_title = NULL, last_stream_id = NULL, "
            "offline_since = NULL, stream_started_at = NULL, last_seen_live_at = NULL, "
            "peak_viewers = NULL, "
            "stats_sent = 0, viewer_sum = 0, viewer_samples = 0, "
            "followers_at_start = NULL "
            "WHERE chat_id = ? AND twitch_login = ? "
            "AND is_live = 0 AND stats_sent = 1 AND last_message_id IS NULL",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

    @_serialized
    async def recover_finished_sessions(self) -> None:
        """Дочищает сессии после падения между mark_stats_sent и локальной очисткой.

        История и deferred reports находятся в отдельных таблицах и не затрагиваются.
        Сессии с неудалённым live-постом остаются до успешной повторной попытки.
        """
        await self.conn.execute(
            "UPDATE tracked_channels SET last_title = NULL, last_stream_id = NULL, "
            "offline_since = NULL, stream_started_at = NULL, last_seen_live_at = NULL, "
            "peak_viewers = NULL, "
            "stats_sent = 0, viewer_sum = 0, viewer_samples = 0, "
            "followers_at_start = NULL "
            "WHERE is_live = 0 AND stats_sent = 1 AND last_message_id IS NULL"
        )
        await self.conn.commit()

    @_serialized
    async def clear_message(self, chat_id: int, twitch_login: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET last_message_id = NULL, last_title = NULL, "
            "last_stream_id = NULL, offline_since = NULL, "
            "stream_started_at = NULL, last_seen_live_at = NULL, peak_viewers = NULL, "
            "stats_sent = 0, "
            "viewer_sum = 0, viewer_samples = 0, followers_at_start = NULL "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

    @_serialized
    async def add_stream_sample(
        self,
        chat_id: int,
        twitch_login: str,
        stream_id: str,
        sampled_at: float,
        viewer_count: int,
        title: str,
        game_name: str,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO stream_samples "
            "(chat_id, twitch_login, stream_id, sampled_at, viewer_count, title, game_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, twitch_login, stream_id, sampled_at, viewer_count, title, game_name),
        )
        await self.conn.commit()

    async def get_stream_samples(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> list[tuple[float, int, str, str]]:
        """(sampled_at, viewer_count, title, game_name) по возрастанию времени."""
        cursor = await self.conn.execute(
            "SELECT sampled_at, viewer_count, title, game_name FROM stream_samples "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? "
            "ORDER BY sampled_at ASC",
            (chat_id, twitch_login, stream_id),
        )
        return await cursor.fetchall()

    @_serialized
    async def add_chat_activity_samples(
        self, chat_id: int, twitch_login: str, stream_id: str, activity: list[tuple[float, int]]
    ) -> None:
        if not activity:
            return
        await self.conn.executemany(
            "INSERT INTO chat_activity_samples "
            "(chat_id, twitch_login, stream_id, minute_ts, message_count) VALUES (?, ?, ?, ?, ?)",
            [(chat_id, twitch_login, stream_id, minute_ts, count) for minute_ts, count in activity],
        )
        await self.conn.commit()

    async def get_chat_activity_samples(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> list[tuple[float, int]]:
        cursor = await self.conn.execute(
            "SELECT minute_ts, message_count FROM chat_activity_samples "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? ORDER BY minute_ts ASC",
            (chat_id, twitch_login, stream_id),
        )
        return await cursor.fetchall()

    @_serialized
    async def add_chat_unique_nicks(
        self, chat_id: int, twitch_login: str, stream_id: str, nicks: list[tuple[str, float]]
    ) -> None:
        if not nicks:
            return
        await self.conn.executemany(
            "INSERT INTO chat_unique_nicks "
            "(chat_id, twitch_login, stream_id, nick, joined_at) VALUES (?, ?, ?, ?, ?)",
            [(chat_id, twitch_login, stream_id, nick, joined_at) for nick, joined_at in nicks],
        )
        await self.conn.commit()

    @_serialized
    async def save_stream_chatters(
        self, twitch_login: str, stream_id: str, nicks: list[tuple[str, float]]
    ) -> None:
        """Ники чатеров сохраняются один раз на стрим, независимо от того, сколько
        чатов за ним следят. INSERT OR IGNORE делает повтор безопасным."""
        if not nicks:
            return
        await self.conn.executemany(
            "INSERT OR IGNORE INTO stream_chatters "
            "(twitch_login, stream_id, nick, first_seen_at) VALUES (?, ?, ?, ?)",
            [(twitch_login, stream_id, nick, ts) for nick, ts in nicks],
        )
        await self.conn.commit()

    async def get_chat_unique_nicks(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> list[tuple[str, float]]:
        """Ники чатеров за стрим. Сначала смотрим в общее хранилище, привязанное
        к стриму; старая таблица с копией на каждый чат остаётся запасным путём для
        эфиров, которые шли в момент обновления бота (данные живут сутки и истекут)."""
        cursor = await self.conn.execute(
            "SELECT nick, first_seen_at FROM stream_chatters "
            "WHERE twitch_login = ? AND stream_id = ? ORDER BY first_seen_at ASC",
            (twitch_login, stream_id),
        )
        rows = await cursor.fetchall()
        if rows:
            return rows
        cursor = await self.conn.execute(
            "SELECT nick, joined_at FROM chat_unique_nicks "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? ORDER BY joined_at ASC",
            (chat_id, twitch_login, stream_id),
        )
        return await cursor.fetchall()

    async def get_last_finished_stream(
        self, chat_id: int, twitch_login: str
    ) -> tuple[
        str, float, str | None, str | None, int, int, int, int | None,
        str | None, int | None, int | None, str | None, str | None, str | None,
    ] | None:
        """Последний завершённый стрим этого канала в этом чате.
        (stream_id, ended_at, started_at, title, duration_seconds, peak_viewers, avg_viewers,
        new_followers, new_followers_text, unique_chatters, join_reliable,
        top_chatters_json, raid_events_json, collab_json) или None."""
        cursor = await self.conn.execute(
            "SELECT stream_id, ended_at, started_at, title, duration_seconds, peak_viewers, "
            "avg_viewers, new_followers, new_followers_text, unique_chatters, join_reliable, "
            "top_chatters_json, raid_events_json, collab_json "
            "FROM stream_history WHERE chat_id = ? AND twitch_login = ? "
            "ORDER BY ended_at DESC LIMIT 1",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

    async def get_finished_stream(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> tuple[
        str, float, str | None, str | None, int, int, int, int | None,
        str | None, int | None, int | None, str | None, str | None, str | None,
    ] | None:
        """Конкретный завершённый стрим для устойчивой deferred-доставки."""
        cursor = await self.conn.execute(
            "SELECT stream_id, ended_at, started_at, title, duration_seconds, peak_viewers, "
            "avg_viewers, new_followers, new_followers_text, unique_chatters, join_reliable, "
            "top_chatters_json, raid_events_json, collab_json "
            "FROM stream_history WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

    @_serialized
    async def save_stream_chat_meta(
        self,
        chat_id: int,
        twitch_login: str,
        stream_id: str,
        join_reliable: bool,
        top_chatters_json: str | None,
        raid_events_json: str | None,
        created_at: float,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO stream_chat_meta "
            "(chat_id, twitch_login, stream_id, join_reliable, top_chatters_json, "
            "raid_events_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, twitch_login, stream_id) DO UPDATE SET "
            "join_reliable = MIN(stream_chat_meta.join_reliable, excluded.join_reliable), "
            "top_chatters_json = excluded.top_chatters_json, "
            "raid_events_json = excluded.raid_events_json, "
            "created_at = excluded.created_at",
            (
                chat_id, twitch_login, stream_id, int(join_reliable),
                top_chatters_json, raid_events_json, created_at,
            ),
        )
        await self.conn.commit()

    @_serialized
    async def take_stream_chat_meta(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> tuple[bool, str | None, str | None] | None:
        """(join_reliable, top_chatters_json, raid_events_json) с удалением записи —
        данные нужны ровно один раз, при формировании итогового отчёта.

        Чтение и удаление идут двумя запросами, а не через DELETE ... RETURNING:
        RETURNING появился только в SQLite 3.35, и на образе с более старой версией
        бот падал бы на первом же отчёте. Атомарность даёт блокировка записи."""
        cursor = await self.conn.execute(
            "SELECT join_reliable, top_chatters_json, raid_events_json "
            "FROM stream_chat_meta WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        if row is None:
            await self.conn.commit()
            return None
        await self.conn.execute(
            "DELETE FROM stream_chat_meta "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        await self.conn.commit()
        return bool(row[0]) if row[0] is not None else True, row[1], row[2]

    @_serialized
    async def invalidate_live_chat_stats_after_restart(self) -> None:
        """Помечает chat stats текущих logical sessions как неполные после потери RAM."""
        now = time.time()
        await self.conn.execute(
            "INSERT INTO stream_chat_meta "
            "(chat_id, twitch_login, stream_id, join_reliable, top_chatters_json, "
            "raid_events_json, created_at) "
            "SELECT chat_id, twitch_login, last_stream_id, 0, NULL, NULL, ? "
            "FROM tracked_channels WHERE last_stream_id IS NOT NULL "
            "AND (is_live = 1 OR stats_sent = 0) "
            "ON CONFLICT(chat_id, twitch_login, stream_id) DO UPDATE SET "
            "join_reliable = 0, created_at = excluded.created_at",
            (now,),
        )
        await self.conn.commit()

    async def get_stream_chat_meta(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> tuple[bool, str | None, str | None] | None:
        """Читает meta без удаления: automatic flow удалит её после durable save."""
        cursor = await self.conn.execute(
            "SELECT join_reliable, top_chatters_json, raid_events_json "
            "FROM stream_chat_meta WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return bool(row[0]) if row[0] is not None else True, row[1], row[2]

    @_serialized
    async def delete_stream_chat_meta(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> None:
        await self.conn.execute(
            "DELETE FROM stream_chat_meta "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        await self.conn.commit()

    @_serialized
    async def purge_old_report_data(self, older_than_ts: float) -> None:
        """Удаляет сырые поминутные данные (график, ники чатеров) старше указанного времени.
        Свёрнутая сводка в stream_history не трогается — она хранится всегда."""
        await self.conn.execute(
            "DELETE FROM stream_samples WHERE sampled_at < ?", (older_than_ts,)
        )
        await self.conn.execute(
            "DELETE FROM chat_activity_samples WHERE minute_ts < ?", (older_than_ts,)
        )
        await self.conn.execute(
            "DELETE FROM chat_unique_nicks WHERE joined_at < ?", (older_than_ts,)
        )
        await self.conn.execute(
            "DELETE FROM stream_chatters WHERE first_seen_at < ?", (older_than_ts,)
        )
        # подстраховка: записи, чей отчёт так и не был отправлен (например, чат
        # удалил бота сразу после стрима), иначе копились бы вечно
        await self.conn.execute(
            "DELETE FROM stream_chat_meta WHERE created_at < ? AND NOT EXISTS ("
            "SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = stream_chat_meta.chat_id "
            "AND tc.twitch_login = stream_chat_meta.twitch_login "
            "AND tc.last_stream_id = stream_chat_meta.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0))",
            (older_than_ts,),
        )
        # Завершённые/terminal outbox-записи нужны только для crash recovery.
        # Pending не удаляем по возрасту: payload хранится прямо в строке и может
        # быть безопасно повторён на следующем обычном poll.
        await self.conn.execute(
            "DELETE FROM report_deliveries WHERE updated_at < ? AND ("
            "terminal_failed = 1 OR (text_sent = 1 AND ("
            "report_format = 'brief' OR html_sent = 1)))",
            (older_than_ts,),
        )
        await self.conn.execute(
            "DELETE FROM follow_event_counts WHERE created_at < ? AND NOT EXISTS ("
            "SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = follow_event_counts.chat_id "
            "AND tc.twitch_login = follow_event_counts.twitch_login "
            "AND tc.last_stream_id = follow_event_counts.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0))",
            (older_than_ts,),
        )
        await self.conn.execute(
            "DELETE FROM follow_event_ids WHERE received_at < ?", (older_than_ts,)
        )
        await self.conn.commit()

    @_serialized
    async def set_followers_at_start(
        self, chat_id: int, twitch_login: str, followers_count: int
    ) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET followers_at_start = ? "
            "WHERE chat_id = ? AND twitch_login = ? AND followers_at_start IS NULL",
            (followers_count, chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_followers_at_start(self, chat_id: int, twitch_login: str) -> int | None:
        cursor = await self.conn.execute(
            "SELECT followers_at_start FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    @_serialized
    async def start_follow_event_count(
        self, chat_id: int, twitch_login: str, stream_id: str, reliable: bool
    ) -> None:
        """Заводит точный EventSub-счётчик для нового стрима.

        INSERT OR IGNORE важен при рестарте процесса: уже накопленный счётчик не
        обнуляется, а недостоверный интервал не становится снова достоверным.
        """
        await self.conn.execute(
            "INSERT OR IGNORE INTO follow_event_counts "
            "(chat_id, twitch_login, stream_id, event_count, reliable, created_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (chat_id, twitch_login, stream_id, int(reliable), time.time()),
        )
        await self.conn.commit()

    @_serialized
    async def record_follow_event(self, twitch_login: str, message_id: str) -> bool:
        """Учитывает EventSub follow во всех активных карточках канала ровно один раз."""
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO follow_event_ids (message_id, received_at) VALUES (?, ?)",
            (message_id, time.time()),
        )
        if cursor.rowcount == 0:
            await self.conn.rollback()
            return False
        await self.conn.execute(
            "UPDATE follow_event_counts SET event_count = event_count + 1 "
            "WHERE twitch_login = ? AND EXISTS ("
            "SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = follow_event_counts.chat_id "
            "AND tc.twitch_login = follow_event_counts.twitch_login "
            "AND tc.last_stream_id = follow_event_counts.stream_id "
            "AND tc.is_live = 1)",
            (twitch_login,),
        )
        await self.conn.commit()
        return True

    @_serialized
    async def mark_live_follow_counts_unreliable(self, twitch_login: str) -> None:
        """Помечает текущие эфиры неточными, если EventSub-соединение прервалось."""
        await self.conn.execute(
            "UPDATE follow_event_counts SET reliable = 0 "
            "WHERE twitch_login = ? AND EXISTS ("
            "SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = follow_event_counts.chat_id "
            "AND tc.twitch_login = follow_event_counts.twitch_login "
            "AND tc.last_stream_id = follow_event_counts.stream_id "
            "AND tc.is_live = 1)",
            (twitch_login,),
        )
        await self.conn.commit()

    @_serialized
    async def invalidate_live_follow_counts_after_restart(self) -> None:
        """После рестарта неизвестно, сколько EventSub-событий пришло во время простоя."""
        await self.conn.execute(
            "UPDATE follow_event_counts SET reliable = 0 WHERE EXISTS ("
            "SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = follow_event_counts.chat_id "
            "AND tc.twitch_login = follow_event_counts.twitch_login "
            "AND tc.last_stream_id = follow_event_counts.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0))"
        )
        await self.conn.commit()

    async def get_follow_event_count(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> tuple[int, bool] | None:
        cursor = await self.conn.execute(
            "SELECT event_count, reliable FROM follow_event_counts "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        return (row[0], bool(row[1])) if row else None

    async def all_token_logins(self) -> list[str]:
        cursor = await self.conn.execute("SELECT twitch_login FROM twitch_user_tokens")
        return [row[0] for row in await cursor.fetchall()]

    @_serialized
    async def save_user_token(
        self,
        twitch_login: str,
        broadcaster_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: float,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO twitch_user_tokens (twitch_login, broadcaster_id, access_token, refresh_token, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(twitch_login) DO UPDATE SET "
            "broadcaster_id = excluded.broadcaster_id, "
            "access_token = excluded.access_token, "
            "refresh_token = excluded.refresh_token, "
            "expires_at = excluded.expires_at",
            (
                twitch_login,
                broadcaster_id,
                self._encrypt_token(access_token),
                self._encrypt_token(refresh_token),
                expires_at,
            ),
        )
        await self.conn.commit()

    async def get_user_token(
        self, twitch_login: str
    ) -> tuple[str, str, str, float] | None:
        """(broadcaster_id, access_token, refresh_token, expires_at) или None."""
        cursor = await self.conn.execute(
            "SELECT broadcaster_id, access_token, refresh_token, expires_at "
            "FROM twitch_user_tokens WHERE twitch_login = ?",
            (twitch_login,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        broadcaster_id, access_token, refresh_token, expires_at = row
        return (
            broadcaster_id,
            self._decrypt_token(access_token),
            self._decrypt_token(refresh_token),
            expires_at,
        )

    async def _insert_stream_history(self, record: StreamHistoryRecord) -> None:
        await self.conn.execute(
            "INSERT INTO stream_history "
            "(chat_id, twitch_login, stream_id, ended_at, duration_seconds, "
            "peak_viewers, avg_viewers, new_followers, started_at, title, "
            "new_followers_text, unique_chatters, join_reliable, "
            "top_chatters_json, raid_events_json, collab_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            # повторная отправка того же отчёта (например, после перезапуска бота
            # между отправкой и отметкой «отправлено») не должна задваивать историю
            "ON CONFLICT(chat_id, twitch_login, stream_id) DO NOTHING",
            (
                record.chat_id,
                record.twitch_login,
                record.stream_id,
                record.ended_at,
                record.duration_seconds,
                record.peak_viewers,
                record.avg_viewers,
                record.new_followers,
                record.started_at,
                record.title,
                record.new_followers_text,
                record.unique_chatters,
                None if record.join_reliable is None else int(record.join_reliable),
                record.top_chatters_json,
                record.raid_events_json,
                record.collab_json,
            ),
        )

    @_serialized
    async def add_stream_history(
        self,
        chat_id: int,
        twitch_login: str,
        stream_id: str,
        ended_at: float,
        duration_seconds: int,
        peak_viewers: int,
        avg_viewers: int,
        new_followers: int | None,
        started_at: str | None = None,
        title: str | None = None,
        new_followers_text: str | None = None,
        unique_chatters: int | None = None,
        join_reliable: bool | None = None,
        top_chatters_json: str | None = None,
        raid_events_json: str | None = None,
        collab_json: str | None = None,
    ) -> None:
        await self._insert_stream_history(
            StreamHistoryRecord(
                chat_id=chat_id,
                twitch_login=twitch_login,
                stream_id=stream_id,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
                peak_viewers=peak_viewers,
                avg_viewers=avg_viewers,
                new_followers=new_followers,
                started_at=started_at,
                title=title,
                new_followers_text=new_followers_text,
                unique_chatters=unique_chatters,
                join_reliable=join_reliable,
                top_chatters_json=top_chatters_json,
                raid_events_json=raid_events_json,
                collab_json=collab_json,
            )
        )
        await self.conn.commit()

    @_serialized
    async def add_stream_history_record(self, history: StreamHistoryRecord) -> None:
        await self._insert_stream_history(history)
        await self.conn.commit()

    @_serialized
    async def save_history_and_report_delivery(
        self,
        history: StreamHistoryRecord,
        recipient_chat_id: int,
        report_format: str,
        text_payload: str,
        html_payload: str | None,
        created_at: float,
    ) -> ReportDelivery:
        """Атомарно фиксирует history и outbox до первой automatic отправки."""
        await self._insert_stream_history(history)
        delivery = await self._insert_report_delivery(
            history.chat_id,
            history.twitch_login,
            history.stream_id,
            recipient_chat_id,
            report_format,
            text_payload,
            html_payload,
            created_at,
        )
        await self.conn.commit()
        return delivery

    @_serialized
    async def set_stats_recipient(self, chat_id: int, stats_chat_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO stats_recipients (chat_id, stats_chat_id) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET stats_chat_id = excluded.stats_chat_id",
            (chat_id, stats_chat_id),
        )
        await self.conn.commit()

    @_serialized
    async def set_default_stats_recipient(self, chat_id: int, stats_chat_id: int) -> None:
        """Как set_stats_recipient, но не перезаписывает уже существующую привязку."""
        await self.conn.execute(
            "INSERT INTO stats_recipients (chat_id, stats_chat_id) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO NOTHING",
            (chat_id, stats_chat_id),
        )
        await self.conn.commit()

    async def get_stats_recipient(self, chat_id: int) -> int | None:
        cursor = await self.conn.execute(
            "SELECT stats_chat_id FROM stats_recipients WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    @_serialized
    async def mark_known_private_user(self, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO known_private_users (user_id) VALUES (?)",
            (user_id,),
        )
        await self.conn.commit()

    async def is_known_private_user(self, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM known_private_users WHERE user_id = ?",
            (user_id,),
        )
        return await cursor.fetchone() is not None

    async def get_display_names_map(self) -> dict[str, str]:
        """Все известные отображаемые имена разом — чтобы не дёргать БД по одному
        логину в цикле при поиске упоминаний коллабов."""
        cursor = await self.conn.execute("SELECT twitch_login, display_name FROM channel_display_names")
        return {row[0]: row[1] for row in await cursor.fetchall()}

    async def get_display_name(self, twitch_login: str) -> str | None:
        cursor = await self.conn.execute(
            "SELECT display_name FROM channel_display_names WHERE twitch_login = ?",
            (twitch_login,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    @_serialized
    async def set_display_name(self, twitch_login: str, display_name: str) -> None:
        await self.conn.execute(
            "INSERT INTO channel_display_names (twitch_login, display_name) VALUES (?, ?) "
            "ON CONFLICT(twitch_login) DO UPDATE SET display_name = excluded.display_name",
            (twitch_login, display_name),
        )
        await self.conn.commit()

    @_serialized
    async def save_vod(
        self,
        chat_id: int,
        twitch_login: str,
        stream_id: str,
        vod_url: str,
        vod_title: str | None,
        chapters_json: str | None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO vod_archive "
            "(chat_id, twitch_login, stream_id, vod_url, vod_title, chapters_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, twitch_login, stream_id) DO UPDATE SET "
            "vod_url = excluded.vod_url, vod_title = excluded.vod_title, "
            "chapters_json = excluded.chapters_json",
            (chat_id, twitch_login, stream_id, vod_url, vod_title, chapters_json, time.time()),
        )
        await self.conn.commit()

    async def get_vod(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> tuple[str, str | None, str | None] | None:
        """(vod_url, vod_title, chapters_json) или None, если VOD не сохранён."""
        cursor = await self.conn.execute(
            "SELECT vod_url, vod_title, chapters_json FROM vod_archive "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ?",
            (chat_id, twitch_login, stream_id),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

    async def get_history_stats(
        self,
        chat_id: int,
        twitch_login: str,
        exclude_stream_id: str | None = None,
    ) -> tuple[int, float, float, int, int] | None:
        """(count, avg_peak, avg_avg, best_peak, best_avg) по прошлым стримам
        (не включая текущий). None, если истории ещё нет."""
        sql = (
            "SELECT COUNT(*), AVG(peak_viewers), AVG(avg_viewers), "
            "MAX(peak_viewers), MAX(avg_viewers) "
            "FROM stream_history WHERE chat_id = ? AND twitch_login = ?"
        )
        params: tuple[object, ...] = (chat_id, twitch_login)
        if exclude_stream_id is not None:
            sql += " AND stream_id != ?"
            params += (exclude_stream_id,)
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None or row[0] == 0:
            return None
        return row[0], row[1], row[2], row[3], row[4]

    async def get_bot_stats(self) -> dict[str, int]:
        """Сводка по использованию бота: сколько людей и групп его подключили."""
        private_users = await self.conn.execute("SELECT COUNT(*) FROM known_private_users")
        groups = await self.conn.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM tracked_channels WHERE chat_id < 0"
        )
        tracked_channels = await self.conn.execute(
            "SELECT COUNT(*) FROM tracked_channels"
        )
        unique_twitch_channels = await self.conn.execute(
            "SELECT COUNT(DISTINCT twitch_login) FROM tracked_channels"
        )
        live_now = await self.conn.execute(
            "SELECT COUNT(*) FROM tracked_channels WHERE is_live = 1"
        )
        # диагностика тихих часов: сколько отчётов ждёт отправки и сколько чатов
        # вообще их настроили — по этим числам видно, копится очередь или нет
        deferred = await self.conn.execute("SELECT COUNT(*) FROM deferred_reports")
        quiet_chats = await self.conn.execute("SELECT COUNT(*) FROM quiet_hours")
        history_rows = await self.conn.execute("SELECT COUNT(*) FROM stream_history")
        return {
            "private_users": (await private_users.fetchone())[0],
            "groups": (await groups.fetchone())[0],
            "tracked_channels": (await tracked_channels.fetchone())[0],
            "unique_twitch_channels": (await unique_twitch_channels.fetchone())[0],
            "live_now": (await live_now.fetchone())[0],
            "deferred_reports": (await deferred.fetchone())[0],
            "quiet_hours_chats": (await quiet_chats.fetchone())[0],
            "history_rows": (await history_rows.fetchone())[0],
        }
