"""Local-only synthetic dashboard for repeatable browser checks."""

import asyncio
import signal
import time
from types import SimpleNamespace

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer


FIXTURE_KEY = "local-browser-fixture-key-32-chars-minimum"


async def main() -> None:
    server = OAuthCallbackServer(
        "http://127.0.0.1/twitch/callback", "127.0.0.1", 0,
        admin_access=AdminAccess(FIXTURE_KEY, enabled=True, secure_cookie=False, owner_id=425785231),
    )

    async def snapshot() -> dict:
        now = time.time()
        return {
            "generated_at": now, "environment": "local",
            "telegram": {"state": "ok", "polling_running": True, "delivery_verified": "unknown"},
            "twitch": {"state": "degraded", "last_success_age_seconds": 64, "eventsub_ready": 2, "eventsub_configured": 3, "auth_blocked_logins": 1},
            "preview": {"state": "disabled", "active_sessions": 0, "active_jobs": 0, "last_success_age_seconds": None, "disabled_reason": "config_error"},
            "audience": {"private_users": 1248, "groups": 82, "tracked_channels": 1680, "unique_twitch_channels": 303},
            "live": [{"login": "very_long_russian_test_channel_name_with_many_symbols", "destinations": 4, "viewers": 1842, "observed_at": now}, {"login": "nightstream", "destinations": 2, "viewers": None, "observed_at": now}],
            "queues": {"pending_deliveries": 3, "oldest_pending_age_seconds": 42, "deferred_reports": 1,
                       "pending_jobs": 12, "leased_jobs": 2, "due_jobs": 5, "failed_jobs": 1,
                       "oldest_due_age_seconds": 640},
            "growth": [{"source": "site", "touched": 240, "activated": 96, "ever_test_plus": 12},
                       {"source": "referral", "touched": 88, "activated": 41, "ever_test_plus": 5}],
            "access": {"active_total": 428, "viewer": 401, "streamer": 27,
                       "by_source": {"test": 96, "paid": 300, "mock": 32}, "expiring_7d": 18,
                       "active_rows": [
                           {"grant_id": "g1", "subject_kind": "viewer", "subject_id": "701000042",
                            "plan": "viewer_plus", "source": "paid", "starts_at": now - 86400,
                            "expires_at": now + 12 * 86400, "issued_by": 425785231, "person_id": 701000042},
                           {"grant_id": "g2", "subject_kind": "streamer", "subject_id": "bc-901",
                            "plan": "streamer_plus", "source": "test", "starts_at": now - 3600,
                            "expires_at": now + 3 * 86400, "issued_by": 425785231, "person_id": 701000777},
                       ],
                       "history": [
                           {"grant_id": "g2", "action": "grant", "actor_telegram_id": 425785231,
                            "happened_at": now - 3600, "plan": "streamer_plus", "source": "test",
                            "subject_kind": "streamer", "subject_id": "bc-901"},
                           {"grant_id": "g1", "action": "revoke", "actor_telegram_id": 0,
                            "happened_at": now - 5 * 86400, "plan": "viewer_plus", "source": "test",
                            "subject_kind": "viewer", "subject_id": "701000042"},
                       ]},
            "backup": {"last_backup_at": now - 6 * 3600, "last_backup_name": "auto-20261004T090000Z.db",
                       "retention": 5, "restore_verified": False},
            "deliveries": {"notifications": 9624, "reports": 118, "total": 9742},
            "activity": {
                "active_today": 842, "new_7d": 316,
                "by_day": [
                    {"date": now - 6 * 86400, "users": 512},
                    {"date": now - 5 * 86400, "users": 604},
                    {"date": now - 4 * 86400, "users": 588},
                    {"date": now - 3 * 86400, "users": 701},
                    {"date": now - 2 * 86400, "users": 655},
                    {"date": now - 1 * 86400, "users": 780},
                    {"date": now, "users": 842},
                ],
            },
            "attention": [
                {"kind": "queue_failed", "severity": "danger", "title": "Не отправлено заданий: 1",
                 "detail": "Очередь уведомлений содержит неудачные задания."},
                {"kind": "queue_delay", "severity": "warn", "title": "Уведомления задерживаются",
                 "detail": "В очереди 5, старейшее ждёт 10 мин."},
                {"kind": "eventsub", "severity": "warn", "title": "EventSub: ошибка",
                 "detail": "Повторится на следующем цикле, если причина не исчезнет."},
            ],
            "errors": {"poller": None, "eventsub": "NetworkError", "preview": None, "database": None, "directory": None},
            "resources": {"process_cpu_percent": 3.4, "process_ram_bytes": 68300000, "db_volume_free_bytes": 2700000000, "db_file_bytes": 786432, "wal_file_bytes": 4096},
        }

    server.set_admin_snapshot_provider(snapshot)

    # Локальные заглушки read- и write-маршрутов панели: только для проверки UI.
    person_card = {
        "user_id": 701000042, "username": "alex_live", "display_name": "Алексей Петров",
        "twitch_login": "alex_stream", "last_active_at": time.time() - 300,
        "grants": [
            {"grant_id": "g1", "plan": "viewer_plus", "source": "manual",
             "starts_at": time.time() - 86400, "expires_at": time.time() + 12 * 86400},
            {"grant_id": "g2", "plan": "streamer_plus", "source": "test",
             "starts_at": time.time() - 3600, "expires_at": time.time() + 3 * 86400},
        ],
        "limits": {"channels": {"used": 38, "limit": 200}, "video": {"used": 3, "limit": 5}},
    }
    history = [
        {"action": "grant", "happened_at": time.time() - 3600, "actor_telegram_id": 425785231,
         "reason": "partnership", "reason_note": None, "comment": "партнёрский доступ",
         "previous_grant_id": None, "previous_expires_at": None,
         "new_expires_at": time.time() + 12 * 86400, "plan": "viewer_plus", "source": "manual"},
    ]

    async def search_people(query, *, filter_kind, limit, offset, now):
        return [{
            "user_id": 701000042, "username": "alex_live", "display_name": "Алексей Петров",
            "last_active_at": time.time() - 300, "plan": "viewer_plus",
            "expires_at": time.time() + 12 * 86400,
        }]

    async def person_card_lookup(user_id, *, now):
        return person_card if user_id == 701000042 else None

    async def person_history(user_id, *, limit, offset):
        return history

    async def grant_manual_access(*args, **kwargs):
        return {"grant_id": "new", "action": "grant", "source": "manual"}

    async def extend_manual_access(*args, **kwargs):
        return {"grant_id": "next", "action": "extend", "source": "manual"}

    async def revoke_manual_access(*args, **kwargs):
        return {"grant_id": "g1", "action": "revoke", "source": "manual"}

    async def access_overview(now):
        return {"active_total": 428, "viewer": 401, "streamer": 27,
                "by_source": {"test": 96, "paid": 300, "mock": 32}, "expiring_7d": 18}

    async def active_grants(now, limit, offset):
        return person_card["grants"]

    async def history_events(limit, offset):
        return history

    services = SimpleNamespace(
        search_people=search_people, person_card=person_card_lookup,
        person_history=person_history, grant_manual_access=grant_manual_access,
        extend_manual_access=extend_manual_access, revoke_manual_access=revoke_manual_access,
    )
    directory = SimpleNamespace(access_overview=access_overview,
                                active_grants=active_grants, history=history_events)
    server.set_admin_services(people=services, directory=directory)
    await server.start()
    port = server._runner.addresses[0][1]
    print(f"Local synthetic admin fixture listening on http://127.0.0.1:{port}/admin", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
