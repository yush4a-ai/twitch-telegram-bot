from __future__ import annotations

import asyncio
import errno
import functools
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from .billing_models import BillingOrder, BillingSubject, PaymentRecord
from .billing_migrations import migrate_plus_payments, ORDER_COLUMNS
from .billing_provider import VerifiedPaymentEvent
from .deep_links import REFERRAL_CODE_RE, parse_growth_start_payload
from .streamer_template import StreamerTemplate, validate_streamer_template
from .viewer_filter import ViewerFilter, validate_viewer_filter
from .viewer_preferences import VideoSelection, validate_video_choices
from .plan_catalog import FREE_VIEWER_CHANNEL_LIMIT, VIEWER_PLUS_CHANNEL_LIMIT
from .entitlements import effective_viewer_predicate, resolve_effective_viewer

if TYPE_CHECKING:
    from .oauth import UserTokenResult


_ENCRYPTED_TOKEN_PREFIX = "fernet:v1:"
logger = logging.getLogger(__name__)

_BILLING_ORDER_FIELDS = (
    "order_id,request_key,telegram_user_id,subject_kind,subject_id,"
    "broadcaster_id,plan,provider,status,units,currency,duration_seconds,"
    "created_at,checkout_expires_at,checkout_reference,paid_at,closed_at,grant_id"
)
_BILLING_ORDER_READ_FIELDS = _BILLING_ORDER_FIELDS + "," + ",".join(ORDER_COLUMNS)
_BILLING_ORDER_DEFINITION = (
    "(order_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, "
    "telegram_user_id INTEGER NOT NULL, subject_kind TEXT NOT NULL "
    "CHECK(subject_kind IN ('viewer','streamer')), subject_id TEXT NOT NULL, "
    "broadcaster_id TEXT, plan TEXT NOT NULL, provider TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status IN ('pending','paid','cancelled','refunded','expired')), "
    "units INTEGER NOT NULL CHECK(units > 0), currency TEXT NOT NULL, "
    "duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 60 AND 2678400), "
    "created_at REAL NOT NULL, checkout_expires_at REAL NOT NULL, "
    "checkout_reference TEXT, paid_at REAL, closed_at REAL, grant_id TEXT UNIQUE, "
    "CHECK(checkout_expires_at > created_at), "
    "CHECK((subject_kind='viewer' AND broadcaster_id IS NULL "
    "AND subject_id=CAST(telegram_user_id AS TEXT) AND plan='viewer_plus') OR "
    "(subject_kind='streamer' AND broadcaster_id=subject_id "
    "AND plan='streamer_plus')))"
)


class DatabaseConfigurationError(RuntimeError):
    """Постоянная ошибка ключа/формата БД, которую restart сам не исправит."""


_PERMANENT_SQLITE_MESSAGES = (
    "unable to open database file",
    "attempt to write a readonly database",
    "read-only",
    "readonly",
    "permission denied",
    "database or disk is full",
    "database is full",
    "file is not a database",
    "database disk image is malformed",
)


def _is_permanent_storage_error(error: BaseException) -> bool:
    if isinstance(error, OSError) and error.errno in {
        errno.EACCES,
        errno.ENOENT,
        errno.ENOSPC,
        errno.ENOTDIR,
        errno.EROFS,
    }:
        return True
    if isinstance(error, (OSError, sqlite3.DatabaseError)):
        message = str(error).lower()
        return any(marker in message for marker in _PERMANENT_SQLITE_MESSAGES)
    return False


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


@dataclass(frozen=True)
class LivePostState:
    chat_id: int
    twitch_login: str
    logical_stream_id: str | None
    message_id: int | None
    message_kind: str
    media_transition_pending: bool
    preview_enabled: bool
    notify_enabled: bool
    media_transition_target_kind: str
    is_live: bool


@dataclass(frozen=True)
class OfflineCleanupState:
    logical_stream_id: str
    message_id: int
    offline_since: float
    stats_sent: bool
    title: str | None
    ended: bool


@dataclass(frozen=True)
class PreviewDestinationState:
    chat_id: int
    twitch_login: str
    notify_enabled: bool
    preview_enabled: bool
    is_live: bool
    logical_stream_id: str | None
    message_id: int | None
    message_kind: str
    include_track_link: bool
    last_stream_ended_at: float | None


# Personal video is selected by the viewer, not by the old preview_enabled flag.
# Both products require a current grant and the broadcaster observed for this post.
_EFFECTIVE_PREVIEW_SQL = (
    "CASE WHEN tc.chat_id>0 THEN EXISTS("
    "SELECT 1 FROM viewer_video_selections v "
    "WHERE v.telegram_user_id=tc.chat_id AND v.twitch_login=tc.twitch_login "
    "AND " + effective_viewer_predicate("v.telegram_user_id", "?1") + " "
    "AND v.broadcaster_id=tc.last_broadcaster_id "
    "AND tc.twitch_login IN (SELECT x.twitch_login FROM tracked_channels x "
    "LEFT JOIN viewer_plan_priority p ON p.telegram_user_id=x.chat_id "
    "AND p.twitch_login=x.twitch_login WHERE x.chat_id=tc.chat_id "
    "ORDER BY COALESCE(p.priority,0),x.added_at,x.twitch_login LIMIT 200)) "
    "ELSE tc.preview_enabled AND EXISTS("
    "SELECT 1 FROM streamer_communities sc JOIN streamer_identities si "
    "ON si.broadcaster_id=sc.broadcaster_id JOIN entitlement_grants g "
    "ON g.subject_kind='streamer' AND g.subject_id=si.broadcaster_id "
    "AND g.plan='streamer_plus' AND g.revoked_at IS NULL "
    "AND g.starts_at<=?1 AND g.expires_at>?2 WHERE sc.chat_id=tc.chat_id "
    "AND si.twitch_login=tc.twitch_login "
    "AND si.broadcaster_id=tc.last_broadcaster_id) END"
)


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
            except BaseException:
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
    last_message_kind TEXT NOT NULL DEFAULT 'text',
    media_transition_pending INTEGER NOT NULL DEFAULT 0,
    media_transition_target_kind TEXT NOT NULL DEFAULT 'video',
    live_post_ended INTEGER NOT NULL DEFAULT 0,
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
    preview_enabled INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0,
    auto_report_enabled INTEGER NOT NULL DEFAULT 0,
    channel_report_enabled INTEGER NOT NULL DEFAULT 0,
    post_recipient_chat_id INTEGER,
    report_format TEXT NOT NULL DEFAULT 'brief',
    raid_detection_enabled INTEGER NOT NULL DEFAULT 1,
    quiet_hours_exempt INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, twitch_login)
);

-- поллер каждую минуту спрашивает «в каких чатах следят за этим логином»;
-- без индекса это полное сканирование таблицы на каждый канал
CREATE INDEX IF NOT EXISTS idx_tracked_channels_login
    ON tracked_channels (twitch_login);
CREATE INDEX IF NOT EXISTS idx_tracked_channels_pending_posts
    ON tracked_channels (offline_since)
    WHERE is_live = 0 AND offline_since IS NOT NULL AND last_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tracked_channels_pending_stats
    ON tracked_channels (offline_since)
    WHERE is_live = 0 AND offline_since IS NOT NULL AND stats_sent = 0
      AND stream_started_at IS NOT NULL;

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
CREATE INDEX IF NOT EXISTS idx_stream_samples_stream_mode
    ON stream_samples (twitch_login, stream_id);

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
-- retention общих чатеров проверяет свежесть logical stream сразу по всем
-- Telegram-источникам; без этого индекса коррелированный lookup сканировал бы
-- бессрочную history для каждой удаляемой строки большого чата
CREATE INDEX IF NOT EXISTS idx_stream_history_stream_retention
    ON stream_history (twitch_login, stream_id, ended_at);

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
CREATE INDEX IF NOT EXISTS idx_report_deliveries_pending
    ON report_deliveries (updated_at)
    WHERE terminal_failed = 0
      AND (text_sent = 0 OR (report_format = 'full' AND html_sent = 0));
