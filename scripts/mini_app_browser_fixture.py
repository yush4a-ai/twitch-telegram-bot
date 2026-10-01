"""Local-only browser fixture for the new Mini App shell; no external sends."""

import asyncio
import json
import signal
import tempfile
from pathlib import Path

from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes


class FixtureTwitch:
    async def channel_exists(self, login):
        return login in {"alpha", "beta"}

    async def search_channels(self, query, limit=6):
        if query not in {"alpha", "beta"}:
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
