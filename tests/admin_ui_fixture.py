"""Local-only synthetic dashboard for repeatable browser checks."""

import asyncio
import signal
import time

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer


FIXTURE_KEY = "local-browser-fixture-key-32-chars-minimum"


async def main() -> None:
    server = OAuthCallbackServer(
        "http://127.0.0.1/twitch/callback", "127.0.0.1", 0,
        admin_access=AdminAccess(FIXTURE_KEY, enabled=True, secure_cookie=False),
    )

    async def snapshot() -> dict:
        return {
            "generated_at": time.time(), "environment": "local",
            "telegram": {"state": "ok", "polling_running": True, "delivery_verified": "unknown"},
            "twitch": {"state": "degraded", "last_success_age_seconds": 64, "eventsub_ready": 2, "eventsub_configured": 3, "auth_blocked_logins": 1},
            "preview": {"state": "disabled", "active_sessions": 0, "active_jobs": 0, "last_success_age_seconds": None, "disabled_reason": "config_error"},
            "audience": {"private_users": 1248, "groups": 82, "tracked_channels": 1680, "unique_twitch_channels": 303},
            "live": [{"login": "very_long_russian_test_channel_name_with_many_symbols", "destinations": 4, "viewers": 1842, "observed_at": time.time()}, {"login": "nightstream", "destinations": 2, "viewers": None, "observed_at": time.time()}],
            "queues": {"pending_deliveries": 3, "oldest_pending_age_seconds": 42, "deferred_reports": 1},
            "errors": {"poller": None, "eventsub": "NetworkError", "preview": None, "database": None},
            "resources": {"process_cpu_percent": 3.4, "process_ram_bytes": 68300000, "db_volume_free_bytes": 2700000000, "db_file_bytes": 786432, "wal_file_bytes": 4096},
        }

    server.set_admin_snapshot_provider(snapshot)
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