CREATE INDEX IF NOT EXISTS idx_report_deliveries_pending_created
    ON report_deliveries (created_at)
    WHERE terminal_failed = 0
      AND (text_sent = 0 OR (report_format = 'full' AND html_sent = 0));

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
CREATE INDEX IF NOT EXISTS idx_deferred_reports_health
    ON deferred_reports (ended_at);

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
            raise DatabaseConfigurationError(
                "TOKEN_ENCRYPTION_KEY не является корректным Fernet-ключом"
            ) from e

    async def connect(self) -> None:
        if self._path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self._path))
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                raise DatabaseConfigurationError(
                    "Не удалось создать каталог DB_PATH; проверь Volume и permissions"
                ) from e
        try:
            self._conn = await aiosqlite.connect(self._path)
        except BaseException as e:
            if _is_permanent_storage_error(e):
                raise DatabaseConfigurationError(
                    "SQLite не может открыть DB_PATH; проверь Volume, путь и permissions"
                ) from e
            raise
        try:
            # Явный busy_timeout делает поведение одинаковым при кратком overlap двух
            # Railway-процессов во время redeploy, а не зависит от default библиотеки.
            await self._conn.execute("PRAGMA busy_timeout=5000;")
            await self._conn.execute("PRAGMA foreign_keys=ON;")
            journal_cursor = await self._conn.execute("PRAGMA journal_mode=WAL;")
            journal_row = await journal_cursor.fetchone()
            if self._path != ":memory:" and (
                not journal_row or str(journal_row[0]).lower() != "wal"
            ):
                raise DatabaseConfigurationError(
                    "SQLite не смогла включить WAL; проверь filesystem Volume"
                )
            await self._conn.executescript(SCHEMA)
            await self._conn.commit()
            # Python sqlite3 не начинает implicit transaction для DDL. Явная
            # граница не даёт redeploy/crash оставить half-applied migration.
            await self._conn.execute("BEGIN IMMEDIATE;")
            await self._migrate()
        except BaseException as e:
            try:
                await self._conn.close()
            finally:
                self._conn = None
            if _is_permanent_storage_error(e):
                raise DatabaseConfigurationError(
                    "SQLite DB_PATH недоступен для записи/WAL; проверь Volume и permissions"
                ) from e
            raise

    @_serialized
    async def _migrate(self) -> None:
        """Добавляет колонки, появившиеся в схеме уже после первого релиза
        (CREATE TABLE IF NOT EXISTS не меняет существующие таблицы)."""
        cursor = await self.conn.execute("PRAGMA table_info(tracked_channels)")
        tracked_columns = {row[1] for row in await cursor.fetchall()}
        first_auto_report_migration = "auto_report_enabled" not in tracked_columns
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
                "preview_enabled": "INTEGER NOT NULL DEFAULT 0",
                "added_at": "REAL NOT NULL DEFAULT 0",
                "auto_report_enabled": "INTEGER NOT NULL DEFAULT 0",
                "last_message_kind": "TEXT NOT NULL DEFAULT 'text'",
                "media_transition_pending": "INTEGER NOT NULL DEFAULT 0",
                "media_transition_target_kind": "TEXT NOT NULL DEFAULT 'video'",
                "live_post_ended": "INTEGER NOT NULL DEFAULT 0",
                "channel_report_enabled": "INTEGER NOT NULL DEFAULT 0",
                "post_recipient_chat_id": "INTEGER",
                "report_format": "TEXT NOT NULL DEFAULT 'brief'",
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
        if first_auto_report_migration:
            # SQLite не поддерживает смену DEFAULT существующей колонки. Все
            # старые значения format намеренно сбрасываются в brief один раз;
            # после этого новые подписки тоже получают brief на уровне схемы.
            if "report_format" in tracked_columns:
                await self.conn.execute(
                    "ALTER TABLE tracked_channels DROP COLUMN report_format"
                )
                await self.conn.execute(
                    "ALTER TABLE tracked_channels ADD COLUMN report_format "
                    "TEXT NOT NULL DEFAULT 'brief'"
                )
            await self.conn.execute(
                "UPDATE tracked_channels SET auto_report_enabled = 0, "
                "report_format = 'brief', channel_report_enabled = 0"
            )
            await self.conn.execute(
                "UPDATE report_deliveries SET terminal_failed = 1, "
                "terminal_reason = 'auto_report_rollout_disabled', updated_at = ? "
                "WHERE terminal_failed = 0 AND "
                "(text_sent = 0 OR (report_format = 'full' AND html_sent = 0))",
                (time.time(),),
            )
            await self.conn.execute("DELETE FROM deferred_reports")
            await self.conn.execute("DELETE FROM quiet_hours_digest_sent")
        # Старые БД проходят пересоздание deferred_reports внутри миграции, поэтому
        # индекс health-агрегата гарантируем уже после возможной замены таблицы.
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deferred_reports_health "
            "ON deferred_reports (ended_at)"
        )
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
        await self._migrate_growth_schema()
        await self._migrate_streamer_schema()
        await self._migrate_streamer_communities_schema()
        await self._migrate_streamer_template_schema()
        await self._migrate_streamer_preset_schema()
        await self._migrate_streamer_stats_schema()
        await self._migrate_billing_schema()
        await self._migrate_billing_subject_schema()
        await self._migrate_viewer_schema()
        await self._migrate_viewer_preferences_schema()
        await self._migrate_viewer_favorites_schema()
        from .viewer_undo import migrate as migrate_viewer_undo
        await migrate_viewer_undo(self.conn)
        await self._migrate_viewer_reminder_schema()
        await self._migrate_viewer_folder_schema()
        await self._migrate_viewer_history_schema()
        await self._migrate_viewer_trial_schema()
        await self._migrate_category_alert_schema()
        await self._migrate_category_delivery_schema()
        await self._migrate_streamer_intents_schema()
        await self._migrate_growth_attribution_schema()
        await migrate_plus_payments(self.conn, now=time.time())
        await self.conn.commit()

    async def _migrate_growth_attribution_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS growth_referral_codes ("
            "code TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL UNIQUE, "
            "created_at REAL NOT NULL) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS growth_attributions ("
            "telegram_user_id INTEGER PRIMARY KEY, source_kind TEXT NOT NULL "
            "CHECK(source_kind IN ('site','referral')), source_code TEXT NOT NULL, "
            "referrer_user_id INTEGER, first_seen_at REAL NOT NULL, "
            "activated_at REAL) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_growth_source "
            "ON growth_attributions(source_kind,activated_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_growth_test_grant "
            "ON entitlement_grants(subject_kind,subject_id,plan,source,created_at)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('r8_001_growth_attribution',?)", (time.time(),)
        )

    async def _migrate_viewer_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_alert_filters ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "games_json TEXT NOT NULL, title_keywords_json TEXT NOT NULL, "
            "exclude_keywords_json TEXT NOT NULL, version INTEGER NOT NULL, "
            "updated_at REAL NOT NULL, PRIMARY KEY(telegram_user_id,twitch_login)) "
            "WITHOUT ROWID"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('r7_001_viewer_filters',?)", (time.time(),)
        )

    async def _migrate_viewer_preferences_schema(self) -> None:
        # Legacy rowid records insertion order. Freeze it before future writes.
        await self.conn.execute(
            "UPDATE tracked_channels SET added_at=CAST(rowid AS REAL) WHERE added_at=0"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_video_selection_state ("
            "telegram_user_id INTEGER PRIMARY KEY, version INTEGER NOT NULL) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_plan_priority ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "priority INTEGER NOT NULL, PRIMARY KEY(telegram_user_id,twitch_login)) "
            "WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_video_selections ("
            "telegram_user_id INTEGER NOT NULL, broadcaster_id TEXT NOT NULL, "
            "twitch_login TEXT NOT NULL, position INTEGER NOT NULL, "
            "PRIMARY KEY(telegram_user_id,broadcaster_id), "
            "UNIQUE(telegram_user_id,twitch_login), "
            "UNIQUE(telegram_user_id,position)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_001_viewer_preferences',?)", (time.time(),)
        )

    async def _migrate_viewer_favorites_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_favorites ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "PRIMARY KEY(telegram_user_id,twitch_login), "
            "FOREIGN KEY(telegram_user_id,twitch_login) "
            "REFERENCES tracked_channels(chat_id,twitch_login) ON DELETE CASCADE) WITHOUT ROWID"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_010_viewer_favorites',?)", (time.time(),)
        )

    async def _migrate_viewer_reminder_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_reminders ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "broadcaster_id TEXT NOT NULL, logical_stream_id TEXT NOT NULL, "
            "delay_minutes INTEGER NOT NULL CHECK(delay_minutes IN (15,30)), "
            "due_at REAL NOT NULL, version INTEGER NOT NULL, "
            "queued_version INTEGER NOT NULL DEFAULT 0, "
            "status TEXT NOT NULL CHECK(status IN "
            "('scheduled','sending','cancelled','sent','suppressed','unknown')), "
            "updated_at REAL NOT NULL, "
            "PRIMARY KEY(telegram_user_id,twitch_login)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_reminders_due "
            "ON viewer_reminders(status,due_at,telegram_user_id)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_005_viewer_reminders',?)", (time.time(),)
        )

    async def _migrate_viewer_folder_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_folders ("
            "id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL, "
            "name TEXT NOT NULL, name_key TEXT NOT NULL, version INTEGER NOT NULL, "
            "games_json TEXT NOT NULL, title_keywords_json TEXT NOT NULL, "
            "exclude_keywords_json TEXT NOT NULL, updated_at REAL NOT NULL, "
            "UNIQUE(telegram_user_id,name_key)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_folders_owner "
            "ON viewer_folders(telegram_user_id,name_key)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_folder_memberships ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "folder_id TEXT NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(telegram_user_id,twitch_login)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_folder_memberships_folder "
            "ON viewer_folder_memberships(folder_id,telegram_user_id)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_006_viewer_folders',?)", (time.time(),)
        )

    async def _migrate_viewer_history_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_event_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "telegram_user_id INTEGER NOT NULL, event_key TEXT NOT NULL UNIQUE, "
            "kind TEXT NOT NULL CHECK(kind IN "
            "('go_live','viewer_category_change','viewer_reminder')), "
            "twitch_login TEXT NOT NULL, logical_stream_id TEXT NOT NULL, "
            "category_name TEXT, outcome TEXT NOT NULL CHECK(outcome IN "
            "('sent','suppressed','unknown')), happened_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_event_history_owner "
            "ON viewer_event_history(telegram_user_id,id DESC)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_viewer_event_history_retention "
            "ON viewer_event_history(happened_at)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_007_viewer_history',?)", (time.time(),)
        )

    async def _migrate_viewer_trial_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS viewer_test_trials ("
            "telegram_user_id INTEGER PRIMARY KEY, grant_id TEXT NOT NULL UNIQUE, "
            "started_at REAL NOT NULL, expires_at REAL NOT NULL) WITHOUT ROWID"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_009_viewer_trial',?)", (time.time(),)
        )

    async def _migrate_category_alert_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS category_alert_state ("
            "broadcaster_id TEXT PRIMARY KEY, logical_stream_id TEXT NOT NULL, "
            "baseline_id TEXT NOT NULL, baseline_name TEXT, candidate_id TEXT, "
            "candidate_name TEXT, candidate_since REAL, "
            "candidate_count INTEGER NOT NULL, last_observed_at REAL NOT NULL, "
            "sequence INTEGER NOT NULL, is_live INTEGER NOT NULL DEFAULT 1) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS category_transitions ("
            "transition_id TEXT PRIMARY KEY, broadcaster_id TEXT NOT NULL, "
            "logical_stream_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
            "from_category_id TEXT NOT NULL, from_category_name TEXT, "
            "to_category_id TEXT NOT NULL, to_category_name TEXT, "
            "observed_at REAL NOT NULL, "
            "UNIQUE(broadcaster_id,logical_stream_id,sequence)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_category_transitions_broadcaster "
            "ON category_transitions(broadcaster_id,observed_at)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_002_category_alerts',?)", (time.time(),)
        )

    async def _migrate_category_delivery_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS category_alert_preferences ("
            "telegram_user_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "enabled INTEGER NOT NULL DEFAULT 0, category_ids_json TEXT NOT NULL DEFAULT '[]', "
            "category_names_json TEXT NOT NULL DEFAULT '[]', "
            "version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, "
            "PRIMARY KEY(telegram_user_id,twitch_login)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS category_alert_delivery_state ("
            "telegram_user_id INTEGER NOT NULL, broadcaster_id TEXT NOT NULL, "
            "logical_stream_id TEXT NOT NULL, last_transition_id TEXT NOT NULL, "
            "last_sent_at REAL NOT NULL, "
            "PRIMARY KEY(telegram_user_id,broadcaster_id,logical_stream_id)) WITHOUT ROWID"
        )
        cursor = await self.conn.execute("PRAGMA table_info(notification_jobs)")
        if "category_transition_id" not in {row[1] for row in await cursor.fetchall()}:
            await self.conn.execute(
                "ALTER TABLE notification_jobs ADD COLUMN category_transition_id TEXT"
            )
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_category_job_unique "
            "ON notification_jobs(chat_id,category_transition_id) "
            "WHERE kind='viewer_category_change' AND category_transition_id IS NOT NULL"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_003_category_delivery',?)", (time.time(),)
        )

    async def _migrate_streamer_intents_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_connect_intents ("
            "intent_id TEXT PRIMARY KEY,telegram_user_id INTEGER NOT NULL, "
            "state_digest TEXT NOT NULL UNIQUE,created_at REAL NOT NULL, "
            "expires_at REAL NOT NULL,status TEXT NOT NULL, "
            "twitch_login TEXT) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_streamer_connect_intents_user "
            "ON streamer_connect_intents(telegram_user_id,created_at)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_community_intents ("
            "intent_id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL, "
            "request_id INTEGER NOT NULL UNIQUE, created_at REAL NOT NULL, "
            "expires_at REAL NOT NULL, chat_type TEXT NOT NULL, "
            "status TEXT NOT NULL, prepared_id TEXT, chat_id INTEGER) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_community_intents_user "
            "ON streamer_community_intents(telegram_user_id,created_at)"
        )
        columns = {row[1] for row in await (await self.conn.execute(
            "PRAGMA table_info(streamer_community_intents)"
        )).fetchall()}
        if "permission_reason" not in columns:
            await self.conn.execute("ALTER TABLE streamer_community_intents ADD COLUMN permission_reason TEXT")
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('r11_005_channel_intent_reasons',?)", (time.time(),)
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_004_streamer_intents',?)", (time.time(),)
        )

    async def _migrate_growth_schema(self) -> None:
        """Add R3 tables inside the caller's BEGIN IMMEDIATE boundary."""
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS stream_observations ("
            "twitch_login TEXT NOT NULL, stream_id TEXT NOT NULL, "
            "sampled_at REAL NOT NULL, viewer_count INTEGER NOT NULL, "
            "title TEXT NOT NULL, game_name TEXT NOT NULL, "
            "PRIMARY KEY (twitch_login, stream_id, sampled_at)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stream_observations_retention "
            "ON stream_observations (sampled_at)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS stream_observation_memberships ("
            "chat_id INTEGER NOT NULL, twitch_login TEXT NOT NULL, "
            "stream_id TEXT NOT NULL, sampled_at REAL NOT NULL, "
            "PRIMARY KEY (chat_id, twitch_login, stream_id, sampled_at)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stream_observation_memberships_stream "
            "ON stream_observation_memberships (twitch_login, stream_id, sampled_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stream_samples_stream_mode "
            "ON stream_samples (twitch_login, stream_id)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES ('r3_001_observations', ?)", (time.time(),)
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS notification_jobs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "kind TEXT NOT NULL, chat_id INTEGER NOT NULL, "
            "twitch_login TEXT NOT NULL, logical_stream_id TEXT NOT NULL, "
            "payload_version INTEGER NOT NULL, due_at REAL NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN ('pending', 'leased', 'done', 'failed')), "
            "attempt_count INTEGER NOT NULL DEFAULT 0, lease_until REAL, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "last_error_class TEXT, "
            "UNIQUE (kind, chat_id, twitch_login, logical_stream_id, payload_version))"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_jobs_due "
            "ON notification_jobs (status, due_at, id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_jobs_lease "
            "ON notification_jobs (status, lease_until, id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_jobs_retention "
            "ON notification_jobs (status, updated_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_jobs_active_go_live "
            "ON notification_jobs (chat_id, twitch_login, logical_stream_id) "
            "WHERE kind = 'go_live' AND payload_version = 1 "
            "AND status IN ('pending', 'leased', 'failed')"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES ('r3_002_notification_jobs', ?)", (time.time(),)
        )
        cursor = await self.conn.execute("PRAGMA table_info(notification_jobs)")
        job_columns = {row[1] for row in await cursor.fetchall()}
        if "revision" not in job_columns:
            await self.conn.execute(
                "ALTER TABLE notification_jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
            )
        if "media_url" not in job_columns:
            await self.conn.execute(
                "ALTER TABLE notification_jobs ADD COLUMN media_url TEXT"
            )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
            "VALUES ('r3_003_live_update_revision', ?)", (time.time(),)
        )

    async def schema_versions(self) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        return [row[0] for row in await cursor.fetchall()]

    async def _migrate_streamer_schema(self) -> None:
        """R4 identity and test entitlements in the existing migration transaction."""
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_identities ("
            "broadcaster_id TEXT PRIMARY KEY, telegram_user_id INTEGER NOT NULL UNIQUE, "
            "twitch_login TEXT NOT NULL, verified_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS entitlement_grants ("
            "grant_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, "
            "subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, "
            "plan TEXT NOT NULL, source TEXT NOT NULL, "
            "starts_at REAL NOT NULL, expires_at REAL NOT NULL, "
            "revoked_at REAL, issued_by INTEGER NOT NULL, created_at REAL NOT NULL, "
            "CHECK(expires_at > starts_at))"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entitlement_access "
            "ON entitlement_grants(subject_kind, subject_id, plan, starts_at, expires_at) "
            "WHERE revoked_at IS NULL"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS entitlement_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, grant_id TEXT NOT NULL, "
            "action TEXT NOT NULL CHECK(action IN ('grant', 'revoke')), "
            "actor_telegram_id INTEGER NOT NULL, happened_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r4_001_streamer_access', ?)", (time.time(),)
        )

    async def _migrate_streamer_communities_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_communities ("
            "broadcaster_id TEXT NOT NULL, chat_id INTEGER NOT NULL, "
            "title TEXT NOT NULL, chat_type TEXT NOT NULL, verified_at REAL NOT NULL, "
            "PRIMARY KEY(broadcaster_id, chat_id)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_streamer_communities_chat "
            "ON streamer_communities(chat_id, broadcaster_id)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r4_002_streamer_communities', ?)", (time.time(),)
        )

    async def _migrate_streamer_template_schema(self) -> None:
        cursor = await self.conn.execute("PRAGMA table_info(tracked_channels)")
        tracked_columns = {row[1] for row in await cursor.fetchall()}
        if "last_broadcaster_id" not in tracked_columns:
            await self.conn.execute(
                "ALTER TABLE tracked_channels ADD COLUMN last_broadcaster_id TEXT"
            )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_post_templates ("
            "broadcaster_id TEXT NOT NULL, chat_id INTEGER NOT NULL, "
            "version INTEGER NOT NULL CHECK(version > 0), "
            "headline TEXT NOT NULL, body TEXT NOT NULL, "
            "buttons_json TEXT NOT NULL, updated_at REAL NOT NULL, "
            "PRIMARY KEY(broadcaster_id, chat_id)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r4_003_streamer_templates', ?)", (time.time(),)
        )

    async def _migrate_streamer_preset_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_template_presets ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, broadcaster_id TEXT NOT NULL, "
            "name TEXT NOT NULL, name_key TEXT NOT NULL, headline TEXT NOT NULL, "
            "body TEXT NOT NULL, buttons_json TEXT NOT NULL, created_at REAL NOT NULL, "
            "UNIQUE(broadcaster_id,name_key))"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_streamer_template_presets_owner "
            "ON streamer_template_presets(broadcaster_id,id)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            "VALUES ('mini_008_streamer_presets',?)", (time.time(),)
        )

    async def _migrate_streamer_stats_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS streamer_post_events ("
            "broadcaster_id TEXT NOT NULL, chat_id INTEGER NOT NULL, "
            "twitch_login TEXT NOT NULL, stream_id TEXT NOT NULL, "
            "message_id INTEGER NOT NULL, published_at REAL NOT NULL, "
            "PRIMARY KEY(chat_id, twitch_login, stream_id, message_id)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_streamer_post_events_owner_time "
            "ON streamer_post_events(broadcaster_id, published_at)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r4_004_streamer_stats', ?)", (time.time(),)
        )

    async def _migrate_billing_schema(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_orders " + _BILLING_ORDER_DEFINITION
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_billing_orders_pending_expiry "
            "ON billing_orders(status, checkout_expires_at)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_billing_orders_owner "
            "ON billing_orders(telegram_user_id, created_at)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_payments ("
            "provider TEXT NOT NULL, payment_id TEXT NOT NULL, order_id TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK(status IN ('captured','refunded')), "
            "units INTEGER NOT NULL CHECK(units > 0), currency TEXT NOT NULL, "
            "captured_at REAL NOT NULL, refunded_at REAL, "
            "PRIMARY KEY(provider,payment_id)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_billing_payments_order "
            "ON billing_payments(order_id)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_webhook_events ("
            "provider TEXT NOT NULL, event_id TEXT NOT NULL, body_sha256 TEXT NOT NULL, "
            "order_id TEXT NOT NULL, event_type TEXT NOT NULL, processed_at REAL NOT NULL, "
            "result_status TEXT NOT NULL, "
            "PRIMARY KEY(provider,event_id)) WITHOUT ROWID"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_audit ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, "
            "action TEXT NOT NULL, happened_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_billing_audit_order "
            "ON billing_audit(order_id,id)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS billing_refund_requests ("
            "request_key TEXT PRIMARY KEY, order_id TEXT NOT NULL, "
            "reference TEXT NOT NULL, requested_at REAL NOT NULL)"
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r5_001_billing_ledger', ?)", (time.time(),)
        )

    async def _migrate_billing_subject_schema(self) -> None:
        """Rebuild legacy Streamer-only orders inside the outer migration transaction."""
        cursor = await self.conn.execute("PRAGMA table_info(billing_orders)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "subject_kind" not in columns:
            await self.conn.execute("CREATE TABLE billing_orders_v2 " + _BILLING_ORDER_DEFINITION)
            await self.conn.execute(
                "INSERT INTO billing_orders_v2 (" + _BILLING_ORDER_FIELDS + ") "
                "SELECT order_id,request_key,telegram_user_id,'streamer',broadcaster_id,"
                "broadcaster_id,plan,provider,status,units,currency,duration_seconds,"
                "created_at,checkout_expires_at,checkout_reference,paid_at,closed_at,grant_id "
                "FROM billing_orders"
            )
            await self.conn.execute("DROP TABLE billing_orders")
            await self.conn.execute("ALTER TABLE billing_orders_v2 RENAME TO billing_orders")
            await self.conn.execute(
                "CREATE INDEX idx_billing_orders_pending_expiry "
                "ON billing_orders(status, checkout_expires_at)"
            )
            await self.conn.execute(
                "CREATE INDEX idx_billing_orders_owner "
                "ON billing_orders(telegram_user_id, created_at)"
            )
        elif not {"subject_id", "broadcaster_id"}.issubset(columns):
            raise DatabaseConfigurationError("billing subject schema is incomplete")
        await self.conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES ('r10_001_billing_subjects', ?)", (time.time(),)
        )

    @staticmethod
    def _billing_order_from_row(row: tuple | None) -> BillingOrder | None:
        return BillingOrder(*row) if row is not None else None

    async def get_billing_order(self, order_id: str) -> BillingOrder | None:
        cursor = await self.conn.execute(
            "SELECT " + _BILLING_ORDER_READ_FIELDS + " FROM billing_orders WHERE order_id = ?",
            (order_id,),
        )
        return self._billing_order_from_row(await cursor.fetchone())

    async def list_billing_orders_for_user(
        self, telegram_user_id: int, *, limit: int = 20,
    ) -> list[BillingOrder]:
        if type(telegram_user_id) is not int or telegram_user_id <= 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid billing history request")
        cursor = await self.conn.execute(
            "SELECT " + _BILLING_ORDER_READ_FIELDS + " FROM billing_orders "
            "WHERE telegram_user_id=? ORDER BY created_at DESC,order_id DESC LIMIT ?",
            (telegram_user_id, limit),
        )
        return [BillingOrder(*row) for row in await cursor.fetchall()]

    async def get_current_plus_grant(
        self, telegram_user_id: int, plan: str, *, now: float | None = None,
    ) -> tuple[str, float] | None:
        if type(telegram_user_id) is not int or telegram_user_id <= 0 or plan not in {"viewer_plus", "streamer_plus"}:
            raise ValueError("invalid Plus subject")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid Plus time")
        if plan == "viewer_plus":
            cursor = await self.conn.execute(
                "SELECT source,expires_at FROM entitlement_grants "
                "WHERE subject_kind='viewer' AND subject_id=? AND plan='viewer_plus' "
                "AND revoked_at IS NULL AND starts_at<=? AND expires_at>? "
                "ORDER BY expires_at DESC LIMIT 1",
                (str(telegram_user_id), at, at),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT g.source,g.expires_at FROM entitlement_grants g "
                "JOIN streamer_identities i ON i.broadcaster_id=g.subject_id "
                "WHERE i.telegram_user_id=? AND g.subject_kind='streamer' "
                "AND g.plan='streamer_plus' AND g.revoked_at IS NULL "
                "AND g.starts_at<=? AND g.expires_at>? "
                "ORDER BY g.expires_at DESC LIMIT 1",
                (telegram_user_id, at, at),
            )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    async def get_billing_order_by_request_key(self, request_key: str) -> BillingOrder | None:
        cursor = await self.conn.execute(
            "SELECT " + _BILLING_ORDER_READ_FIELDS + " FROM billing_orders WHERE request_key = ?",
            (request_key,),
        )
        return self._billing_order_from_row(await cursor.fetchone())

    async def get_billing_payment(self, order_id: str) -> PaymentRecord | None:
        cursor = await self.conn.execute(
            "SELECT provider,payment_id,order_id,status,units,currency,captured_at,refunded_at "
            "FROM billing_payments WHERE order_id = ? LIMIT 1", (order_id,),
        )
        row = await cursor.fetchone()
        return PaymentRecord(*row) if row is not None else None

    async def get_billing_refund_request(self, request_key: str) -> tuple[str, str] | None:
        cursor = await self.conn.execute(
            "SELECT order_id,reference FROM billing_refund_requests WHERE request_key = ?",
            (request_key,),
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row is not None else None

    async def has_viewer_plus(self, telegram_user_id: int, *, now: float | None = None) -> bool:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            return False
        at = time.time() if now is None else now
        if type(at) not in (int, float) or not math.isfinite(at):
            return False
        return (await resolve_effective_viewer(self, telegram_user_id, now=at)).active

    async def get_video_selection(
        self, telegram_user_id: int, *, now: float | None = None,
    ) -> VideoSelection:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid viewer")
        cursor = await self.conn.execute(
            "SELECT version FROM viewer_video_selection_state WHERE telegram_user_id=?",
            (telegram_user_id,),
        )
        version_row = await cursor.fetchone()
        cursor = await self.conn.execute(
            "SELECT s.broadcaster_id,s.twitch_login,t.notify_enabled "
            "FROM viewer_video_selections s LEFT JOIN tracked_channels t "
            "ON t.chat_id=s.telegram_user_id AND t.twitch_login=s.twitch_login "
            "WHERE s.telegram_user_id=? ORDER BY s.position",
            (telegram_user_id,),
        )
        rows = [row for row in await cursor.fetchall() if row[2] is not None]
        plus = await self.has_viewer_plus(telegram_user_id, now=now)
        paused = {row[0] for row in await self.list_personal_channel_status(
            telegram_user_id, now=now,
        ) if row[4]}
        return VideoSelection(
            version=version_row[0] if version_row else 0,
            selected_ids=tuple(row[0] for row in rows),
            effective_ids=tuple(row[0] for row in rows if plus and row[2] and row[1] not in paused),
            selected_logins=tuple(row[1] for row in rows),
        )

    @_serialized
    async def replace_video_selection(
        self, telegram_user_id: int, choices: object, *,
        expected_version: int, now: float | None = None,
    ) -> VideoSelection | None:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid viewer")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid version")
        rows = validate_video_choices(choices)
        at = time.time() if now is None else now
        await self.conn.execute("BEGIN IMMEDIATE")
        if not await self.has_viewer_plus(telegram_user_id, now=at):
            raise PermissionError("Viewer Plus required")
        cursor = await self.conn.execute(
            "SELECT version FROM viewer_video_selection_state WHERE telegram_user_id=?",
            (telegram_user_id,),
        )
        current = await cursor.fetchone()
        version = current[0] if current else 0
        if version != expected_version:
            await self.conn.rollback()
            return None
        for _broadcaster_id, login in rows:
            cursor = await self.conn.execute(
                "SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
                (telegram_user_id, login),
            )
            if await cursor.fetchone() is None:
                raise ValueError("selection includes unsubscribed channel")
        await self.conn.execute(
            "DELETE FROM viewer_video_selections WHERE telegram_user_id=?",
            (telegram_user_id,),
        )
        for position, (broadcaster_id, login) in enumerate(rows):
            await self.conn.execute(
                "INSERT INTO viewer_video_selections "
                "(telegram_user_id,broadcaster_id,twitch_login,position) VALUES (?,?,?,?)",
                (telegram_user_id, broadcaster_id, login, position),
            )
        await self.conn.execute(
            "INSERT INTO viewer_video_selection_state(telegram_user_id,version) VALUES (?,?) "
            "ON CONFLICT(telegram_user_id) DO UPDATE SET version=excluded.version",
            (telegram_user_id, version + 1),
        )
        saved = await self.get_video_selection(telegram_user_id, now=at)
        await self.conn.commit()
        return saved

    @_serialized
    async def issue_test_viewer_plus(
        self, telegram_user_id: int, request_key: str, *, starts_at: float,
        expires_at: float, issued_by: int, now: float | None = None,
    ) -> str:
        at = time.time() if now is None else now
        if (
            type(telegram_user_id) is not int or telegram_user_id <= 0
            or type(issued_by) is not int or issued_by <= 0
            or not isinstance(request_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_key) is None
            or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in (starts_at, expires_at, at))
            or expires_at <= starts_at
        ):
            raise ValueError("invalid test Viewer Plus grant")
        cursor = await self.conn.execute(
            "SELECT grant_id,subject_kind,subject_id,plan,source,starts_at,expires_at,issued_by "
            "FROM entitlement_grants WHERE request_key=?", (request_key,),
        )
        existing = await cursor.fetchone()
        expected = ("viewer", str(telegram_user_id), "viewer_plus", "test",
                    starts_at, expires_at, issued_by)
        if existing is not None:
            if existing[1:] != expected:
                raise ValueError("test Viewer Plus request key conflict")
            return existing[0]
        grant_id = uuid.uuid4().hex
        await self.conn.execute(
            "INSERT INTO entitlement_grants "
            "(grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,issued_by,created_at,beneficiary_telegram_user_id) "
            "VALUES (?,?,'viewer',?,'viewer_plus','test',?,?,?,?,?)",
            (grant_id, request_key, str(telegram_user_id), starts_at,
             expires_at, issued_by, at, telegram_user_id),
        )
        await self.conn.execute(
            "INSERT INTO entitlement_events(grant_id,action,actor_telegram_id,happened_at) "
            "VALUES (?,'grant',?,?)", (grant_id, issued_by, at),
        )
        await self.conn.commit()
        return grant_id

    @_serialized
    async def revoke_test_viewer_plus(
        self, grant_id: str, *, revoked_at: float, issued_by: int,
    ) -> bool:
        if (
            not isinstance(grant_id, str) or not grant_id
            or type(issued_by) is not int or issued_by <= 0
            or not isinstance(revoked_at, (int, float)) or not math.isfinite(revoked_at)
        ):
            raise ValueError("invalid test Viewer Plus revoke")
        cursor = await self.conn.execute(
            "UPDATE entitlement_grants SET revoked_at=? WHERE grant_id=? "
            "AND subject_kind='viewer' AND plan='viewer_plus' "
            "AND source='test' AND revoked_at IS NULL",
            (revoked_at, grant_id),
        )
        if cursor.rowcount != 1:
            await self.conn.rollback()
            return False
        await self.conn.execute(
            "INSERT INTO entitlement_events(grant_id,action,actor_telegram_id,happened_at) "
            "VALUES (?,'revoke',?,?)", (grant_id, issued_by, revoked_at),
        )
        await self.conn.commit()
        return True

    async def get_viewer_filter(
        self, telegram_user_id: int, twitch_login: str,
    ) -> tuple[int, ViewerFilter] | None:
        cursor = await self.conn.execute(
            "SELECT version,games_json,title_keywords_json,exclude_keywords_json "
            "FROM viewer_alert_filters WHERE telegram_user_id=? AND twitch_login=?",
            (telegram_user_id, twitch_login.lower()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return (row[0], validate_viewer_filter(
            json.loads(row[1]), json.loads(row[2]), json.loads(row[3]),
        ))

    async def list_viewer_filters(
        self, telegram_user_id: int,
    ) -> dict[str, tuple[int, ViewerFilter]]:
        cursor = await self.conn.execute(
            "SELECT twitch_login,version,games_json,title_keywords_json,exclude_keywords_json "
            "FROM viewer_alert_filters WHERE telegram_user_id=?",
            (telegram_user_id,),
        )
        rows = await cursor.fetchall()
        return {
            row[0]: (row[1], validate_viewer_filter(
                json.loads(row[2]), json.loads(row[3]), json.loads(row[4]),
            ))
            for row in rows
        }

    async def get_effective_viewer_filter(
        self, telegram_user_id: int, twitch_login: str, *, now: float | None = None,
    ) -> ViewerFilter | None:
        if type(telegram_user_id) is not int or telegram_user_id <= 0 or not isinstance(twitch_login, str):
            return None
        at = time.time() if now is None else now
        if not await self.has_viewer_plus(telegram_user_id, now=at):
            return None
        cursor = await self.conn.execute(
            "SELECT f.games_json,f.title_keywords_json,f.exclude_keywords_json "
            "FROM viewer_alert_filters f WHERE f.telegram_user_id=? AND f.twitch_login=? LIMIT 1",
            (telegram_user_id, twitch_login.lower()),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await self.conn.execute(
                "SELECT f.games_json,f.title_keywords_json,f.exclude_keywords_json "
                "FROM viewer_folder_memberships m JOIN viewer_folders f "
                "ON f.id=m.folder_id AND f.telegram_user_id=m.telegram_user_id "
                "JOIN tracked_channels c ON c.chat_id=m.telegram_user_id "
                "AND c.twitch_login=m.twitch_login "
                "WHERE m.telegram_user_id=? AND m.twitch_login=? LIMIT 1",
                (telegram_user_id, twitch_login.lower()),
            )
            row = await cursor.fetchone()
        return (validate_viewer_filter(
            json.loads(row[0]), json.loads(row[1]), json.loads(row[2]),
        ) if row is not None else None)

    @_serialized
    async def save_viewer_filter(
        self, telegram_user_id: int, twitch_login: str, *, expected_version: int,
        games: object, title_keywords: object, exclude_keywords: object,
        now: float | None = None,
    ) -> int | None:
        rule = validate_viewer_filter(games, title_keywords, exclude_keywords)
        at = time.time() if now is None else now
        if (
            type(telegram_user_id) is not int or telegram_user_id <= 0
            or not isinstance(twitch_login, str)
            or re.fullmatch(r"[A-Za-z0-9_]{2,25}", twitch_login) is None
            or type(expected_version) is not int or expected_version < 0
            or not isinstance(at, (int, float)) or not math.isfinite(at)
        ):
            raise ValueError("invalid Viewer Plus filter write")
        if not await self.has_viewer_plus(telegram_user_id, now=at):
            raise PermissionError("Viewer Plus is required")
        login = twitch_login.lower()
        cursor = await self.conn.execute(
            "SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
            (telegram_user_id, login),
        )
        if await cursor.fetchone() is None:
            return None
        values = (
            json.dumps(rule.games, ensure_ascii=False),
            json.dumps(rule.title_keywords, ensure_ascii=False),
            json.dumps(rule.exclude_keywords, ensure_ascii=False),
        )
        if expected_version == 0:
            cursor = await self.conn.execute(
                "INSERT INTO viewer_alert_filters "
                "(telegram_user_id,twitch_login,games_json,title_keywords_json,"
                "exclude_keywords_json,version,updated_at) "
                "VALUES (?,?,?,?,?,1,?) "
                "ON CONFLICT(telegram_user_id,twitch_login) DO NOTHING",
                (telegram_user_id, login, *values, at),
            )
            version = 1
        else:
            cursor = await self.conn.execute(
                "UPDATE viewer_alert_filters SET games_json=?,title_keywords_json=?,"
                "exclude_keywords_json=?,version=version+1,updated_at=? "
                "WHERE telegram_user_id=? AND twitch_login=? AND version=?",
                (*values, at, telegram_user_id, login, expected_version),
            )
            version = expected_version + 1
        if cursor.rowcount != 1:
            await self.conn.rollback()
            return None
        await self.conn.commit()
        return version

    @_serialized
    async def delete_viewer_filter(
        self, telegram_user_id: int, twitch_login: str, *,
        expected_version: int, now: float | None = None,
    ) -> bool:
        at = time.time() if now is None else now
        if (
            type(telegram_user_id) is not int or telegram_user_id <= 0
            or not isinstance(twitch_login, str)
            or re.fullmatch(r"[A-Za-z0-9_]{2,25}", twitch_login) is None
            or type(expected_version) is not int or expected_version < 1
            or not isinstance(at, (int, float)) or not math.isfinite(at)
        ):
            raise ValueError("invalid Viewer Plus filter reset")
        if not await self.has_viewer_plus(telegram_user_id, now=at):
            raise PermissionError("Viewer Plus is required")
        cursor = await self.conn.execute(
            "DELETE FROM viewer_alert_filters WHERE telegram_user_id=? "
            "AND twitch_login=? AND version=? AND EXISTS "
            "(SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?) "
            "AND " + effective_viewer_predicate("?", "?"),
            (telegram_user_id, twitch_login.lower(), expected_version,
             telegram_user_id, twitch_login.lower(), telegram_user_id, at, at),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def create_billing_order(
        self, order_id: str, request_key: str, telegram_user_id: int,
        duration_seconds: int, *, now: float, plan: str = "streamer_plus",
        subject: BillingSubject | None = None,
    ) -> BillingOrder:
        if (
            not isinstance(order_id, str) or not re.fullmatch(r"[0-9a-f]{32}", order_id)
            or not isinstance(request_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_key) is None
            or type(telegram_user_id) is not int or telegram_user_id <= 0
            or type(duration_seconds) is not int or not 60 <= duration_seconds <= 2678400
            or not isinstance(now, (int, float)) or not math.isfinite(now)
            or plan not in {"viewer_plus", "streamer_plus"}
            or (subject is not None and not isinstance(subject, BillingSubject))
        ):
            raise ValueError("invalid billing order")
        if plan == "viewer_plus":
            expected_subject = BillingSubject("viewer", str(telegram_user_id))
            broadcaster_id = None
        else:
            identity = await self.get_streamer_identity(telegram_user_id)
            if identity is None:
                raise PermissionError("streamer identity is not linked")
            expected_subject = BillingSubject("streamer", identity[0])
            broadcaster_id = identity[0]
        if subject is not None and subject != expected_subject:
            raise PermissionError("billing subject is not owned by Telegram user")
        subject = expected_subject
        existing = await self.get_billing_order_by_request_key(request_key)
        if existing is not None:
            if (
                existing.telegram_user_id != telegram_user_id
                or existing.subject != subject
                or existing.broadcaster_id != broadcaster_id
                or existing.duration_seconds != duration_seconds
                or existing.plan != plan or existing.provider != "mock"
                or existing.units != 1 or existing.currency != "TEST"
            ):
                raise ValueError("billing request key conflict")
            return existing
        await self.conn.execute(
            "INSERT INTO billing_orders "
            "(order_id,request_key,telegram_user_id,subject_kind,subject_id,"
            "broadcaster_id,plan,provider,status,"
            "units,currency,duration_seconds,created_at,checkout_expires_at,beneficiary_telegram_user_id) "
            "VALUES (?,?,?,?,?,?,?,'mock','pending',1,'TEST',?,?,?,?)",
            (order_id, request_key, telegram_user_id, subject.kind, subject.subject_id,
             broadcaster_id, plan, duration_seconds, now, now + 900, telegram_user_id),
        )
        await self.conn.execute(
            "INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'created',?)",
            (order_id, now),
        )
        await self.conn.commit()
        return (await self.get_billing_order(order_id))

    @_serialized
    async def save_billing_checkout_reference(
        self, order_id: str, reference: str, *, now: float,
    ) -> BillingOrder:
        if not isinstance(reference, str) or not 1 <= len(reference) <= 256:
            raise ValueError("invalid checkout reference")
        cursor = await self.conn.execute(
            "UPDATE billing_orders SET checkout_reference = ? "
            "WHERE order_id = ? AND status = 'pending' "
            "AND checkout_expires_at > ? AND checkout_reference IS NULL",
            (reference, order_id, now),
        )
        order = await self.get_billing_order(order_id)
        if cursor.rowcount != 1 and (order is None or order.checkout_reference != reference):
            await self.conn.rollback()
            raise ValueError("checkout reference conflict")
        await self.conn.commit()
        return order

    @_serialized
    async def cancel_billing_order(
        self, telegram_user_id: int, order_id: str, *, now: float,
    ) -> bool:
        order = await self.get_billing_order(order_id)
        if order is None or order.telegram_user_id != telegram_user_id:
            raise PermissionError("billing order is not owned by Telegram user")
        if order.status in {"cancelled", "expired"}:
            return False
        if order.status != "pending":
            raise ValueError("only pending billing orders can be cancelled")
        status = "cancelled" if now < order.checkout_expires_at else "expired"
        cursor = await self.conn.execute(
            "UPDATE billing_orders SET status = ?, closed_at = ? "
            "WHERE order_id = ? AND status = 'pending'",
            (status, now, order_id),
        )
        if cursor.rowcount == 1:
            await self.conn.execute(
                "INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,?,?)",
                (order_id, status, now),
            )
        await self.conn.commit()
        return status == "cancelled" and cursor.rowcount == 1

    @_serialized
    async def expire_pending_billing_orders(self, *, now: float) -> int:
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("invalid expiry time")
        cursor = await self.conn.execute(
            "SELECT order_id FROM billing_orders "
            "WHERE status = 'pending' AND checkout_expires_at <= ?",
            (now,),
        )
        order_ids = [row[0] for row in await cursor.fetchall()]
        if not order_ids:
            return 0
        await self.conn.executemany(
            "UPDATE billing_orders SET status='expired', closed_at=? "
            "WHERE order_id=? AND status='pending'",
            [(now, order_id) for order_id in order_ids],
        )
        await self.conn.executemany(
            "INSERT INTO billing_audit(order_id,action,happened_at) "
            "VALUES (?,'expired',?)",
            [(order_id, now) for order_id in order_ids],
        )
        await self.conn.commit()
        return len(order_ids)

    @_serialized
    async def save_billing_refund_request(
        self, order_id: str, request_key: str, reference: str, *, now: float,
    ) -> str:
        if (
            not isinstance(request_key, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_key) is None
            or not isinstance(reference, str) or not 1 <= len(reference) <= 256
            or not isinstance(now, (int, float)) or not math.isfinite(now)
        ):
            raise ValueError("invalid refund request")
        existing = await self.get_billing_refund_request(request_key)
        if existing is not None:
            if existing[0] != order_id:
                raise ValueError("refund request key conflict")
            return existing[1]
        order = await self.get_billing_order(order_id)
        if order is None or order.status != "paid":
            raise ValueError("only paid orders can request a refund")
        await self.conn.execute(
            "INSERT INTO billing_refund_requests(request_key,order_id,reference,requested_at) "
            "VALUES (?,?,?,?)", (request_key, order_id, reference, now),
        )
        await self.conn.execute(
            "INSERT INTO billing_audit(order_id,action,happened_at) "
            "VALUES (?,'refund_requested',?)", (order_id, now),
        )
        await self.conn.commit()
        return reference

    @_serialized
    async def apply_verified_billing_event(
        self, event: VerifiedPaymentEvent, body_sha256: str, *, now: float,
    ) -> str:
        if (
            not isinstance(event, VerifiedPaymentEvent)
            or not isinstance(body_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", body_sha256) is None
            or not isinstance(now, (int, float)) or not math.isfinite(now)
        ):
            raise ValueError("invalid verified billing event")
        cursor = await self.conn.execute(
            "SELECT body_sha256,result_status FROM billing_webhook_events "
            "WHERE provider = ? AND event_id = ?",
            (event.provider, event.event_id),
        )
        previous = await cursor.fetchone()
        if previous is not None:
            if previous[0] != body_sha256:
                raise ValueError("billing event ID conflicts with existing body")
            return previous[1]
        order = await self.get_billing_order(event.order_id)
        if (
            order is None or order.provider != event.provider
            or order.units != event.units or order.currency != event.currency
        ):
            raise ValueError("billing event does not match an order")
        if now < order.created_at:
            raise ValueError("billing event predates its order")
        payment = await self.get_billing_payment(order.order_id)
        if event.event_type == "captured":
            if order.status == "paid":
                if payment is None or payment.payment_id != event.payment_id or payment.status != "captured":
                    raise ValueError("capture conflicts with paid order")
                if now < payment.captured_at:
                    raise ValueError("capture replay predates original capture")
            elif (
                order.status != "pending" or order.checkout_reference is None
                or now >= order.checkout_expires_at
            ):
                raise ValueError("capture arrived after checkout closed")
            else:
                cursor = await self.conn.execute(
                    "SELECT order_id FROM billing_payments "
                    "WHERE provider = ? AND payment_id = ?",
                    (event.provider, event.payment_id),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError("provider payment ID already belongs to another order")
                grant_id = uuid.uuid4().hex
                await self.conn.execute(
                    "INSERT INTO billing_payments "
                    "(provider,payment_id,order_id,status,units,currency,captured_at) "
                    "VALUES (?,?,?,'captured',?,?,?)",
                    (event.provider, event.payment_id, order.order_id,
                     event.units, event.currency, now),
                )
                await self.conn.execute(
                    "INSERT INTO entitlement_grants "
                    "(grant_id,request_key,subject_kind,subject_id,plan,source,"
                    "starts_at,expires_at,issued_by,created_at,beneficiary_telegram_user_id) "
                    "VALUES (?,?,?,?,?,'mock',?,?,0,?,?)",
                    (grant_id, "mock-order:" + order.order_id,
                     order.subject_kind, order.subject_id, order.plan,
                     now, now + order.duration_seconds, now, order.beneficiary_telegram_user_id),
                )
                await self.conn.execute(
                    "INSERT INTO entitlement_events "
                    "(grant_id,action,actor_telegram_id,happened_at) "
                    "VALUES (?,'grant',0,?)", (grant_id, now),
                )
                await self.conn.execute(
                    "UPDATE billing_orders SET status='paid', paid_at=?, grant_id=? "
                    "WHERE order_id=? AND status='pending'",
                    (now, grant_id, order.order_id),
                )
                await self.conn.execute(
                    "INSERT INTO billing_audit(order_id,action,happened_at) "
                    "VALUES (?,'captured',?)", (order.order_id, now),
                )
            result = "paid"
        elif event.event_type == "refunded":
            if payment is None or payment.payment_id != event.payment_id:
                raise ValueError("refund has no matching captured payment")
            if now < payment.captured_at:
                raise ValueError("refund predates capture")
            if order.status == "refunded" and payment.status == "refunded":
                result = "refunded"
            elif order.status != "paid" or payment.status != "captured" or order.grant_id is None:
                raise ValueError("refund arrived before capture")
            else:
                await self.conn.execute(
                    "UPDATE billing_payments SET status='refunded',refunded_at=? "
                    "WHERE provider=? AND payment_id=? AND status='captured'",
                    (now, event.provider, event.payment_id),
                )
                grant_cursor = await self.conn.execute(
                    "UPDATE entitlement_grants SET revoked_at=? "
                    "WHERE grant_id=? AND source='mock' AND revoked_at IS NULL",
                    (now, order.grant_id),
                )
                if grant_cursor.rowcount != 1:
                    raise ValueError("linked mock grant cannot be revoked")
                await self.conn.execute(
                    "INSERT INTO entitlement_events "
                    "(grant_id,action,actor_telegram_id,happened_at) "
                    "VALUES (?,'revoke',0,?)", (order.grant_id, now),
                )
                await self.conn.execute(
                    "UPDATE billing_orders SET status='refunded',closed_at=? "
                    "WHERE order_id=? AND status='paid'", (now, order.order_id),
                )
                await self.conn.execute(
                    "INSERT INTO billing_audit(order_id,action,happened_at) "
                    "VALUES (?,'refunded',?)", (order.order_id, now),
                )
                result = "refunded"
        else:
            raise ValueError("unsupported verified billing event")
        await self.conn.execute(
            "INSERT INTO billing_webhook_events "
            "(provider,event_id,body_sha256,order_id,event_type,processed_at,result_status) "
            "VALUES (?,?,?,?,?,?,?)",
            (event.provider, event.event_id, body_sha256, order.order_id,
             event.event_type, now, result),
        )
        await self.conn.commit()
        return result

    async def get_streamer_delivery_stats(
        self, telegram_user_id: int, *, since: float,
    ) -> dict[str, int | float | None]:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid Telegram user ID")
        if not isinstance(since, (int, float)) or not math.isfinite(since):
            raise ValueError("invalid start time")
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM streamer_communities c "
            "JOIN streamer_identities i ON i.broadcaster_id = c.broadcaster_id "
            "WHERE i.telegram_user_id = ?", (telegram_user_id,),
        )
        connected = int((await cursor.fetchone())[0])
        cursor = await self.conn.execute(
            "SELECT COUNT(*), MAX(e.published_at) FROM streamer_post_events e "
            "JOIN streamer_identities i ON i.broadcaster_id = e.broadcaster_id "
            "WHERE i.telegram_user_id = ? AND e.chat_id < 0 "
            "AND e.published_at >= ?", (telegram_user_id, since),
        )
        count, latest = await cursor.fetchone()
        return {
            "connected_communities": connected,
            "published_posts": int(count),
            "latest_published_at": latest,
        }

    @_serialized
    async def link_streamer_identity(
        self, telegram_user_id: int, broadcaster_id: str, twitch_login: str,
        *, verified_at: float,
    ) -> bool:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid Telegram user ID")
        if not isinstance(broadcaster_id, str) or not broadcaster_id.isascii() or not broadcaster_id.isdecimal() or int(broadcaster_id) <= 0:
            raise ValueError("invalid Twitch broadcaster ID")
        if not isinstance(twitch_login, str) or re.fullmatch(r"[A-Za-z0-9_]{2,25}", twitch_login) is None:
            raise ValueError("invalid Twitch login")
        if not isinstance(verified_at, (int, float)) or not math.isfinite(verified_at):
            raise ValueError("invalid verification time")
        cursor = await self.conn.execute(
            "SELECT broadcaster_id, telegram_user_id FROM streamer_identities "
            "WHERE broadcaster_id = ? OR telegram_user_id = ?",
            (broadcaster_id, telegram_user_id),
        )
        matches = await cursor.fetchall()
        if any(row != (broadcaster_id, telegram_user_id) for row in matches):
            return False
        await self.conn.execute(
            "INSERT INTO streamer_identities "
            "(broadcaster_id, telegram_user_id, twitch_login, verified_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(broadcaster_id) DO UPDATE SET "
            "twitch_login=excluded.twitch_login, verified_at=excluded.verified_at",
            (broadcaster_id, telegram_user_id, twitch_login.lower(), verified_at),
        )
        await self.conn.commit()
        return True

    async def get_streamer_identity(self, telegram_user_id: int) -> tuple[str, str] | None:
        cursor = await self.conn.execute(
            "SELECT broadcaster_id, twitch_login FROM streamer_identities "
            "WHERE telegram_user_id = ?", (telegram_user_id,),
        )
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else None

    @_serialized
    async def create_streamer_connect_intent(
        self, intent_id: str, telegram_user_id: int, state_digest: str, *, now: float,
    ) -> None:
        if (not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80
                or type(telegram_user_id) is not int or telegram_user_id <= 0
                or not isinstance(state_digest, str) or len(state_digest) != 64
                or type(now) not in (int, float) or not math.isfinite(now)):
            raise ValueError("invalid streamer connect intent")
        await self.conn.execute(
            "UPDATE streamer_connect_intents SET status='superseded' "
            "WHERE telegram_user_id=? AND status='pending'",
            (telegram_user_id,),
        )
        await self.conn.execute(
            "INSERT INTO streamer_connect_intents "
            "(intent_id,telegram_user_id,state_digest,created_at,expires_at,status) "
            "VALUES (?,?,?,?,?,'pending')",
            (intent_id, telegram_user_id, state_digest, now, now + 600),
        )
        await self.conn.commit()

    async def get_streamer_connect_intent(self, intent_id: str) -> tuple | None:
        cursor = await self.conn.execute(
            "SELECT intent_id,telegram_user_id,created_at,expires_at,status,twitch_login "
            "FROM streamer_connect_intents WHERE intent_id=?", (intent_id,),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

    @_serialized
    async def claim_streamer_connect_intent(
        self, intent_id: str, telegram_user_id: int, *, now: float,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE streamer_connect_intents SET status='verifying' "
            "WHERE intent_id=? AND telegram_user_id=? AND status='pending' "
            "AND expires_at>?",
            (intent_id, telegram_user_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def finish_streamer_connect_intent(
        self, intent_id: str, status: str, *, twitch_login: str | None = None,
    ) -> bool:
        if status not in {"connected", "failed", "conflict", "cancelled", "expired"}:
            raise ValueError("invalid streamer connect status")
        cursor = await self.conn.execute(
            "UPDATE streamer_connect_intents SET status=?,twitch_login=? "
            "WHERE intent_id=? AND status IN ('pending','verifying')",
            (status, twitch_login, intent_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def create_community_intent(
        self, intent_id: str, telegram_user_id: int, request_id: int,
        chat_type: str, *, now: float,
    ) -> bool:
        if (not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80
                or type(telegram_user_id) is not int or telegram_user_id <= 0
                or type(request_id) is not int or not 1 <= request_id <= 2**31 - 1
                or chat_type != "channel"
                or type(now) not in (int, float) or not math.isfinite(now)):
            raise ValueError("invalid community intent")
        cursor = await self.conn.execute(
            "INSERT INTO streamer_community_intents "
            "(intent_id,telegram_user_id,request_id,created_at,expires_at,chat_type,status) "
            "SELECT ?,?,?,?,?,?,'pending' WHERE EXISTS ("
            "SELECT 1 FROM streamer_identities WHERE telegram_user_id=?)",
            (intent_id, telegram_user_id, request_id, now, now + 600, chat_type,
             telegram_user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def get_community_intent(self, intent_id: str) -> tuple | None:
        cursor = await self.conn.execute(
            "SELECT intent_id,telegram_user_id,request_id,created_at,expires_at, "
            "chat_type,status,prepared_id,chat_id,permission_reason FROM streamer_community_intents "
            "WHERE intent_id=?", (intent_id,),
        )
        row = await cursor.fetchone()
        return tuple(row) if row else None

    @_serialized
    async def set_community_prepared_id(self, intent_id: str, prepared_id: str) -> bool:
        cursor = await self.conn.execute(
            "UPDATE streamer_community_intents SET prepared_id=? "
            "WHERE intent_id=? AND status='pending'",
            (prepared_id, intent_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def claim_community_intent(
        self, telegram_user_id: int, request_id: int, chat_id: int, *, now: float,
    ) -> tuple[str, str] | None:
        if (type(telegram_user_id) is not int or telegram_user_id <= 0
                or type(request_id) is not int or type(chat_id) is not int
                or chat_id >= 0 or not math.isfinite(now)):
            return None
        cursor = await self.conn.execute(
            "UPDATE streamer_community_intents SET status='verifying',chat_id=? "
            "WHERE telegram_user_id=? AND request_id=? AND status='pending' "
            "AND expires_at>? AND EXISTS (SELECT 1 FROM streamer_identities "
            "WHERE telegram_user_id=?) RETURNING intent_id,chat_type",
            (chat_id, telegram_user_id, request_id, now, telegram_user_id),
        )
        row = await cursor.fetchone()
        await self.conn.commit()
        return (row[0], row[1]) if row else None

    @_serialized
    async def finish_community_intent(
        self, intent_id: str, status: str, *, chat_id: int, permission_reason: str | None = None,
    ) -> bool:
        if status not in {"connected", "denied", "failed"}:
            raise ValueError("invalid community intent status")
        if permission_reason not in {None, "ready", "bot_absent", "bot_member", "missing_post_right",
                                     "user_denied", "wrong_chat_type", "network_error"}:
            raise ValueError("invalid permission reason")
        cursor = await self.conn.execute(
            "UPDATE streamer_community_intents SET status=?,chat_id=?,permission_reason=? "
            "WHERE intent_id=? AND status='verifying'",
            (status, chat_id, permission_reason, intent_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def cancel_community_intent(self, intent_id: str, telegram_user_id: int) -> bool:
        cursor = await self.conn.execute(
            "UPDATE streamer_community_intents SET status='cancelled' "
            "WHERE intent_id=? AND telegram_user_id=? AND status IN ('pending','verifying')",
            (intent_id, telegram_user_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def add_streamer_community(
        self, telegram_user_id: int, chat_id: int, title: str,
        chat_type: str, *, now: float | None = None, intent_id: str | None = None,
        verification_started: float | None = None,
    ) -> bool:
        if type(telegram_user_id) is not int or telegram_user_id <= 0 or type(chat_id) is not int or chat_id >= 0:
            raise ValueError("invalid community identity")
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError("invalid community title")
        if chat_type not in {"group", "supergroup", "channel"}:
            raise ValueError("invalid community type")
        base_at = time.time() if now is None else now
        clock_started = time.monotonic()
        if verification_started is not None:
            if intent_id is None or not isinstance(verification_started, (int, float)) or not math.isfinite(verification_started):
                raise ValueError("invalid verification clock")
            clock_started = verification_started
        if type(base_at) not in (int, float) or not math.isfinite(base_at):
            raise ValueError("invalid verification time")
        if intent_id is not None and (not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80 or chat_type != "channel"):
            raise ValueError("invalid channel intent")
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            at = (time.time() if now is None else
                  base_at + max(0, time.monotonic() - clock_started) if intent_id is not None else base_at)
            if intent_id is not None:
                cursor = await self.conn.execute(
                    "SELECT 1 FROM streamer_community_intents WHERE intent_id=? AND telegram_user_id=? "
                    "AND chat_id=? AND chat_type='channel' AND status='verifying' AND expires_at>?",
                    (intent_id, telegram_user_id, chat_id, at),
                )
                if await cursor.fetchone() is None:
                    await self.conn.rollback()
                    return False
            cursor = await self.conn.execute(
                "SELECT COUNT(*) FROM streamer_communities c "
                "JOIN streamer_identities i ON i.broadcaster_id = c.broadcaster_id "
                "WHERE i.telegram_user_id = ?", (telegram_user_id,),
            )
            count = (await cursor.fetchone())[0]
            cursor = await self.conn.execute(
                "SELECT 1 FROM streamer_communities c "
                "JOIN streamer_identities i ON i.broadcaster_id = c.broadcaster_id "
                "WHERE i.telegram_user_id = ? AND c.chat_id = ?",
                (telegram_user_id, chat_id),
            )
            if count >= 10 and await cursor.fetchone() is None:
                raise ValueError("community limit reached")
            cursor = await self.conn.execute(
                "INSERT INTO streamer_communities(broadcaster_id, chat_id, title, chat_type, verified_at) "
                "SELECT i.broadcaster_id, ?, ?, ?, ? FROM streamer_identities i "
                "WHERE i.telegram_user_id = ? "
                "ON CONFLICT(broadcaster_id, chat_id) DO UPDATE SET "
                "title=excluded.title, chat_type=excluded.chat_type, verified_at=excluded.verified_at",
                (chat_id, title.strip(), chat_type, at, telegram_user_id),
            )
            if cursor.rowcount != 1:
                await self.conn.rollback()
                return False
            if intent_id is not None:
                await self.conn.execute("UPDATE streamer_community_intents SET status='connected',permission_reason='ready' "
                                        "WHERE intent_id=? AND status='verifying'", (intent_id,))
            await self.conn.commit()
            return True
        except BaseException:
            await self.conn.rollback()
            raise

    async def list_streamer_communities(self, telegram_user_id: int) -> list[tuple[int, str, str]]:
        cursor = await self.conn.execute(
            "SELECT c.chat_id, c.title, c.chat_type FROM streamer_communities c "
            "JOIN streamer_identities i ON i.broadcaster_id = c.broadcaster_id "
            "WHERE i.telegram_user_id = ? ORDER BY c.chat_id LIMIT 10",
            (telegram_user_id,),
        )
        return [tuple(row) for row in await cursor.fetchall()]

    @_serialized
    async def save_streamer_template(
        self, telegram_user_id: int, chat_id: int, *, expected_version: int,
        headline: str, body: str, buttons: object, now: float | None = None,
    ) -> int | None:
        template = validate_streamer_template(headline, body, buttons)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid template version")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid update time")
        cursor = await self.conn.execute(
            "SELECT i.broadcaster_id FROM streamer_identities i "
            "JOIN streamer_communities c ON c.broadcaster_id = i.broadcaster_id "
            "AND c.chat_id = ? WHERE i.telegram_user_id = ? AND EXISTS ("
            "SELECT 1 FROM entitlement_grants g WHERE g.subject_kind = 'streamer' "
            "AND g.subject_id = i.broadcaster_id AND g.plan = 'streamer_plus' "
            "AND g.revoked_at IS NULL AND g.starts_at <= ? AND g.expires_at > ?)",
            (chat_id, telegram_user_id, at, at),
        )
        owner = await cursor.fetchone()
        if owner is None:
            return None
        broadcaster_id = owner[0]
        buttons_json = json.dumps(
            [{"label": button.label, "url": button.url} for button in template.buttons],
            ensure_ascii=False, separators=(",", ":"),
        )
        if expected_version == 0:
            cursor = await self.conn.execute(
                "INSERT INTO streamer_post_templates "
                "(broadcaster_id, chat_id, version, headline, body, buttons_json, updated_at) "
                "VALUES (?, ?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(broadcaster_id, chat_id) DO NOTHING",
                (broadcaster_id, chat_id, template.headline, template.body, buttons_json, at),
            )
            version = 1
        else:
            cursor = await self.conn.execute(
                "UPDATE streamer_post_templates SET version = version + 1, "
                "headline = ?, body = ?, buttons_json = ?, updated_at = ? "
                "WHERE broadcaster_id = ? AND chat_id = ? AND version = ?",
                (template.headline, template.body, buttons_json, at,
                 broadcaster_id, chat_id, expected_version),
            )
            version = expected_version + 1
        if cursor.rowcount != 1:
            await self.conn.rollback()
            return None
        await self.conn.commit()
        return version

    async def get_streamer_template(
        self, telegram_user_id: int, chat_id: int,
    ) -> tuple[int, str, str, list[dict[str, str]]] | None:
        cursor = await self.conn.execute(
            "SELECT t.version, t.headline, t.body, t.buttons_json "
            "FROM streamer_post_templates t JOIN streamer_identities i "
            "ON i.broadcaster_id = t.broadcaster_id "
            "JOIN streamer_communities c ON c.broadcaster_id = t.broadcaster_id "
            "AND c.chat_id = t.chat_id "
            "WHERE i.telegram_user_id = ? AND t.chat_id = ?",
            (telegram_user_id, chat_id),
        )
        row = await cursor.fetchone()
        return (row[0], row[1], row[2], json.loads(row[3])) if row else None

    async def get_active_streamer_template_for_destination(
        self, chat_id: int, twitch_login: str, *, now: float | None = None,
    ) -> StreamerTemplate | None:
        at = time.time() if now is None else now
        cursor = await self.conn.execute(
            "SELECT t.headline, t.body, t.buttons_json "
            "FROM tracked_channels tc JOIN streamer_identities i "
            "ON i.broadcaster_id = tc.last_broadcaster_id "
            "AND i.twitch_login = tc.twitch_login "
            "JOIN streamer_communities c ON c.broadcaster_id = i.broadcaster_id "
            "AND c.chat_id = tc.chat_id "
            "JOIN streamer_post_templates t ON t.broadcaster_id = i.broadcaster_id "
            "AND t.chat_id = tc.chat_id "
            "WHERE tc.chat_id = ? AND tc.twitch_login = ? AND tc.is_live = 1 "
            "AND EXISTS(SELECT 1 FROM entitlement_grants g "
            "WHERE g.subject_kind = 'streamer' AND g.subject_id = i.broadcaster_id "
            "AND g.plan = 'streamer_plus' AND g.revoked_at IS NULL "
            "AND g.starts_at <= ? AND g.expires_at > ?) LIMIT 1",
            (chat_id, twitch_login, at, at),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return validate_streamer_template(row[0], row[1], json.loads(row[2]))
        except (ValueError, TypeError):
            return None

    @_serialized
    async def save_verified_streamer_connection(
        self, telegram_user_id: int, result: UserTokenResult, *, verified_at: float, intent_id: str | None = None,
    ) -> bool:
        """Persist the just-verified Twitch result and Telegram link atomically."""
        broadcaster_id = result.broadcaster_id
        twitch_login = result.login
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid Telegram user ID")
        if not broadcaster_id.isascii() or not broadcaster_id.isdecimal() or int(broadcaster_id) <= 0:
            raise ValueError("invalid Twitch broadcaster ID")
        if re.fullmatch(r"[A-Za-z0-9_]{2,25}", twitch_login) is None:
            raise ValueError("invalid Twitch login")
        if not isinstance(verified_at, (int, float)) or not math.isfinite(verified_at):
            raise ValueError("invalid verification time")
        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            if intent_id is not None:
                cursor = await self.conn.execute(
                    "SELECT 1 FROM streamer_connect_intents WHERE intent_id=? AND telegram_user_id=? "
                    "AND status='verifying' AND expires_at>?", (intent_id, telegram_user_id, time.time()),
                )
                if await cursor.fetchone() is None:
                    await self.conn.rollback()
                    return False
            cursor = await self.conn.execute(
                "SELECT broadcaster_id, telegram_user_id FROM streamer_identities "
                "WHERE broadcaster_id = ? OR telegram_user_id = ?",
                (broadcaster_id, telegram_user_id),
            )
            if any(row != (broadcaster_id, telegram_user_id) for row in await cursor.fetchall()):
                await self.conn.rollback()
                return False
            await self.conn.execute(
                "INSERT INTO streamer_identities "
                "(broadcaster_id, telegram_user_id, twitch_login, verified_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(broadcaster_id) DO UPDATE SET "
                "twitch_login=excluded.twitch_login, verified_at=excluded.verified_at",
                (broadcaster_id, telegram_user_id, twitch_login.lower(), verified_at),
            )
            await self.conn.execute(
                "INSERT INTO twitch_user_tokens "
                "(twitch_login, broadcaster_id, access_token, refresh_token, expires_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(twitch_login) DO UPDATE SET "
                "broadcaster_id=excluded.broadcaster_id, "
                "access_token=excluded.access_token, "
                "refresh_token=excluded.refresh_token, expires_at=excluded.expires_at",
                (
                    twitch_login.lower(), broadcaster_id,
                    self._encrypt_token(result.access_token),
                    self._encrypt_token(result.refresh_token), result.expires_at,
                ),
            )
            if intent_id is not None:
                await self.conn.execute("UPDATE streamer_connect_intents SET status='connected',twitch_login=? "
                                        "WHERE intent_id=? AND status='verifying'", (twitch_login.lower(), intent_id))
            await self.conn.commit()
            return True
        except BaseException:
            await self.conn.rollback()
            raise

    @_serialized
    async def issue_test_streamer_plus(
        self, broadcaster_id: str, request_key: str, *, starts_at: float,
        expires_at: float, issued_by: int, now: float | None = None,
        beneficiary_telegram_user_id: int | None = None,
    ) -> str:
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128 or not request_key.isascii():
            raise ValueError("invalid request key")
        if type(issued_by) is not int or issued_by <= 0:
            raise ValueError("invalid actor")
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in (starts_at, expires_at)) or expires_at <= starts_at:
            raise ValueError("invalid entitlement interval")
        created_at = time.time() if now is None else now
        if not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
            raise ValueError("invalid creation time")
        if beneficiary_telegram_user_id is not None:
            if type(beneficiary_telegram_user_id) is not int or beneficiary_telegram_user_id <= 0:
                raise ValueError("invalid beneficiary")
            cursor = await self.conn.execute(
                "SELECT telegram_user_id FROM streamer_identities WHERE broadcaster_id=?",
                (broadcaster_id,),
            )
            identity = await cursor.fetchone()
            if identity is None or identity[0] != beneficiary_telegram_user_id:
                raise PermissionError("beneficiary must be the verified Telegram buyer")
        cursor = await self.conn.execute(
            "SELECT grant_id, subject_id, starts_at, expires_at, issued_by,beneficiary_telegram_user_id "
            "FROM entitlement_grants WHERE request_key = ?", (request_key,),
        )
        existing = await cursor.fetchone()
        if existing:
            if existing[1:] != (broadcaster_id, starts_at, expires_at, issued_by, beneficiary_telegram_user_id):
                raise ValueError("idempotency key conflicts with existing grant")
            return existing[0]
        cursor = await self.conn.execute(
            "SELECT 1 FROM streamer_identities WHERE broadcaster_id = ?", (broadcaster_id,),
        )
        if await cursor.fetchone() is None:
            raise ValueError("Twitch account is not linked to a verified Telegram user")
        grant_id = uuid.uuid4().hex
        await self.conn.execute(
            "INSERT INTO entitlement_grants "
            "(grant_id, request_key, subject_kind, subject_id, plan, source, "
            "starts_at, expires_at, issued_by, created_at,beneficiary_telegram_user_id) "
            "VALUES (?, ?, 'streamer', ?, 'streamer_plus', 'test', ?, ?, ?, ?, ?)",
            (grant_id, request_key, broadcaster_id, starts_at, expires_at, issued_by, created_at,
             beneficiary_telegram_user_id),
        )
        await self.conn.execute(
            "INSERT INTO entitlement_events(grant_id, action, actor_telegram_id, happened_at) "
            "VALUES (?, 'grant', ?, ?)", (grant_id, issued_by, created_at),
        )
        await self.conn.commit()
        return grant_id

    @_serialized
    async def revoke_test_streamer_plus(
        self, grant_id: str, *, revoked_at: float, issued_by: int,
    ) -> bool:
        if not isinstance(grant_id, str) or not grant_id or type(issued_by) is not int or issued_by <= 0 or not isinstance(revoked_at, (int, float)) or not math.isfinite(revoked_at):
            raise ValueError("invalid revoke request")
        cursor = await self.conn.execute(
            "UPDATE entitlement_grants SET revoked_at = ? "
            "WHERE grant_id = ? AND source = 'test' AND revoked_at IS NULL",
            (revoked_at, grant_id),
        )
        if cursor.rowcount != 1:
            await self.conn.rollback()
            return False
        await self.conn.execute(
            "INSERT INTO entitlement_events(grant_id, action, actor_telegram_id, happened_at) "
            "VALUES (?, 'revoke', ?, ?)", (grant_id, issued_by, revoked_at),
        )
        await self.conn.commit()
        return True

    async def has_streamer_plus(self, telegram_user_id: int, *, now: float | None = None) -> bool:
        return await self.get_streamer_plus_expiry(telegram_user_id, now=now) is not None

    async def get_streamer_placement_capabilities(
        self, broadcaster_id: str, chat_id: int, *, now: float
    ) -> tuple[bool, bool]:
        """Return verified placement and its own current Streamer Plus grant."""
        cursor = await self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM entitlement_grants g "
            "WHERE g.subject_kind='streamer' AND g.subject_id=c.broadcaster_id "
            "AND g.plan='streamer_plus' AND g.revoked_at IS NULL "
            "AND g.starts_at <= ? AND g.expires_at > ?) "
            "FROM streamer_communities c JOIN streamer_identities i "
            "ON i.broadcaster_id=c.broadcaster_id "
            "WHERE c.broadcaster_id=? AND c.chat_id=? LIMIT 1",
            (now, now, broadcaster_id, chat_id),
        )
        row = await cursor.fetchone()
        return (True, bool(row[0])) if row is not None else (False, False)

    async def get_streamer_plus_expiry(
        self, telegram_user_id: int, *, now: float | None = None,
    ) -> float | None:
        at = time.time() if now is None else now
        cursor = await self.conn.execute(
            "SELECT MAX(g.expires_at) FROM streamer_identities i JOIN entitlement_grants g "
            "ON g.subject_id = i.broadcaster_id "
            "WHERE i.telegram_user_id = ? AND g.subject_kind = 'streamer' "
            "AND g.plan = 'streamer_plus' AND g.revoked_at IS NULL "
            "AND g.starts_at <= ? AND g.expires_at > ?",
            (telegram_user_id, at, at),
        )
        return (await cursor.fetchone())[0]

    def _encrypt_token(self, value: str) -> str:
        if self._token_cipher is None or value.startswith(_ENCRYPTED_TOKEN_PREFIX):
            return value
        encrypted = self._token_cipher.encrypt(value.encode("utf-8")).decode("ascii")
        return _ENCRYPTED_TOKEN_PREFIX + encrypted

    def _decrypt_token(self, value: str) -> str:
        if not value.startswith(_ENCRYPTED_TOKEN_PREFIX):
            return value
        if self._token_cipher is None:
            raise DatabaseConfigurationError(
                "В базе есть зашифрованные Twitch-токены, но TOKEN_ENCRYPTION_KEY не задан"
            )
        payload = value[len(_ENCRYPTED_TOKEN_PREFIX):]
        try:
            return self._token_cipher.decrypt(payload.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as e:
            raise DatabaseConfigurationError(
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
                raise DatabaseConfigurationError(
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
            try:
                await self._conn.close()
            finally:
                self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database.connect() ещё не вызван"
        return self._conn

    @_serialized
    async def get_or_create_growth_referral_code(
        self, user_id: int, *, now: float | None = None,
    ) -> str:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid referral owner")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid referral timestamp")
        cursor = await self.conn.execute(
            "SELECT code FROM growth_referral_codes WHERE owner_user_id=?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is not None:
            return row[0]
        for _ in range(8):
            code = secrets.token_urlsafe(9)
            if REFERRAL_CODE_RE.fullmatch(code) is None:
                continue
            cursor = await self.conn.execute(
                "INSERT OR IGNORE INTO growth_referral_codes(code,owner_user_id,created_at) "
                "VALUES (?,?,?)", (code, user_id, at),
            )
            if cursor.rowcount == 1:
                await self.conn.commit()
                return code
        await self.conn.rollback()
        raise RuntimeError("could not allocate referral code")

    @_serialized
    async def record_growth_touch(
        self, user_id: int, payload: str, *, now: float | None = None,
    ) -> bool:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid attribution user")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid attribution timestamp")
        parsed = parse_growth_start_payload(payload)
        if parsed is None:
            return False
        source_kind, source_code = parsed
        cursor = await self.conn.execute(
            "SELECT 1 FROM growth_attributions WHERE telegram_user_id=?", (user_id,)
        )
        if await cursor.fetchone() is not None:
            return False
        cursor = await self.conn.execute(
            "SELECT 1 FROM tracked_channels WHERE chat_id=? LIMIT 1", (user_id,)
        )
        if await cursor.fetchone() is not None:
            return False
        referrer_id = None
        if source_kind == "referral":
            cursor = await self.conn.execute(
                "SELECT owner_user_id FROM growth_referral_codes WHERE code=?", (source_code,)
            )
            row = await cursor.fetchone()
            if row is None or row[0] == user_id:
                return False
            referrer_id = row[0]
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO growth_attributions "
            "(telegram_user_id,source_kind,source_code,referrer_user_id,first_seen_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, source_kind, source_code, referrer_id, at),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def growth_funnel_snapshot(self) -> list[dict[str, int | str]]:
        cursor = await self.conn.execute(
            "WITH sources(source) AS (VALUES ('site'), ('referral')) "
            "SELECT s.source, COUNT(a.telegram_user_id), COUNT(a.activated_at), "
            "COALESCE(SUM(CASE WHEN a.activated_at IS NOT NULL AND ("
            "EXISTS (SELECT 1 FROM entitlement_grants g "
            "WHERE g.subject_kind='viewer' "
            "AND g.subject_id=CAST(a.telegram_user_id AS TEXT) "
            "AND g.plan='viewer_plus' AND g.source='test' "
            "AND g.created_at>=a.activated_at) "
            "OR EXISTS (SELECT 1 FROM streamer_identities i "
            "JOIN entitlement_grants g ON g.subject_kind='streamer' "
            "AND g.subject_id=i.broadcaster_id "
            "AND g.plan='streamer_plus' AND g.source='test' "
            "AND g.created_at>=a.activated_at "
            "WHERE i.telegram_user_id=a.telegram_user_id)) "
            "THEN 1 ELSE 0 END),0) "
            "FROM sources s LEFT JOIN growth_attributions a ON a.source_kind=s.source "
            "GROUP BY s.source ORDER BY CASE s.source WHEN 'site' THEN 0 ELSE 1 END"
        )
        return [
            {"source": row[0], "touched": row[1], "activated": row[2],
             "ever_test_plus": row[3]}
            for row in await cursor.fetchall()
        ]

    async def _mark_growth_activation(self, chat_id: int) -> None:
        if chat_id > 0:
            await self.conn.execute(
                "UPDATE growth_attributions SET activated_at=? "
                "WHERE telegram_user_id=? AND activated_at IS NULL",
                (time.time(), chat_id),
            )

    async def _next_tracking_order(self, chat_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT COALESCE(MAX(added_at),0)+1 FROM tracked_channels WHERE chat_id=?",
            (chat_id,),
        )
        return float((await cursor.fetchone())[0])

    @_serialized
    async def add_channel(self, chat_id: int, twitch_login: str) -> bool:
        try:
            added_at = await self._next_tracking_order(chat_id)
            await self.conn.execute(
                "INSERT INTO tracked_channels (chat_id, twitch_login, added_at) VALUES (?, ?, ?)",
                (chat_id, twitch_login, added_at),
            )
            await self._mark_growth_activation(chat_id)
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            # Ограничение уникальности отменяет только сам INSERT, но оставляет
            # транзакцию открытой. Явно закрываем её до следующей операции.
            await self.conn.rollback()
            return False

    @_serialized
    async def add_channel_with_limit(
        self, chat_id: int, twitch_login: str, max_channels: int
    ) -> str:
        """Атомарно добавляет канал с per-chat лимитом.

        Возвращает ``created``, ``already`` или ``limit``. Повторная проверка под
        общей write-lock обязательна: между предварительной Twitch-валидацией двух
        параллельных запросов оба могли увидеть 49 строк и иначе создать 51-ю.
        """
        if type(chat_id) is not int or chat_id == 0 or type(max_channels) is not int or max_channels < 0:
            raise ValueError("invalid tracking limit")
        await self.conn.execute("BEGIN IMMEDIATE")
        cursor = await self.conn.execute(
            "SELECT 1 FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        if await cursor.fetchone() is not None:
            await self.conn.commit()
            return "already"

        if chat_id > 0:
            actual = (VIEWER_PLUS_CHANNEL_LIMIT if await self.has_viewer_plus(chat_id)
                      else FREE_VIEWER_CHANNEL_LIMIT)
        else:
            actual = FREE_VIEWER_CHANNEL_LIMIT
        effective_limit = min(max_channels, actual)

        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM tracked_channels WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        if row is not None and row[0] >= effective_limit:
            await self.conn.commit()
            return "limit"

        try:
            added_at = await self._next_tracking_order(chat_id)
            await self.conn.execute(
                "INSERT INTO tracked_channels (chat_id, twitch_login, added_at) VALUES (?, ?, ?)",
                (chat_id, twitch_login, added_at),
            )
            await self._mark_growth_activation(chat_id)
            await self.conn.commit()
            return "created"
        except aiosqlite.IntegrityError:
            await self.conn.rollback()
            return "already"

    @_serialized
    async def remove_channel(self, chat_id: int, twitch_login: str) -> bool:
        removed = await self._remove_channel_rows(chat_id, twitch_login)
        await self.conn.commit()
        return removed

    @_serialized
    async def remove_channel_with_undo(self, user_id: int, login: str):
        from .viewer_undo import snapshot, issue
        if type(user_id) is not int or user_id <= 0:
            raise ValueError('personal subscription required')
        await self.conn.execute('BEGIN IMMEDIATE')
        saved = await snapshot(self.conn, user_id, login)
        if saved is None:
            await self.conn.commit()
            return None
        await self._remove_channel_rows(user_id, login)
        result = await issue(self.conn, user_id, login, saved)
        await self.conn.commit()
        return result

    @_serialized
    async def undo_channel_removal(self, user_id: int, token: str):
        from .viewer_undo import restore
        if type(user_id) is not int or user_id <= 0 or not isinstance(token, str) or len(token) != 43:
            raise ValueError('invalid undo')
        await self.conn.execute('BEGIN IMMEDIATE')
        limit = VIEWER_PLUS_CHANNEL_LIMIT if await self.has_viewer_plus(user_id) else FREE_VIEWER_CHANNEL_LIMIT
        result = await restore(self.conn, user_id, token, limit)
        await self.conn.commit()
        return result

    async def _remove_channel_rows(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "DELETE FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        if chat_id > 0:
            await self.conn.execute(
                "DELETE FROM viewer_alert_filters WHERE telegram_user_id=? AND twitch_login=?",
                (chat_id, twitch_login),
            )
            await self.conn.execute(
                "DELETE FROM category_alert_preferences WHERE telegram_user_id=? AND twitch_login=?",
                (chat_id, twitch_login),
            )
            await self.conn.execute(
                "DELETE FROM viewer_plan_priority WHERE telegram_user_id=? AND twitch_login=?",
                (chat_id, twitch_login),
            )
            await self.conn.execute(
                "DELETE FROM viewer_folder_memberships WHERE telegram_user_id=? "
                "AND twitch_login=?", (chat_id, twitch_login),
            )
            deleted = await self.conn.execute(
                "DELETE FROM viewer_video_selections WHERE telegram_user_id=? AND twitch_login=?",
                (chat_id, twitch_login),
            )
            if deleted.rowcount:
                await self.conn.execute(
                    "UPDATE viewer_video_selection_state SET version=version+1 "
                    "WHERE telegram_user_id=?", (chat_id,),
                )
        return cursor.rowcount > 0

    @_serialized
    async def remove_all_channels(self, chat_id: int) -> int:
        """Атомарно удаляет current-state недоступного Telegram-чата.

        История, VOD и report outbox намеренно остаются: первые два — архив,
        pending delivery должна пройти final guard и стать terminal. Возвращает
        число снятых Twitch-каналов.
        """
        cursor = await self.conn.execute(
            "DELETE FROM tracked_channels WHERE chat_id = ?", (chat_id,)
        )
        removed = cursor.rowcount
        await self.conn.execute('DELETE FROM viewer_unfollow_undo WHERE telegram_user_id=?', (chat_id,))
        if chat_id > 0:
            await self.conn.execute(
                "DELETE FROM viewer_alert_filters WHERE telegram_user_id=?", (chat_id,)
            )
            await self.conn.execute(
                "DELETE FROM category_alert_preferences WHERE telegram_user_id=?", (chat_id,)
            )
            await self.conn.execute(
                "DELETE FROM viewer_plan_priority WHERE telegram_user_id=?", (chat_id,)
            )
            await self.conn.execute(
                "DELETE FROM viewer_folder_memberships WHERE telegram_user_id=?", (chat_id,)
            )
            await self.conn.execute(
                "DELETE FROM viewer_video_selections WHERE telegram_user_id=?", (chat_id,)
            )
            await self.conn.execute(
                "UPDATE viewer_video_selection_state SET version=version+1 "
                "WHERE telegram_user_id=?", (chat_id,),
            )
        for table in (
            "telegram_channels", "stats_recipients", "quiet_hours",
            "quiet_hours_digest_sent", "stream_chat_meta", "user_timezones",
        ):
            await self.conn.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
        # Удаляем и очередь, предназначенную самому чату, и указатели на отчёты,
        # источником которых был удалённый чат. Сама stream_history остаётся.
        await self.conn.execute(
            "DELETE FROM deferred_reports WHERE chat_id = ? OR source_chat_id = ?",
            (chat_id, chat_id),
        )
        # Marker без очереди смысла не имеет. Это также убирает marker личного
        # получателя, если его последние deferred rows пришли из удалённого source.
        await self.conn.execute(
            "DELETE FROM quiet_hours_digest_sent WHERE NOT EXISTS ("
            "SELECT 1 FROM deferred_reports d "
            "WHERE d.chat_id = quiet_hours_digest_sent.chat_id)"
        )
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

    async def list_personal_channel_status(
        self, telegram_user_id: int, *, now: float | None = None,
    ) -> list[tuple[str, bool, bool, float | None, bool]]:
        """Own viewer rows with the last confirmed live observation timestamp."""
        cursor = await self.conn.execute(
            "SELECT t.twitch_login,t.notify_enabled,t.is_live,t.last_seen_live_at, "
            "COALESCE(p.priority,0),t.added_at "
            "FROM tracked_channels t LEFT JOIN viewer_plan_priority p "
            "ON p.telegram_user_id=t.chat_id AND p.twitch_login=t.twitch_login "
            "WHERE t.chat_id=? ORDER BY COALESCE(p.priority,0),t.added_at,t.twitch_login",
            (telegram_user_id,),
        )
        rows = await cursor.fetchall()
        limit = (VIEWER_PLUS_CHANNEL_LIMIT if await self.has_viewer_plus(telegram_user_id, now=now)
                 else FREE_VIEWER_CHANNEL_LIMIT)
        result = [
            (row[0], bool(row[1]), bool(row[2]), row[3], index >= limit)
            for index, row in enumerate(rows)
        ]
        return sorted(result, key=lambda row: row[0])

    async def is_personal_channel_active(
        self, telegram_user_id: int, twitch_login: str, *, now: float | None = None,
    ) -> bool:
        rows = await self.list_personal_channel_status(telegram_user_id, now=now)
        return any(row[0] == twitch_login and not row[4] for row in rows)

    @_serialized
    async def promote_personal_channel(self, telegram_user_id: int, twitch_login: str) -> str:
        if type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid viewer")
        await self.conn.execute("BEGIN IMMEDIATE")
        rows = await self.list_personal_channel_status(telegram_user_id)
        target = next((row for row in rows if row[0] == twitch_login), None)
        if target is None:
            await self.conn.rollback()
            return "not_subscribed"
        if not target[4]:
            await self.conn.rollback()
            return "already"
        cursor = await self.conn.execute(
            "SELECT MIN(priority) FROM viewer_plan_priority WHERE telegram_user_id=?",
            (telegram_user_id,),
        )
        smallest = (await cursor.fetchone())[0]
        priority = min(0, smallest or 0) - 1
        await self.conn.execute(
            "INSERT INTO viewer_plan_priority(telegram_user_id,twitch_login,priority) "
            "VALUES (?,?,?) ON CONFLICT(telegram_user_id,twitch_login) "
            "DO UPDATE SET priority=excluded.priority",
            (telegram_user_id, twitch_login, priority),
        )
        await self.conn.commit()
        return "activated"

    async def list_live_channels(
        self, chat_id: int, *, twitch_login: str | None = None
    ) -> list[tuple[str, str, int | None, str | None]]:
        """(twitch_login, title, viewer_count, game_name) для каналов чата, которые сейчас
        в эфире. viewer_count/game_name — из последнего опроса, None если сэмплов ещё не было."""
        query = (
            "SELECT tc.twitch_login, tc.last_title, "
            "COALESCE(ss.viewer_count, obs.viewer_count) AS viewer_count, "
            "COALESCE(ss.game_name, obs.game_name) AS game_name "
            "FROM tracked_channels tc "
            "LEFT JOIN stream_samples ss ON ss.rowid = ("
            " SELECT sample.rowid FROM stream_samples sample "
            " WHERE sample.chat_id = tc.chat_id AND sample.twitch_login = tc.twitch_login "
            " AND sample.stream_id = tc.last_stream_id "
            " ORDER BY sample.sampled_at DESC LIMIT 1) "
            "LEFT JOIN stream_observation_memberships m ON "
            "m.chat_id = tc.chat_id AND m.twitch_login = tc.twitch_login "
            "AND m.stream_id = tc.last_stream_id AND m.sampled_at = ("
            " SELECT MAX(membership.sampled_at) FROM stream_observation_memberships membership "
            " WHERE membership.chat_id = tc.chat_id "
            " AND membership.twitch_login = tc.twitch_login "
            " AND membership.stream_id = tc.last_stream_id) "
            "LEFT JOIN stream_observations obs ON obs.twitch_login = m.twitch_login "
            "AND obs.stream_id = m.stream_id AND obs.sampled_at = m.sampled_at "
            "WHERE tc.chat_id = ? AND tc.is_live = 1 "
        )
        params: tuple[int] | tuple[int, str] = (chat_id,)
        if twitch_login is not None:
            query += "AND tc.twitch_login = ? "
            params = (chat_id, twitch_login)
        query += "ORDER BY tc.twitch_login"
        cursor = await self.conn.execute(query, params)
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
    ) -> list[tuple[str, bool, bool, int | None, str, bool, bool, bool, bool, bool]]:
        """(twitch_login, notify_enabled, preview_enabled,
        post_recipient_chat_id, report_format, raid_detection_enabled,
        quiet_hours_exempt, channel_report_enabled, auto_report_enabled,
        is_live) для всех каналов чата."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, notify_enabled, preview_enabled, "
            "post_recipient_chat_id, report_format, raid_detection_enabled, "
            "quiet_hours_exempt, channel_report_enabled, auto_report_enabled, "
            "is_live FROM tracked_channels "
            "WHERE chat_id = ? ORDER BY twitch_login",
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [
            (
                row[0], bool(row[1]), bool(row[2]), row[3], row[4] or "brief",
                bool(row[5]), bool(row[6]), bool(row[7]), bool(row[8]), bool(row[9]),
            )
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
    async def set_preview_enabled(
        self, chat_id: int, twitch_login: str, enabled: bool
    ) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET preview_enabled = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (int(enabled), chat_id, twitch_login),
        )
        await self.conn.commit()

    @_serialized
    async def set_personal_notify_if_subscribed(
        self, telegram_user_id: int, twitch_login: str, enabled: bool,
    ) -> bool:
        """Atomically update only an existing subscription owned by this user."""
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET notify_enabled=? "
            "WHERE chat_id=? AND twitch_login=?",
            (int(enabled), telegram_user_id, twitch_login),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def list_viewer_favorites(self, telegram_user_id: int) -> set[str]:
        cursor = await self.conn.execute(
            "SELECT twitch_login FROM viewer_favorites WHERE telegram_user_id=?", (telegram_user_id,)
        )
        return {row[0] for row in await cursor.fetchall()}

    @_serialized
    async def set_viewer_favorite(self, telegram_user_id: int, twitch_login: str, is_favorite: bool) -> bool:
        if type(is_favorite) is not bool or type(telegram_user_id) is not int or telegram_user_id <= 0:
            raise ValueError("invalid favorite")
        if is_favorite:
            await self.conn.execute(
                "INSERT OR IGNORE INTO viewer_favorites(telegram_user_id,twitch_login) "
                "SELECT chat_id,twitch_login FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
                (telegram_user_id, twitch_login),
            )
        else:
            await self.conn.execute(
                "DELETE FROM viewer_favorites WHERE telegram_user_id=? AND twitch_login=?",
                (telegram_user_id, twitch_login),
            )
        cursor = await self.conn.execute(
            "SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?", (telegram_user_id, twitch_login)
        )
        exists = await cursor.fetchone() is not None
        await self.conn.commit()
        return exists

    async def get_preview_enabled(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT preview_enabled FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    @_serialized
    async def set_auto_report_enabled(
        self, chat_id: int, twitch_login: str, enabled: bool
    ) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET auto_report_enabled = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (int(enabled), chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_auto_report_enabled(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT auto_report_enabled FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    @_serialized
    async def set_channel_report_enabled(
        self, chat_id: int, twitch_login: str, enabled: bool
    ) -> None:
        """Включает публичный итог только для конкретной tracked-пары.

        Регистрация Telegram-канала проверяется в routing/guard, а не превращает
        эту настройку в неявный opt-in.
        """
        await self.conn.execute(
            "UPDATE tracked_channels SET channel_report_enabled = ? "
            "WHERE chat_id = ? AND twitch_login = ?",
            (int(enabled), chat_id, twitch_login),
        )
        await self.conn.commit()

    async def get_channel_report_enabled(
        self, chat_id: int, twitch_login: str
    ) -> bool:
        cursor = await self.conn.execute(
            "SELECT channel_report_enabled FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row else False

    @_serialized
    async def set_report_format(self, chat_id: int, twitch_login: str, report_format: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET report_format = ? WHERE chat_id = ? AND twitch_login = ?",
            (report_format, chat_id, twitch_login),
        )
        await self.conn.commit()

    @_serialized
    async def save_report_preferences(self, chat_id: int, login: str, enabled: bool, format_: str, *, channel: bool) -> bool:
        if type(enabled) is not bool or type(channel) is not bool or format_ not in ('brief','full'):
            raise ValueError('invalid report preferences')
        field='channel_report_enabled' if channel else 'auto_report_enabled'
        cursor=await self.conn.execute(f'UPDATE tracked_channels SET {field}=?,report_format=? WHERE chat_id=? AND twitch_login=?',
                                       (int(enabled),format_,chat_id,login))
        await self.conn.commit()
        return cursor.rowcount==1

    async def get_report_format(self, chat_id: int, twitch_login: str) -> str:
        """'full' (текст + HTML-отчёт) или 'brief' (только текст). По умолчанию 'brief'."""
        cursor = await self.conn.execute(
            "SELECT report_format FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else "brief"

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
        """Получатель итогового отчёта с безопасным channel opt-in.

        Положительные per-channel/chat-wide привязки имеют приоритет. Обычная группа
        никогда не становится получателем. Telegram-канал получает итог в себя только
        когда он зарегистрирован и настройка конкретного Twitch-канала включена.
        """
        per_channel = await self.get_post_recipient(chat_id, twitch_login)
        if per_channel is not None and per_channel > 0:
            return per_channel
        default_recipient = await self.get_stats_recipient(chat_id)
        if default_recipient is not None and default_recipient > 0:
            return default_recipient
        if (
            chat_id < 0
            and await self.is_telegram_channel(chat_id)
            and await self.get_channel_report_enabled(chat_id, twitch_login)
        ):
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
    async def set_quiet_hours_notify_after(self, chat_id: int, enabled: bool) -> bool:
        cursor = await self.conn.execute(
            "UPDATE quiet_hours SET notify_after_enabled = ? WHERE chat_id = ?",
            (int(enabled), chat_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

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
            "SELECT tc.chat_id, tc.twitch_login, tc.is_live, tc.last_stream_id, tc.last_message_id, "
            "tc.last_message_kind, tc.last_title, tc.offline_since, tc.stream_started_at, "
            "tc.last_seen_live_at, tc.peak_viewers, tc.notify_enabled, tc.followers_at_start, tc.stats_sent, "
            "tc.added_at, COALESCE(p.priority,0), tc.last_broadcaster_id "
            "FROM tracked_channels tc LEFT JOIN viewer_plan_priority p "
            "ON p.telegram_user_id=tc.chat_id AND p.twitch_login=tc.twitch_login "
            "ORDER BY tc.twitch_login, tc.chat_id"
        )
        rows = await cursor.fetchall()
        at = time.time()
        grants = await self.conn.execute(
            "SELECT DISTINCT candidates.user_id FROM "
            "(SELECT CAST(subject_id AS INTEGER) AS user_id FROM entitlement_grants "
            "WHERE subject_kind='viewer' AND plan='viewer_plus' "
            "UNION SELECT beneficiary_telegram_user_id FROM entitlement_grants "
            "WHERE subject_kind='streamer' AND plan='streamer_plus' "
            "AND beneficiary_telegram_user_id IS NOT NULL) candidates WHERE "
            + effective_viewer_predicate("candidates.user_id", "?"),
            (at, at),
        )
        plus_users = {int(row[0]) for row in await grants.fetchall() if str(row[0]).isdecimal()}
        personal: dict[int, list[tuple[int, float, str]]] = {}
        for row in rows:
            if row[0] > 0:
                personal.setdefault(row[0], []).append((row[15], row[14], row[1]))
        allowed = {
            chat_id: {login for _, _, login in sorted(items)[:(
                VIEWER_PLUS_CHANNEL_LIMIT if chat_id in plus_users else FREE_VIEWER_CHANNEL_LIMIT
            )]}
            for chat_id, items in personal.items()
        }
        chats_by_login: dict[str, list[int]] = {}
        states: dict[tuple[int, str], tuple] = {}
        for row in rows:
            chat_id, login = row[0], row[1]
            paused = chat_id > 0 and login not in allowed[chat_id]
            if not paused:
                chats_by_login.setdefault(login, []).append(chat_id)
            states[(chat_id, login)] = (
                bool(row[2]), row[3], row[4], row[5], row[6], row[7], row[8],
                row[9], row[10], bool(row[11]) and not paused, row[12], bool(row[13]),
                row[16],
            )
        return chats_by_login, states

    async def list_private_media_cleanup_candidates(
        self,
    ) -> list[tuple[int, str, str, int]]:
        """Live private posts that may still show an animation after a plan change."""
        cursor = await self.conn.execute(
            "SELECT chat_id,twitch_login,last_stream_id,last_message_id "
            "FROM tracked_channels WHERE chat_id>0 AND is_live=1 "
            "AND last_stream_id IS NOT NULL AND last_message_id IS NOT NULL "
            "AND (last_message_kind='animation' OR "
            "(last_message_kind='photo' AND media_transition_pending=1 "
            "AND media_transition_target_kind='animation')) "
            "ORDER BY chat_id,twitch_login"
        )
        return [tuple(row) for row in await cursor.fetchall()]

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

    async def get_live_post_state(
        self, chat_id: int, twitch_login: str
    ) -> LivePostState | None:
        cursor = await self.conn.execute(
            "SELECT last_stream_id, last_message_id, last_message_kind, "
            "media_transition_pending, preview_enabled, notify_enabled, "
            "media_transition_target_kind, is_live "
            "FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return LivePostState(
            chat_id=chat_id,
            twitch_login=twitch_login,
            logical_stream_id=row[0],
            message_id=row[1],
            message_kind=row[2],
            media_transition_pending=bool(row[3]),
            preview_enabled=bool(row[4]),
            notify_enabled=bool(row[5]),
            media_transition_target_kind=row[6],
            is_live=bool(row[7]),
        )

    async def get_offline_cleanup_state(
        self, chat_id: int, twitch_login: str
    ) -> OfflineCleanupState | None:
        cursor = await self.conn.execute(
            "SELECT last_stream_id, last_message_id, offline_since, stats_sent, "
            "last_title, live_post_ended FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ? AND is_live = 0 "
            "AND last_stream_id IS NOT NULL AND last_message_id IS NOT NULL "
            "AND offline_since IS NOT NULL",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return OfflineCleanupState(
            logical_stream_id=row[0], message_id=row[1], offline_since=row[2],
            stats_sent=bool(row[3]), title=row[4], ended=bool(row[5]),
        ) if row is not None else None

    async def get_live_post_ended(self, chat_id: int, twitch_login: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT live_post_ended FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return bool(row[0]) if row is not None else False

    async def get_live_post_details(
        self, chat_id: int, twitch_login: str
    ) -> tuple[str | None, str | None]:
        """Возвращает заголовок и последнюю категорию текущего live-post."""
        cursor = await self.conn.execute(
            "SELECT last_title, last_stream_id FROM tracked_channels "
            "WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, None
        title, stream_id = row
        game_cursor = await self.conn.execute(
            "SELECT game_name FROM ("
            " SELECT game_name, sampled_at FROM stream_samples "
            " WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? "
            " UNION ALL "
            " SELECT o.game_name, o.sampled_at FROM stream_observation_memberships m "
            " JOIN stream_observations o USING (twitch_login, stream_id, sampled_at) "
            " WHERE m.chat_id = ? AND m.twitch_login = ? AND m.stream_id = ?) "
            "ORDER BY sampled_at DESC LIMIT 1",
            (chat_id, twitch_login, stream_id, chat_id, twitch_login, stream_id),
        )
        game_row = await game_cursor.fetchone()
        game_name = game_row[0] if game_row and game_row[0] not in (None, "—") else None
        return title, game_name

    @_serialized
    async def mark_live_post_ended_if_current(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET live_post_ended = 1 "
            "WHERE chat_id = ? AND twitch_login = ? AND last_stream_id = ? "
            "AND last_message_id = ? AND is_live = 0",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_preview_destination_states(
        self, twitch_login: str
    ) -> list[PreviewDestinationState]:
        now = time.time()
        cursor = await self.conn.execute(
            "SELECT tc.chat_id, tc.twitch_login, tc.notify_enabled, "
            f"{_EFFECTIVE_PREVIEW_SQL}, tc.is_live, tc.last_stream_id, "
            "tc.last_message_id, tc.last_message_kind, "
            "EXISTS(SELECT 1 FROM telegram_channels tgc "
            "WHERE tgc.chat_id = tc.chat_id), tc.last_stream_ended_at "
            "FROM tracked_channels tc WHERE tc.twitch_login = ? "
            "ORDER BY tc.chat_id",
            (now, now, twitch_login),
        )
        return [self._preview_destination_from_row(row) for row in await cursor.fetchall()]

    async def get_preview_destination_state(
        self, chat_id: int, twitch_login: str
    ) -> PreviewDestinationState | None:
        now = time.time()
        cursor = await self.conn.execute(
            "SELECT tc.chat_id, tc.twitch_login, tc.notify_enabled, "
            f"{_EFFECTIVE_PREVIEW_SQL}, tc.is_live, tc.last_stream_id, "
            "tc.last_message_id, tc.last_message_kind, "
            "EXISTS(SELECT 1 FROM telegram_channels tgc "
            "WHERE tgc.chat_id = tc.chat_id), tc.last_stream_ended_at "
            "FROM tracked_channels tc "
            "WHERE tc.chat_id = ? AND tc.twitch_login = ?",
            (now, now, chat_id, twitch_login),
        )
        row = await cursor.fetchone()
        return self._preview_destination_from_row(row) if row is not None else None

    @staticmethod
    def _preview_destination_from_row(row: tuple) -> PreviewDestinationState:
        return PreviewDestinationState(
            chat_id=row[0],
            twitch_login=row[1],
            notify_enabled=bool(row[2]),
            preview_enabled=bool(row[3]),
            is_live=bool(row[4]),
            logical_stream_id=row[5],
            message_id=row[6],
            message_kind=row[7] or "text",
            include_track_link=bool(row[8]),
            last_stream_ended_at=row[9],
        )

    @_serialized
    async def begin_video_transition(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET media_transition_pending = 1 "
            "WHERE chat_id = ? AND twitch_login = ? AND is_live = 1 "
            "AND last_stream_id = ? AND last_message_id = ?",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @_serialized
    async def begin_animation_transition(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET media_transition_pending = 1, "
            "media_transition_target_kind = 'animation' "
            "WHERE chat_id = ? AND twitch_login = ? AND is_live = 1 "
            "AND last_stream_id = ? AND last_message_id = ?",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @_serialized
    async def begin_photo_transition(
        self, chat_id: int, twitch_login: str, logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET media_transition_pending=1, "
            "media_transition_target_kind='photo' "
            "WHERE chat_id=? AND twitch_login=? AND is_live=1 "
            "AND last_stream_id=? AND last_message_id=? "
            "AND ((last_message_kind='animation' AND media_transition_pending=0) "
            "OR (last_message_kind IN ('photo','animation') "
            "AND media_transition_pending=1 "
            "AND media_transition_target_kind='animation'))",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    @_serialized
    async def finish_animation_transition(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        return await self._set_live_message_kind_if_current_unlocked(
            chat_id, twitch_login, logical_stream_id, message_id, "animation", False
        )

    @_serialized
    async def finish_video_transition(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        return await self._set_live_message_kind_if_current_unlocked(
            chat_id,
            twitch_login,
            logical_stream_id,
            message_id,
            "video",
            False,
        )

    @_serialized
    async def clear_video_transition(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET media_transition_pending = 0 "
            "WHERE chat_id = ? AND twitch_login = ? AND last_stream_id = ? "
            "AND last_message_id = ?",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def _set_live_message_kind_if_current_unlocked(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
        message_kind: str,
        media_transition_pending: bool,
    ) -> bool:
        if message_kind not in {"text", "photo", "video", "animation"}:
            raise ValueError(f"Unsupported live message kind: {message_kind!r}")
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET last_message_kind = ?, "
            "media_transition_pending = ? "
            "WHERE chat_id = ? AND twitch_login = ? AND last_stream_id = ? "
            "AND last_message_id = ?",
            (
                message_kind,
                int(media_transition_pending),
                chat_id,
                twitch_login,
                logical_stream_id,
                message_id,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    @_serialized
    async def set_live_message_kind_if_current(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
        message_kind: str,
        media_transition_pending: bool,
    ) -> bool:
        return await self._set_live_message_kind_if_current_unlocked(
            chat_id,
            twitch_login,
            logical_stream_id,
            message_id,
            message_kind,
            media_transition_pending,
        )

    @_serialized
    async def clear_live_message_if_current(
        self,
        chat_id: int,
        twitch_login: str,
        logical_stream_id: str,
        message_id: int,
    ) -> bool:
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET last_message_id = NULL, "
            "last_message_kind = 'text', media_transition_pending = 0, "
            "live_post_ended = 0 "
            "WHERE chat_id = ? AND twitch_login = ? AND last_stream_id = ? "
            "AND last_message_id = ?",
            (chat_id, twitch_login, logical_stream_id, message_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

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
        message_kind: str = "text",
        queued_go_live: bool = False,
        preserve_live_message: bool = False,
        broadcaster_id: str | None = None,
    ) -> None:
        # при старте нового стрима (is_live=True и меняется stream_id) обнуляем накопленную
        # сумму зрителей — CASE проверяет, отличается ли stream_id от того, что уже в базе.
        # peak_viewers обновляется, только если явно передан (не None) — иначе, при вызове
        # без этого параметра на каждой итерации опроса, он бы затирался в NULL прямо перед
        # тем, как record_viewer_sample успевает честно накопить в нём максимум за стрим.
        cursor = await self.conn.execute(
            "UPDATE tracked_channels SET is_live = ?, last_stream_id = ?, "
            "last_message_kind = CASE "
            "    WHEN ? AND last_message_id IS NOT NULL THEN last_message_kind "
            "    WHEN ? IS NULL THEN 'text' "
            "    WHEN last_message_id = ? THEN last_message_kind "
            "    ELSE 'text' END, "
            "media_transition_pending = CASE "
            "    WHEN ? AND last_message_id IS NOT NULL THEN media_transition_pending "
            "    WHEN ? IS NULL THEN 0 "
            "    WHEN last_message_id = ? THEN media_transition_pending "
            "    ELSE 0 END, "
            "last_message_id = CASE WHEN ? AND last_message_id IS NOT NULL "
            "THEN last_message_id ELSE ? END, "
            "last_title = ?, offline_since = ?, "
            "stream_started_at = ?, "
            "last_broadcaster_id = ?, "
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
            "    THEN NULL ELSE followers_at_start END, "
            "live_post_ended = CASE WHEN ? THEN 0 ELSE live_post_ended END "
            "WHERE chat_id = ? AND twitch_login = ?",
            (
                int(is_live), stream_id,
                int(preserve_live_message), message_id, message_id,
                int(preserve_live_message), message_id, message_id,
                int(preserve_live_message), message_id,
                title, offline_since,
                stream_started_at,
                broadcaster_id,
                last_seen_live_at, last_seen_live_at, int(is_live), stream_id,
                peak_viewers, peak_viewers, int(is_live), stream_id,
                int(is_live),
                int(is_live), stream_id, int(is_live), stream_id,
                int(is_live), stream_id,
                int(is_live),
                chat_id, twitch_login,
            ),
        )
        if queued_go_live and cursor.rowcount > 0:
            if not is_live or stream_id is None or message_id is not None:
                raise ValueError("queued go-live requires live state without a message")
            queued_at = last_seen_live_at if last_seen_live_at is not None else time.time()
            await self.conn.execute(
                "INSERT INTO notification_jobs "
                "(kind, chat_id, twitch_login, logical_stream_id, payload_version, "
                "due_at, status, attempt_count, lease_until, created_at, updated_at) "
                "VALUES ('go_live', ?, ?, ?, 1, ?, 'pending', 0, NULL, ?, ?) "
                "ON CONFLICT (kind, chat_id, twitch_login, logical_stream_id, payload_version) "
                "DO NOTHING",
                (chat_id, twitch_login, stream_id, queued_at, queued_at, queued_at),
            )
        await self.conn.commit()

    async def snapshot_queued_live_starts(self) -> set[tuple[int, str, str]]:
        cursor = await self.conn.execute(
            "SELECT chat_id, twitch_login, logical_stream_id FROM notification_jobs "
            "INDEXED BY idx_notification_jobs_active_go_live "
            "WHERE kind = 'go_live' AND payload_version = 1 "
            "AND status IN ('pending', 'leased', 'failed')"
        )
        return set(await cursor.fetchall())

    async def is_restored_viewer_session(self, user_id: int, login: str, stream_id: str) -> bool:
        cursor = await self.conn.execute(
            'SELECT 1 FROM viewer_restored_sessions WHERE telegram_user_id=? AND twitch_login=? AND stream_id=?',
            (user_id, login, stream_id),
        )
        return await cursor.fetchone() is not None

    @_serialized
    async def set_live_message_if_current(
        self, chat_id: int, twitch_login: str, stream_id: str, message_id: int
    ) -> bool:
        try:
            cursor = await self.conn.execute(
                "UPDATE tracked_channels SET last_message_id = ?, last_message_kind = 'text' "
                "WHERE chat_id = ? AND twitch_login = ? AND last_stream_id = ? "
                "AND is_live = 1 AND notify_enabled = 1 AND last_message_id IS NULL",
                (message_id, chat_id, twitch_login, stream_id),
            )
            if cursor.rowcount == 1:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO streamer_post_events "
                    "(broadcaster_id, chat_id, twitch_login, stream_id, message_id, published_at) "
                    "SELECT tc.last_broadcaster_id, tc.chat_id, tc.twitch_login, "
                    "tc.last_stream_id, ?, ? FROM tracked_channels tc "
                    "JOIN streamer_identities i ON i.broadcaster_id = tc.last_broadcaster_id "
                    "AND i.twitch_login = tc.twitch_login "
                    "WHERE tc.chat_id = ? AND tc.twitch_login = ? "
                    "AND tc.last_stream_id = ?",
                    (message_id, time.time(), chat_id, twitch_login, stream_id),
                )
            await self.conn.commit()
            return cursor.rowcount == 1
        except Exception:
            await self.conn.rollback()
            raise

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
        """Live-посты офлайн-каналов, ожидающие удаления или финального редактирования.

        Для публичных чатов пост удаляется, для лички переводится в ended-карточку.
        ``stats_sent`` возвращается для выбора между сохранением ссылки до отчёта
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
            "ORDER BY updated_at ASC"
        )
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await self.conn.execute(sql, params)
        return [_report_delivery_from_row(tuple(row)) for row in await cursor.fetchall()]

    @_serialized
    async def defer_report_delivery_retry(
        self, delivery: ReportDelivery, updated_at: float
    ) -> None:
        """Сдвигает временно недоступную delivery в конец retry-очереди.

        Без этого пять старейших адресатов с постоянной сетевой ошибкой навсегда
        перекрывали все более новые отчёты из-за LIMIT в poller.
        """
        await self.conn.execute(
            "UPDATE report_deliveries SET updated_at = ? "
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
        logger.warning(
            "Automatic report delivery стала terminal: login=%s, reason=%s",
            delivery.twitch_login,
            reason,
        )

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
            "UPDATE tracked_channels SET last_message_id = NULL, last_message_kind = 'text', "
            "media_transition_pending = 0, live_post_ended = 0 "
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
            "peak_viewers = NULL, last_message_id = NULL, last_message_kind = 'text', "
            "media_transition_pending = 0, "
            "live_post_ended = 0, "
            "stats_sent = 0, viewer_sum = 0, viewer_samples = 0, "
            "followers_at_start = NULL "
            "WHERE chat_id = ? AND twitch_login = ? "
            "AND is_live = 0 AND stats_sent = 1 "
            "AND (last_message_id IS NULL OR live_post_ended = 1)",
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
            "peak_viewers = NULL, last_message_id = NULL, last_message_kind = 'text', "
            "media_transition_pending = 0, "
            "live_post_ended = 0, "
            "stats_sent = 0, viewer_sum = 0, viewer_samples = 0, "
            "followers_at_start = NULL "
            "WHERE is_live = 0 AND stats_sent = 1 "
            "AND (last_message_id IS NULL OR live_post_ended = 1)"
        )
        await self.conn.commit()

    @_serialized
    async def clear_message(self, chat_id: int, twitch_login: str) -> None:
        await self.conn.execute(
            "UPDATE tracked_channels SET last_message_id = NULL, "
            "last_message_kind = 'text', media_transition_pending = 0, last_title = NULL, "
            "live_post_ended = 0, "
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

    @_serialized
    async def record_stream_observation(
        self,
        twitch_login: str,
        stream_id: str,
        sampled_at: float,
        viewer_count: int,
        title: str,
        game_name: str,
        chat_ids: list[int],
    ) -> None:
        """Persist one poll sample for every processed destination of a logical stream.

        An already-active legacy stream stays legacy for all destinations, including
        destinations added midstream. Memberships preserve skipped-poll gaps exactly.
        """
        recipients = list(dict.fromkeys(chat_ids))
        if not recipients:
            return
        cursor = await self.conn.execute(
            "SELECT 1 FROM stream_samples WHERE twitch_login = ? AND stream_id = ? LIMIT 1",
            (twitch_login, stream_id),
        )
        if await cursor.fetchone() is not None:
            await self.conn.executemany(
                "INSERT INTO stream_samples "
                "(chat_id, twitch_login, stream_id, sampled_at, viewer_count, title, game_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(chat_id, twitch_login, stream_id, sampled_at, viewer_count, title, game_name)
                 for chat_id in recipients],
            )
        else:
            await self.conn.execute(
                "INSERT OR IGNORE INTO stream_observations "
                "(twitch_login, stream_id, sampled_at, viewer_count, title, game_name) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (twitch_login, stream_id, sampled_at, viewer_count, title, game_name),
            )
            await self.conn.executemany(
                "INSERT OR IGNORE INTO stream_observation_memberships "
                "(chat_id, twitch_login, stream_id, sampled_at) VALUES (?, ?, ?, ?)",
                [(chat_id, twitch_login, stream_id, sampled_at) for chat_id in recipients],
            )
        await self.conn.commit()

    async def get_stream_samples(
        self, chat_id: int, twitch_login: str, stream_id: str
    ) -> list[tuple[float, int, str, str]]:
        """(sampled_at, viewer_count, title, game_name) по возрастанию времени."""
        mode = await self.conn.execute(
            "SELECT 1 FROM stream_samples WHERE twitch_login = ? AND stream_id = ? LIMIT 1",
            (twitch_login, stream_id),
        )
        if await mode.fetchone() is not None:
            cursor = await self.conn.execute(
                "SELECT sampled_at, viewer_count, title, game_name FROM stream_samples "
                "WHERE chat_id = ? AND twitch_login = ? AND stream_id = ? "
                "ORDER BY sampled_at ASC",
                (chat_id, twitch_login, stream_id),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT o.sampled_at, o.viewer_count, o.title, o.game_name "
                "FROM stream_observation_memberships m "
                "JOIN stream_observations o USING (twitch_login, stream_id, sampled_at) "
                "WHERE m.chat_id = ? AND m.twitch_login = ? AND m.stream_id = ? "
                "ORDER BY m.sampled_at ASC",
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
        """Удаляет raw-данные завершённых сессий после retention от их окончания.

        Ранний sample длинного стрима может быть намного старше ``older_than_ts``,
        хотя сам стрим закончился недавно. Поэтому active/reconnect/pending session
        и вся сессия со свежей history защищены целиком. Свёрнутая ``stream_history``
        не трогается — она хранится всегда.
        """
        await self.conn.execute(
            "DELETE FROM stream_observation_memberships AS raw WHERE raw.sampled_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = raw.chat_id AND tc.twitch_login = raw.twitch_login "
            "AND tc.last_stream_id = raw.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0)) "
            "AND NOT EXISTS (SELECT 1 FROM stream_history h "
            "WHERE h.chat_id = raw.chat_id AND h.twitch_login = raw.twitch_login "
            "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
            (older_than_ts, older_than_ts),
        )
        await self.conn.execute(
            "DELETE FROM stream_observations AS raw WHERE raw.sampled_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM stream_observation_memberships m "
            "WHERE m.twitch_login = raw.twitch_login AND m.stream_id = raw.stream_id "
            "AND m.sampled_at = raw.sampled_at)",
            (older_than_ts,),
        )
        await self.conn.execute(
            "DELETE FROM stream_samples AS raw WHERE raw.sampled_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = raw.chat_id AND tc.twitch_login = raw.twitch_login "
            "AND tc.last_stream_id = raw.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0)) "
            "AND NOT EXISTS (SELECT 1 FROM stream_history h "
            "WHERE h.chat_id = raw.chat_id AND h.twitch_login = raw.twitch_login "
            "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
            (older_than_ts, older_than_ts),
        )
        await self.conn.execute(
            "DELETE FROM chat_activity_samples AS raw WHERE raw.minute_ts < ? "
            "AND NOT EXISTS (SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = raw.chat_id AND tc.twitch_login = raw.twitch_login "
            "AND tc.last_stream_id = raw.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0)) "
            "AND NOT EXISTS (SELECT 1 FROM stream_history h "
            "WHERE h.chat_id = raw.chat_id AND h.twitch_login = raw.twitch_login "
            "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
            (older_than_ts, older_than_ts),
        )
        await self.conn.execute(
            "DELETE FROM chat_unique_nicks AS raw WHERE raw.joined_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM tracked_channels tc "
            "WHERE tc.chat_id = raw.chat_id AND tc.twitch_login = raw.twitch_login "
            "AND tc.last_stream_id = raw.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0)) "
            "AND NOT EXISTS (SELECT 1 FROM stream_history h "
            "WHERE h.chat_id = raw.chat_id AND h.twitch_login = raw.twitch_login "
            "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
            (older_than_ts, older_than_ts),
        )
        await self.conn.execute(
            "DELETE FROM stream_chatters AS raw WHERE raw.first_seen_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM tracked_channels tc "
            "WHERE tc.twitch_login = raw.twitch_login "
            "AND tc.last_stream_id = raw.stream_id "
            "AND (tc.is_live = 1 OR tc.stats_sent = 0)) "
            "AND NOT EXISTS (SELECT 1 FROM stream_history h "
            "WHERE h.twitch_login = raw.twitch_login "
            "AND h.stream_id = raw.stream_id AND h.ended_at >= ?)",
            (older_than_ts, older_than_ts),
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
            "DELETE FROM notification_jobs WHERE status IN ('done', 'failed') "
            "AND updated_at < ?",
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

    async def get_admin_live_streams(self, limit: int = 20) -> list[dict]:
        """Unique live Twitch channels; no chat IDs in the owner projection."""
        cursor = await self.conn.execute(
            "SELECT twitch_login, destinations, observed_at, viewer_count FROM ("
            " SELECT tc.twitch_login, "
            " COUNT(*) OVER (PARTITION BY tc.twitch_login) AS destinations, "
            " MAX(tc.last_seen_live_at) OVER (PARTITION BY tc.twitch_login) AS observed_at, "
            " COALESCE(ss.viewer_count, obs.viewer_count) AS viewer_count, "
            " ROW_NUMBER() OVER (PARTITION BY tc.twitch_login "
            " ORDER BY COALESCE(ss.sampled_at, obs.sampled_at) DESC, tc.chat_id) AS rn "
            " FROM tracked_channels tc "
            " LEFT JOIN stream_samples ss ON ss.rowid = ("
            "  SELECT sample.rowid FROM stream_samples sample "
            "  WHERE sample.chat_id = tc.chat_id "
            "  AND sample.twitch_login = tc.twitch_login "
            "  AND sample.stream_id = tc.last_stream_id "
            "  ORDER BY sample.sampled_at DESC LIMIT 1) "
            " LEFT JOIN stream_observation_memberships m ON "
            " m.chat_id = tc.chat_id AND m.twitch_login = tc.twitch_login "
            " AND m.stream_id = tc.last_stream_id AND m.sampled_at = ("
            "  SELECT MAX(membership.sampled_at) FROM stream_observation_memberships membership "
            "  WHERE membership.chat_id = tc.chat_id "
            "  AND membership.twitch_login = tc.twitch_login "
            "  AND membership.stream_id = tc.last_stream_id) "
            " LEFT JOIN stream_observations obs ON obs.twitch_login = m.twitch_login "
            " AND obs.stream_id = m.stream_id AND obs.sampled_at = m.sampled_at "
            " WHERE tc.is_live = 1) "
            "WHERE rn = 1 ORDER BY observed_at DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        )
        return [
            {"login": row[0], "destinations": row[1], "viewers": row[3], "observed_at": row[2]}
            for row in await cursor.fetchall()
        ]

    async def health_snapshot(self, now: float | None = None) -> dict[str, int | float | None]:
        """Дешёвая read-only диагностика очередей и SQLite storage.

        Здесь намеренно нет integrity check, checkpoint или обхода history/raw
        таблиц. Outbox и deferred агрегируются по покрывающим индексам, токены лишь
        считаются без чтения и расшифровки значений.
        """
        snapshot_at = time.time() if now is None else now
        pending_cursor = await self.conn.execute(
            "SELECT COUNT(*), MIN(created_at) FROM report_deliveries "
            "WHERE terminal_failed = 0 AND (text_sent = 0 OR "
            "(report_format = 'full' AND html_sent = 0))"
        )
        pending_count, oldest_pending_at = await pending_cursor.fetchone()

        deferred_cursor = await self.conn.execute(
            "SELECT COUNT(*), MIN(ended_at) FROM deferred_reports"
        )
        deferred_count, oldest_deferred_at = await deferred_cursor.fetchone()

        from .notification_queue import NotificationQueue
        notification_jobs = await NotificationQueue(self).depth_snapshot(snapshot_at)

        token_cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM twitch_user_tokens"
        )
        stored_user_tokens = (await token_cursor.fetchone())[0]

        pragma_values: dict[str, int | None] = {}
        for pragma in ("page_count", "freelist_count", "page_size"):
            try:
                cursor = await self.conn.execute(f"PRAGMA {pragma}")
                row = await cursor.fetchone()
                pragma_values[pragma] = int(row[0]) if row is not None else None
            except Exception:
                pragma_values[pragma] = None

        # PRAGMA database_list сообщает реальный путь и для относительной DB path,
        # при этом :memory:/URI memory возвращают пустую строку. Сам путь наружу не
        # отдаём, чтобы /health не раскрывал структуру filesystem.
        database_path = ""
        try:
            cursor = await self.conn.execute("PRAGMA database_list")
            for _sequence, name, path in await cursor.fetchall():
                if name == "main":
                    database_path = path or ""
                    break
        except Exception:
            pass

        db_file_bytes = self._safe_file_size(database_path, missing=0)
        wal_file_bytes = self._safe_file_size(
            f"{database_path}-wal" if database_path else "",
            missing=0,
        )
        return {
            **notification_jobs,
            "pending_deliveries": int(pending_count),
            "oldest_pending_age_seconds": (
                max(0.0, snapshot_at - float(oldest_pending_at))
                if oldest_pending_at is not None
                else None
            ),
            "deferred_reports": int(deferred_count),
            "oldest_deferred_age_seconds": (
                max(0.0, snapshot_at - float(oldest_deferred_at))
                if oldest_deferred_at is not None
                else None
            ),
            "stored_user_tokens": int(stored_user_tokens),
            "db_file_bytes": db_file_bytes,
            "wal_file_bytes": wal_file_bytes,
            "page_count": pragma_values["page_count"],
            "freelist_count": pragma_values["freelist_count"],
            "page_size": pragma_values["page_size"],
        }

    @staticmethod
    def _safe_file_size(path: str, *, missing: int) -> int | None:
        if not path:
            return missing
        try:
            return os.path.getsize(path)
        except FileNotFoundError:
            return missing
        except OSError:
            # Permission/race/mount errors не должны ломать owner health endpoint.
            return None
