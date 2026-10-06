"""Read-only, source-labeled operational projection for the owner panel."""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import shutil
import tempfile
import time
from collections.abc import Callable

from .audience_metrics import collect_audience, funnel as audience_funnel


_SAFE_ERROR = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}\Z")

# Временные каталоги, куда превью пишет буферы, рендер и разбор кадров.
PREVIEW_TEMP_PATTERNS = ("signalbot-preview", "twitch-signalbot-preview-*")


def _directory_bytes(path: pathlib.Path, *, limit: float = 1.0) -> int:
    """Размер каталога; обход ограничен по времени, чтобы не тормозить панель."""
    total = 0
    started = time.monotonic()
    stack = [path]
    while stack:
        if time.monotonic() - started > limit:
            break
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir() and not entry.is_symlink():
                    stack.append(entry)
                elif entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    return total


def preview_disk_usage() -> dict[str, int] | None:
    """Сколько места занимают временные файлы превью и сколько свободно.

    Владельцу важно видеть это рядом с состоянием подсистемы: превью — самая
    прожорливая часть бота, и упор в место выглядел как «превью сломалось».
    """
    root = pathlib.Path(tempfile.gettempdir())
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return None
    used = 0
    for pattern in PREVIEW_TEMP_PATTERNS:
        try:
            candidates = list(root.glob(pattern))
        except OSError:
            continue
        for candidate in candidates:
            try:
                used += _directory_bytes(candidate)
            except OSError:
                # Сбой обхода не должен ломать снимок панели.
                continue
    result = {
        "free_bytes": int(usage.free),
        "total_bytes": int(usage.total),
        "preview_bytes": int(used),
    }
    # Том с базой — отдельное место: если он переполнится, пострадает не только
    # превью, поэтому владелец должен видеть обе цифры.
    try:
        data = shutil.disk_usage(os.getenv("DB_PATH", "/data/bot.db"))
    except OSError:
        return result
    result["data_free_bytes"] = int(data.free)
    result["data_total_bytes"] = int(data.total)
    return result

# Сколько проблем показываем владельцу на первом экране и с какого возраста
# очереди считаем задержку заметной.
ATTENTION_LIMIT = 3
QUEUE_DELAY_SECONDS = 300
SNAPSHOT_PAGE = 20
# Общий бюджет на пять срезов каталога панели.
DIRECTORY_BUDGET_SECONDS = 2.0
ACTIVITY_BUDGET_SECONDS = 2.0
WEEK_SECONDS = 7 * 86_400
MSK_OFFSET_SECONDS = 3 * 3600


def _msk_midnight(now: float) -> float:
    """Начало суток по Москве: фиксированное смещение +3, как в боте."""
    return ((now + MSK_OFFSET_SECONDS) // 86_400) * 86_400 - MSK_OFFSET_SECONDS


def _attention(queues: dict | None, errors: dict, preview_state: str | None,
               eventsub: dict | None = None, blocked_logins: list[str] | None = None) -> list[dict]:
    """До трёх проблем, отсортированных по влиянию на людей."""
    items: list[dict] = []
    if errors.get("database"):
        items.append({
            "kind": "database",
            "severity": "danger",
            "title": "Часть данных базы недоступна",
            "detail": "Показатели за этот сбор неполные. Повторите обновление.",
        })
    if errors.get("directory"):
        items.append({
            "kind": "directory",
            "severity": "warn",
            "title": "Раздел «Доступы» недоступен",
            "detail": "Права и история за этот сбор не загрузились. Повторите обновление.",
        })
    if errors.get("activity"):
        items.append({
            "kind": "activity",
            "severity": "warn",
            "title": "Показатели активности недоступны",
            "detail": "Число активных сегодня и новых за неделю не собраны.",
        })
    if queues:
        failed = queues.get("failed_jobs")
        if failed:
            items.append({
                "kind": "queue_failed",
                "severity": "danger",
                "title": f"Не отправлено заданий: {failed}",
                "detail": "Очередь уведомлений содержит неудачные задания.",
            })
        due = queues.get("due_jobs")
        age = queues.get("oldest_due_age_seconds")
        if due and age is not None and age > QUEUE_DELAY_SECONDS:
            items.append({
                "kind": "queue_delay",
                "severity": "warn",
                "title": "Уведомления задерживаются",
                "detail": f"В очереди {due}, старейшее ждёт {int(age // 60)} мин.",
            })
    if preview_state == "degraded":
        items.append({
            "kind": "preview",
            "severity": "warn",
            "title": "Видеопревью работает со сбоями",
            "detail": "Последняя сборка превью завершилась ошибкой.",
        })
    for subsystem, label in (("poller", "Опрос Twitch"), ("eventsub", "EventSub")):
        if errors.get(subsystem):
            items.append({
                "kind": subsystem,
                "severity": "warn",
                "title": f"{label}: ошибка",
                "detail": _subsystem_detail(subsystem, eventsub or {}, blocked_logins or []),
            })
    return items[:ATTENTION_LIMIT]


def _subsystem_detail(subsystem: str, eventsub: dict, blocked_logins: list[str]) -> str:
    """Конкретика вместо «что-то сломалось»: сколько каналов и кого просить."""
    parts: list[str] = []
    if subsystem == "eventsub":
        ready, configured = eventsub.get("ready_logins"), eventsub.get("configured_logins")
        if isinstance(ready, int) and isinstance(configured, int) and configured:
            parts.append(f"Подписано {ready} из {configured} каналов.")
    safe = [name for name in blocked_logins if isinstance(name, str) and name]
    if safe:
        parts.append("Нужна повторная авторизация Twitch: " + ", ".join(safe) + ".")
    parts.append("Повторится на следующем цикле, если причина не исчезнет.")
    return " ".join(parts)


def _error_class(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ERROR.fullmatch(value) else None


def _resources(db_path: str, previous: tuple[float, float] | None) -> tuple[dict, tuple[float, float]]:
    wall, cpu = time.monotonic(), time.process_time()
    cpu_percent = None
    if previous is not None and wall > previous[0]:
        cpu_percent = round(max(0.0, (cpu - previous[1]) / (wall - previous[0])) * 100, 1)
    ram_bytes = None
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        ram_bytes = resident_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    disk_free = disk_total = None
    if db_path != ":memory:":
        try:
            disk = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)))
            disk_free, disk_total = disk.free, disk.total
        except OSError:
            pass
    return {
        "process_cpu_percent": cpu_percent,
        "process_ram_bytes": ram_bytes,
        "db_volume_free_bytes": disk_free,
        "db_volume_total_bytes": disk_total,
    }, (wall, cpu)


