"""Локальный фиктивный сервер панели владельца — только для визуальной проверки.

Отдаёт статику панели и поддельный снапшот. Никаких реальных данных, секретов,
сети наружу и авторизации: сервер слушает только 127.0.0.1 и предназначен для
снимков интерфейса при разработке.

Режимы `--delay` и `--fail` показывают состояния загрузки и ошибки: они годятся
для ручной проверки в обычном браузере. Headless Chrome на медленном ответе не
завершается, поэтому автоматические снимки делаются только в штатном режиме.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import time

from aiohttp import web

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "bot" / "admin_ui"

_NOW = int(time.time())
_DAY = 86400
_ACTIVITY = [
    {"date": _NOW - _DAY * offset, "users": users}
    for offset, users in ((6, 1), (5, 0), (4, 2), (3, 1), (2, 0), (1, 3), (0, 3))
]

SNAPSHOT = {
    "environment": "staging",
    "csrf": "local-fixture-csrf",
    "generated_at": _NOW,
    "audience": {"private_users": 67},
    "access": {
        "active_total": 2,
        "by_source": {"manual": 1, "paid": 1},
        "active_rows": [
            {
                "person_id": 101, "display_name": "Алекс", "username": "alex_live",
                "plan": "viewer_plus", "source": "manual", "expires_at": _NOW + _DAY * 3,
            },
            {
                "person_id": 104, "display_name": "Ирина Сергеевна", "username": "ira_stream",
                "plan": "streamer_plus", "source": "paid", "expires_at": _NOW + _DAY * 40,
            },
        ],
        "history": [
            {"actor_telegram_id": None, "happened_at": _NOW - _DAY, "action": "grant", "plan": "viewer_plus"},
            {"actor_telegram_id": None, "happened_at": _NOW - _DAY * 2, "action": "revoke", "plan": "viewer_plus"},
        ],
    },
    "deliveries": {"total": 4},
    "activity": {"active_today": 3, "new_7d": 1, "by_day": _ACTIVITY},
    "telegram": {"state": "ok"},
    "twitch": {"state": "degraded", "auth_blocked_names": ["drakeoffc"]},
    "preview": {"state": "ok"},
    "attention": [
        {"kind": "twitch", "title": "Один канал требует авторизации", "detail": "Напоминания об эфирах не уходят", "severity": "warn"},
        {"kind": "backup", "title": "Копия не проверена", "detail": "Восстановление не запускалось", "severity": "warn"},
    ],
    "live": [],
    "growth": [],
    "system": {},
    "errors": {},
    "queues": {"pending_jobs": 0, "leased_jobs": 0, "due_jobs": 2, "failed_jobs": 1, "oldest_due_age_seconds": 420},
    "backup": {
        "last_backup_at": _NOW - 36000, "last_backup_name": "auto-20261005T030000Z.db",
        "copies": 5, "retention": 5, "restore_verified": False,
    },
    "resources": {
        "process_cpu_percent": 3.4, "process_ram_bytes": 251658240,
        "db_volume_free_bytes": 454299648, "db_file_bytes": 13180928, "wal_file_bytes": 4120032,
    },
}

# Выдуманные люди: локальная фикстура, реальные данные сюда не попадают.
PEOPLE = [
    {
        "user_id": 101, "display_name": "Алекс", "username": "alex_live",
        "plan": "viewer_plus", "expires_at": _NOW + _DAY * 30, "last_active_at": _NOW - 3600,
        "twitch_login": "alex_live",
        "grants": [{"plan": "viewer_plus", "source": "manual", "expires_at": _NOW + _DAY * 30}],
        "limits": {"channels": {"used": 38, "limit": 200}, "video": {"used": 5, "limit": 5}},
    },
    {
        "user_id": 102, "display_name": "Мария", "username": "m_ree",
        "plan": "free", "expires_at": None, "last_active_at": _NOW - 7200,
        "twitch_login": None,
        "grants": [],
        "limits": {"channels": {"used": 12, "limit": 50}, "video": {"used": None, "limit": None}},
    },
    {
        "user_id": 103, "display_name": "Дмитрий", "username": None,
        "plan": "free", "expires_at": None, "last_active_at": _NOW - _DAY * 3,
        "twitch_login": None,
        "grants": [],
        "limits": {"channels": {"used": 0, "limit": 50}, "video": {"used": None, "limit": None}},
    },
    {
        "user_id": 104, "display_name": "Ирина Сергеевна", "username": "ira_stream",
        "plan": "streamer_plus", "expires_at": _NOW + _DAY * 14, "last_active_at": _NOW - _DAY,
        "twitch_login": "ira_stream",
        "grants": [{"plan": "streamer_plus", "source": "paid", "expires_at": _NOW + _DAY * 14}],
        "limits": {"channels": {"used": 204, "limit": 200}, "video": {"used": 5, "limit": 5}},
    },
]


async def index(_request: web.Request) -> web.Response:
    return web.FileResponse(UI / "index.html")


async def snapshot(_request: web.Request) -> web.Response:
    return web.json_response(SNAPSHOT)


async def people(_request: web.Request) -> web.Response:
    return web.json_response({"people": PEOPLE})

async def person(request: web.Request) -> web.Response:
    wanted = str(request.match_info["user_id"])
    card = next((item for item in PEOPLE if str(item["user_id"]) == wanted), None)
    if card is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(card)


async def person_history(_request: web.Request) -> web.Response:
    return web.json_response({"events": []})


def build_app(*, delay: float = 0.0, fail: bool = False) -> web.Application:
    async def snapshot_route(_request: web.Request) -> web.Response:
        if fail:
            return web.json_response({"error": "fixture_failure"}, status=500)
        if delay:
            await asyncio.sleep(delay)
        return web.json_response(SNAPSHOT)

    app = web.Application()
    app.router.add_get("/admin", index)
    app.router.add_get("/admin/panel.css", lambda _r: web.FileResponse(UI / "panel.css"))
    app.router.add_get("/admin/panel.js", lambda _r: web.FileResponse(UI / "panel.js"))
    app.router.add_get("/admin/api/snapshot", snapshot_route)
    app.router.add_get("/admin/api/users", people)
    app.router.add_get("/admin/api/users/{user_id}", person)
    app.router.add_get("/admin/api/users/{user_id}/history", person_history)
    return app


async def main(port: int, *, delay: float = 0.0, fail: bool = False) -> None:
    runner = web.AppRunner(build_app(delay=delay, fail=fail))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"ADMIN_FIXTURE=http://127.0.0.1:{port}/admin", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--delay", type=float, default=0.0, help="задержка снапшота: состояние загрузки")
    parser.add_argument("--fail", action="store_true", help="снапшот отвечает 500: состояние ошибки")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.port, delay=args.delay, fail=args.fail))
    except KeyboardInterrupt:
        pass
