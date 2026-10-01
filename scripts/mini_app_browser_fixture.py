"""Local-only browser fixture for the new Mini App shell; no external sends."""

import asyncio
import json
import signal
import tempfile
import time
from pathlib import Path

from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes


class FixtureTwitch:
    _channels = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")

    async def channel_exists(self, login):
        return login in self._channels

    async def get_user_id(self, login):
        return str(1000 + self._channels.index(login)) if login in self._channels else None

    async def search_channels(self, query, limit=6):
        if query not in self._channels:
            return []
        from types import SimpleNamespace
        name = (
            "Очень длинное русское имя стримера с несколькими словами и подробным описанием"
            if query == "beta" else query.title()
        )
        return [SimpleNamespace(login=query, display_name=name, is_live=False)]


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ts-mini-app-browser-") as directory:
        db = Database(str(Path(directory) / "fixture.db"))
        await db.connect()
        await db.add_channel(501, "alpha")
        for login in FixtureTwitch._channels[1:]:
            await db.add_channel(501, login)
        for index in range(51):
            await db.add_channel(502, f"track{index:03}")
        now = time.time()
        await db.issue_test_viewer_plus(
            501, "browser-plus-fixture", starts_at=now - 5,
            expires_at=now + 3600, issued_by=425785231, now=now,
        )
        app = web.Application()
        install_mini_app_routes(app, db, "123456:test-telegram-token", twitch=FixtureTwitch())
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        print(json.dumps({"url": f"http://127.0.0.1:{runner.addresses[0][1]}/app"}), flush=True)
        stop = asyncio.Event()
        for name in ("SIGINT", "SIGTERM"):
            try:
                asyncio.get_running_loop().add_signal_handler(getattr(signal, name), stop.set)
            except NotImplementedError:
                pass
        try:
            await stop.wait()
        finally:
            await runner.cleanup()
            await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
