from __future__ import annotations

import time

import aiosqlite

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

CREATE TABLE IF NOT EXISTS twitch_user_tokens (
    twitch_login TEXT PRIMARY KEY,
    broadcaster_id TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at REAL NOT NULL
);

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

CREATE TABLE IF NOT EXISTS chat_activity_samples (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    minute_ts REAL NOT NULL,
    message_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_activity_lookup
    ON chat_activity_samples (chat_id, twitch_login, stream_id);

CREATE TABLE IF NOT EXISTS chat_unique_nicks (
    chat_id INTEGER NOT NULL,
    twitch_login TEXT NOT NULL,
    stream_id TEXT NOT NULL,
    nick TEXT NOT NULL,
    joined_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_unique_nicks_lookup
    ON chat_unique_nicks (chat_id, twitch_login, stream_id);

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
    stream_id TEXT,
    ended_at REAL NOT NULL,
    PRIMARY KEY (chat_id, source_chat_id, twitch_login)
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
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

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
        await self._add_missing_columns(
            "tracked_channels",
            {
                "notify_enabled": "INTEGER NOT NULL DEFAULT 1",
                "post_recipient_chat_id": "INTEGER",
                "report_format": "TEXT NOT NULL DEFAULT 'full'",
                "raid_detection_enabled": "INTEGER NOT NULL DEFAULT 1",
                "quiet_hours_exempt": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        await self.conn.commit()

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

    async def add_channel(self, chat_id: int, twitch_login: str) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO tracked_channels (chat_id, twitch_login) VALUES (?, ?)",
                (chat_id, twitch_login),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_channel(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_channels(self, chat_id: int) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT twitch_login FROM tracked_channels WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def list_channels_with_notify(self, chat_id: int) -> list[tuple[str, bool]]:
        """(twitch_login, notify_enabled) для всех каналов чата."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, notify_enabled FROM tracked_channels "
            "WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [(row[0], bool(row[1])) for row in rows]

    async def list_channels_with_routing(
        self, chat_id: int
    ) -> list[tuple[str, bool, int | None, str, bool, bool]]:
        """(twitch_login, notify_enabled, post_recipient_chat_id, report_format,
        raid_detection_enabled, quiet_hours_exempt) для всех каналов чата."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, notify_enabled, post_recipient_chat_id, report_format, "
            "raid_detection_enabled, quiet_hours_exempt FROM tracked_channels "
            "WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [
            (row[0], bool(row[1]), row[2], row[3] or "full", bool(row[4]), bool(row[5]))
            for row in rows
        ]

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

    async def set_post_recipient(
        self, chat_id: int, twitch_login: str, recipient_chat_id: int | None
    ) -> None:
        """recipient_chat_id=None сбрасывает явную привязку — канал возвращается
        к общей привязке чата (stats_recipients) или к самому чату."""
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

    async def resolve_post_recipient(self, chat_id: int, twitch_login: str) -> int:
        """Итоговый получатель постов для канала: явная привязка канала →
        привязка всего чата → сам чат."""
        per_channel = await self.get_post_recipient(chat_id, twitch_login)
        if per_channel is not None:
            return per_channel
        return await self.get_stats_recipient(chat_id) or chat_id

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

    async def set_quiet_hours_notify_after(self, chat_id: int, enabled: bool) -> None:
        await self.conn.execute(
            "UPDATE quiet_hours SET notify_after_enabled = ? WHERE chat_id = ?",
            (int(enabled), chat_id),
        )
        await self.conn.commit()

    async def all_quiet_hours_chat_ids(self) -> list[int]:
        cursor = await self.conn.execute("SELECT chat_id FROM quiet_hours")
        return [row[0] for row in await cursor.fetchall()]

    async def add_deferred_report(
        self, chat_id: int, source_chat_id: int, twitch_login: str, stream_id: str | None, ended_at: float
    ) -> None:
        await self.conn.execute(
            "INSERT INTO deferred_reports (chat_id, source_chat_id, twitch_login, stream_id, ended_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, source_chat_id, twitch_login) DO UPDATE SET "
            "stream_id = excluded.stream_id, ended_at = excluded.ended_at",
            (chat_id, source_chat_id, twitch_login, stream_id, ended_at),
        )
        await self.conn.commit()

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
        return await cursor.fetchall()

    async def is_quiet_hours_digest_sent(self, chat_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM quiet_hours_digest_sent WHERE chat_id = ?", (chat_id,)
        )
        return await cursor.fetchone() is not None

    async def mark_quiet_hours_digest_sent(self, chat_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO quiet_hours_digest_sent (chat_id) VALUES (?)", (chat_id,)
        )
        await self.conn.commit()

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

    async def set_utc_offset(self, chat_id: int, utc_offset_minutes: int) -> None:
        await self.conn.execute(
            "INSERT INTO user_timezones (chat_id, utc_offset_minutes) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET utc_offset_minutes = excluded.utc_offset_minutes",
            (chat_id, utc_offset_minutes),
        )
        await self.conn.commit()

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
                peak_viewers, peak_viewers, int(is_live), stream_id,
                int(is_live),
                int(is_live), stream_id, int(is_live), stream_id,
                int(is_live), stream_id,
                chat_id, twitch_login,
            ),
        )
        await self.conn.commit()

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

    async def pending_offline_posts(self) -> list[tuple[int, str, int, float]]:
        """(chat_id, twitch_login, message_id, offline_since) для постов, ждущих возможного удаления."""
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, last_message_id, offline_since "
            "FROM tracked_channels "
            "WHERE is_live = 0 AND offline_since IS NOT NULL AND last_message_id IS NOT NULL"
        )
        return await cursor.fetchall()

    async def pending_stats(self) -> list[tuple[int, str, float, str, str, int, int, int, str, int | None]]:
        """(chat_id, twitch_login, offline_since, title, started_at, peak_viewers,
        viewer_sum, viewer_samples, last_stream_id, followers_at_start) для неотправленной статистики."""
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, offline_since, last_title, stream_started_at, "
            "peak_viewers, viewer_sum, viewer_samples, last_stream_id, followers_at_start "
            "FROM tracked_channels "
            "WHERE is_live = 0 AND offline_since IS NOT NULL AND stats_sent = 0 "
            "AND stream_started_at IS NOT NULL"
        )
        return await cursor.fetchall()

    async def mark_stats_sent(self, chat_id: int, twitch_login: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET stats_sent = 1 WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

    async def clear_message(self, chat_id: int, twitch_login: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET last_message_id = NULL, last_title = NULL, "
            "last_stream_id = NULL, offline_since = NULL, "
            "stream_started_at = NULL, peak_viewers = NULL, stats_sent = 0, "
            "viewer_sum = 0, viewer_samples = 0, followers_at_start = NULL "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        await self.conn.commit()

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

    async def get_chat_unique_nicks(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> list[tuple[str, float]]:
        cursor = await self.conn.execute(
            "SELECT nick, joined_at FROM chat_unique_nicks "
            "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? ORDER BY joined_at ASC",
            (chat_id, twitch_login, stream_id),
        )
        return await cursor.fetchall()

    async def get_last_finished_stream(
        self, chat_id: int, twitch_login: str
    ) -> tuple[str, float, str | None, str | None, int, int, int, int | None, str | None, int | None, int | None, str | None, str | None] | None:
        """Последний завершённый стрим этого канала в этом чате.
        (stream_id, ended_at, started_at, title, duration_seconds, peak_viewers, avg_viewers,
        new_followers, new_followers_text, unique_chatters, join_reliable,
        top_chatters_json, raid_events_json) или None."""
        cursor = await self.conn.execute(
            "SELECT stream_id, ended_at, started_at, title, duration_seconds, peak_viewers, "
            "avg_viewers, new_followers, new_followers_text, unique_chatters, join_reliable, "
            "top_chatters_json, raid_events_json "
            "FROM stream_history WHERE chat_id = ? AND twitch_login = ? "
            "ORDER BY ended_at DESC LIMIT 1",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

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
        await self.conn.commit()

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
            (twitch_login, broadcaster_id, access_token, refresh_token, expires_at),
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
        return tuple(row) if row else None

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
        await self.conn.execute(
            "INSERT INTO stream_history "
            "(chat_id, twitch_login, stream_id, ended_at, duration_seconds, "
            "peak_viewers, avg_viewers, new_followers, started_at, title, "
            "new_followers_text, unique_chatters, join_reliable, "
            "top_chatters_json, raid_events_json, collab_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, twitch_login, stream_id, ended_at, duration_seconds,
                peak_viewers, avg_viewers, new_followers, started_at, title,
                new_followers_text, unique_chatters,
                None if join_reliable is None else int(join_reliable),
                top_chatters_json, raid_events_json, collab_json,
            ),
        )
        await self.conn.commit()

    async def set_stats_recipient(self, chat_id: int, stats_chat_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO stats_recipients (chat_id, stats_chat_id) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET stats_chat_id = excluded.stats_chat_id",
            (chat_id, stats_chat_id),
        )
        await self.conn.commit()

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

    async def get_display_name(self, twitch_login: str) -> str | None:
        cursor = await self.conn.execute(
            "SELECT display_name FROM channel_display_names WHERE twitch_login = ?",
            (twitch_login,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_display_name(self, twitch_login: str, display_name: str) -> None:
        await self.conn.execute(
            "INSERT INTO channel_display_names (twitch_login, display_name) VALUES (?, ?) "
            "ON CONFLICT(twitch_login) DO UPDATE SET display_name = excluded.display_name",
            (twitch_login, display_name),
        )
        await self.conn.commit()

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
        self, chat_id: int, twitch_login: str
    ) -> tuple[int, float, float, int, int] | None:
        """(count, avg_peak, avg_avg, best_peak, best_avg) по прошлым стримам
        (не включая текущий). None, если истории ещё нет."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*), AVG(peak_viewers), AVG(avg_viewers), "
            "MAX(peak_viewers), MAX(avg_viewers) "
            "FROM stream_history WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        if row is None or row[0] == 0:
            return None
        return row[0], row[1], row[2], row[3], row[4]
