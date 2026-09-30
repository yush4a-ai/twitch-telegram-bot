"""Read-only, source-labeled operational projection for the owner panel."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from collections.abc import Callable


_SAFE_ERROR = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,79}\Z")


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
    ) -> None:
        self._db = db
        self._poller = poller
        self._eventsub = follow_listener
        self._tokens = token_store
        self._preview = preview_manager
        self._db_path = db_path
        self._telegram_polling_provider = telegram_polling_provider
        self._environment = environment
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
        try:
            preview = self._preview.health_snapshot(now) if self._preview is not None else None
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

        audience = live = queues = None
        database_failed = False
        try:
            audience = await asyncio.wait_for(self._db.get_bot_stats(), 2.0)
        except Exception:
            database_failed = True
        try:
            live = await asyncio.wait_for(self._db.get_admin_live_streams(), 2.0)
        except Exception:
            database_failed = True
        try:
            queues = await asyncio.wait_for(self._db.health_snapshot(now), 2.0)
        except Exception:
            database_failed = True

        resources, self._previous_cpu = _resources(self._db_path, self._previous_cpu)
        if queues is not None:
            resources["db_file_bytes"] = queues.get("db_file_bytes")
            resources["wal_file_bytes"] = queues.get("wal_file_bytes")
        else:
            resources["db_file_bytes"] = None
            resources["wal_file_bytes"] = None

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
            },
            "preview": {
                "state": preview_state,
                "active_sessions": preview.get("active_sessions") if preview else None,
                "active_jobs": preview.get("active_jobs") if preview else None,
                "latest_observation_age_seconds": preview.get("latest_observation_age_seconds") if preview else None,
                "last_success_age_seconds": preview.get("last_success_age_seconds") if preview else None,
                "disabled_reason": _error_class(preview.get("disabled_reason")) if preview else None,
            },
            "audience": audience,
            "live": live,
            "queues": queues,
            "errors": {
                "poller": _error_class(poller.get("last_cycle_error")),
                "eventsub": _error_class(eventsub.get("last_error")),
                "preview": _error_class(preview.get("last_error")) if preview else None,
                "database": "unavailable" if database_failed else None,
            },
            "resources": resources,
        }