class AdminSnapshot:
    def __init__(
        self,
        db,
        poller,
        follow_listener,
        token_store,
        preview_manager,
        *,
        db_path: str,
        telegram_polling_provider: Callable[[], bool | None],
        environment: str,
        directory=None,
    ) -> None:
        self._db = db
        self._poller = poller
        self._eventsub = follow_listener
        self._tokens = token_store
        self._preview = preview_manager
        self._db_path = db_path
        self._telegram_polling_provider = telegram_polling_provider
        self._environment = environment
        self._directory = directory
        self._previous_cpu: tuple[float, float] | None = None

    async def collect(self) -> dict:
        now = time.time()
        try:
            poller = self._poller.health_snapshot(now)
        except Exception:
            poller = {}
        try:
            eventsub = self._eventsub.health_snapshot(now)
        except Exception:
            eventsub = {}
        try:
            tokens = self._tokens.health_snapshot()
        except Exception:
            tokens = None
        # Логины берём отдельным методом: общий health-контракт токенов остаётся
        # агрегированным, а конкретику видит только панель владельца.
        blocked_logins: list[str] = []
        try:
            provider = getattr(self._tokens, "blocked_logins", None)
            if callable(provider):
                blocked_logins = [name for name in provider() if isinstance(name, str) and name][:5]
        except Exception:
            blocked_logins = []
        try:
            # PreviewManager uses a monotonic clock; the wall-clock timestamp
            # used by poller/EventSub would turn a recent capture into decades.
            preview = self._preview.health_snapshot() if self._preview is not None else None
        except Exception:
            preview = None
        try:
            polling = self._telegram_polling_provider()
        except Exception:
            polling = None

        success_age = poller.get("last_successful_cycle_age_seconds")
        stale_after = poller.get("stale_after_seconds")
        if not poller or success_age is None or stale_after is None or tokens is None:
            twitch_state = "unknown"
        elif (
            not poller.get("running") or poller.get("stopping")
            or success_age > stale_after or poller.get("last_cycle_error")
            or not eventsub.get("running")
            or (eventsub.get("ready_logins") or 0) < (eventsub.get("configured_logins") or 0)
            or (tokens.get("auth_blocked_logins") or 0) > 0
        ):
            twitch_state = "degraded"
        else:
            twitch_state = "ok"

        if preview is None:
            preview_state = "unknown"
        elif not preview.get("enabled"):
            preview_state = "disabled"
        elif not preview.get("manager_running") or preview.get("last_error"):
            preview_state = "degraded"
        elif preview.get("last_success_age_seconds") is None:
            preview_state = "unknown"
        else:
            preview_state = "ok"

        audience = live = queues = growth = None
        people = None
        database_failed = False
        try:
            audience = await asyncio.wait_for(self._db.get_bot_stats(), 2.0)
        except Exception:
            database_failed = True
        try:
            # Сколько людей приходит, возвращается и где останавливается.
            collected = await asyncio.wait_for(collect_audience(self._db), 2.0)
            people = {**collected.as_dict(), "funnel": list(audience_funnel(collected))}
        except Exception:
            people = None
        try:
            live = await asyncio.wait_for(self._db.get_admin_live_streams(), 2.0)
        except Exception:
            database_failed = True
        try:
            queues = await asyncio.wait_for(self._db.health_snapshot(now), 2.0)
        except Exception:
            database_failed = True
        try:
            growth = await asyncio.wait_for(self._db.growth_funnel_snapshot(), 2.0)
        except Exception:
            database_failed = True
        try:
            funnel = await asyncio.wait_for(self._db.growth_funnel_report(now=now), 2.0)
        except Exception:
            funnel = None
            database_failed = True

        resources, self._previous_cpu = _resources(self._db_path, self._previous_cpu)
        if queues is not None:
            resources["db_file_bytes"] = queues.get("db_file_bytes")
            resources["wal_file_bytes"] = queues.get("wal_file_bytes")
        else:
            resources["db_file_bytes"] = None
            resources["wal_file_bytes"] = None

        access = rows = history = backup = deliveries = None
        directory_failed = False
        if self._directory is not None:
            # Пять срезов идут параллельно под общим бюджетом: последовательные
            # таймауты по 2 с складывались бы и не оставляли времени на ответ.
            try:
                gathered = await asyncio.wait_for(asyncio.gather(
                    self._directory.access_overview(now),
                    self._directory.active_grants(now, SNAPSHOT_PAGE, 0),
                    self._directory.history(SNAPSHOT_PAGE, 0),
                    self._directory.backup_status(),
                    self._directory.deliveries_24h(now),
                    return_exceptions=True,
                ), DIRECTORY_BUDGET_SECONDS)
                directory_failed = any(
                    isinstance(value, BaseException) for value in gathered
                )
                access, rows, history, backup, deliveries = [
                    None if isinstance(value, BaseException) else value for value in gathered
                ]
            except Exception:
                directory_failed = True
        if access is not None:
            access["active_rows"] = rows
            access["history"] = history

        activity = None
        activity_failed = False
        if callable(getattr(self._db, "count_active_since", None)):
            try:
                active_today, new_7d, by_day = await asyncio.wait_for(asyncio.gather(
                    self._db.count_active_since(_msk_midnight(now)),
                    self._db.count_first_seen_since(now - WEEK_SECONDS),
                    self._db.activity_by_day(days=7, now=now),
                ), ACTIVITY_BUDGET_SECONDS)
                activity = {"active_today": active_today, "new_7d": new_7d, "by_day": by_day}
            except Exception:
                activity_failed = True

        errors = {
            "poller": _error_class(poller.get("last_cycle_error")),
            "eventsub": _error_class(eventsub.get("last_error")),
            "preview": _error_class(preview.get("last_error")) if preview else None,
            "database": "unavailable" if database_failed else None,
            "directory": "unavailable" if directory_failed else None,
            "activity": "unavailable" if activity_failed else None,
        }

        return {
            "generated_at": now,
            "environment": self._environment,
            "telegram": {
                "state": "unknown" if polling is None else ("ok" if polling else "degraded"),
                "polling_running": polling,
                "delivery_verified": "unknown",
            },
            "twitch": {
                "state": twitch_state,
                "last_success_age_seconds": success_age,
                "last_cycle_duration_seconds": poller.get("last_cycle_duration_seconds"),
                "eventsub_running": eventsub.get("running"),
                "eventsub_ready": eventsub.get("ready_logins"),
                "eventsub_configured": eventsub.get("configured_logins"),
                "auth_blocked_logins": tokens.get("auth_blocked_logins") if tokens else None,
                "auth_blocked_names": blocked_logins or None,
            },
            "preview": {
                "state": preview_state,
                "active_sessions": preview.get("active_sessions") if preview else None,
                "deferred_sessions": preview.get("deferred_sessions") if preview else None,
                "max_active_sessions": preview.get("max_active_sessions") if preview else None,
                "active_jobs": preview.get("active_jobs") if preview else None,
                "max_concurrent_jobs": preview.get("max_concurrent_jobs") if preview else None,
                "consecutive_provider_failures": (
                    preview.get("consecutive_provider_failures") if preview else None),
                "latest_observation_age_seconds": preview.get("latest_observation_age_seconds") if preview else None,
                "last_success_age_seconds": preview.get("last_success_age_seconds") if preview else None,
                "disabled_reason": _error_class(preview.get("disabled_reason")) if preview else None,
                "disk": preview_disk_usage(),
            },
            "audience": audience,
            "people": people,
            "live": live,
            "queues": queues,
            "growth": growth,
            "funnel": funnel,
            "access": access,
            "backup": backup,
            "deliveries": deliveries,
            "activity": activity,
            "attention": _attention(queues, errors, preview_state, eventsub, blocked_logins),
            "errors": errors,
            "resources": resources,
        }
