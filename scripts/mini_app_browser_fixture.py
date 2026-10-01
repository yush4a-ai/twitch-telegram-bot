"""Local-only browser fixture for the new Mini App shell; no external sends."""

import asyncio
import json
import os
import signal
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from bot.mini_app_streamer import complete_community_intent
from bot.viewer_history import ViewerHistoryService


class FixtureBot:
    id = 999

    async def get_chat(self, chat_id):
        return SimpleNamespace(type="supergroup", title="Тестовое сообщество")

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")

    async def save_prepared_keyboard_button(self, *, user_id, button):
        return SimpleNamespace(id=f"fixture-prepared-{button.request_chat.request_id}")


class FixtureTwitch:
    _channels = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
    _categories = {"100": "Minecraft", "200": "Just Chatting", "300": "Art"}

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

    async def search_categories(self, query, *, limit=8):
        return [
            (category_id, name)
            for category_id, name in self._categories.items()
            if query.casefold() in name.casefold()
        ][:limit]

    async def get_categories(self, ids):
        return {category_id: self._categories[category_id] for category_id in ids
                if category_id in self._categories}


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
        preview_status = os.getenv("MINI_APP_QA_PREVIEW_STATUS", "")
        if preview_status or os.getenv("MINI_APP_QA_REMINDER") or os.getenv("MINI_APP_QA_HISTORY"):
            if preview_status:
                await db.replace_video_selection(501, [("1000", "alpha")], expected_version=0)
            await db.set_live_state(
                501, "alpha", True, "fixture-live", 701, "Тестовый эфир",
                broadcaster_id="1000", last_seen_live_at=now,
            )
        if os.getenv("MINI_APP_QA_HISTORY"):
            await ViewerHistoryService(db).record_direct_live(
                501, "alpha", "fixture-live", 701, now=now,
            )
            await db.issue_test_viewer_plus(
                503, "browser-history-empty", starts_at=now - 5,
                expires_at=now + 3600, issued_by=425785231, now=now,
            )
        await db.link_streamer_identity(601, "2001", "alpha", verified_at=now)
        await db.link_streamer_identity(603, "2003", "beta", verified_at=now)
        await db.link_streamer_identity(604, "2004", "gamma", verified_at=now)
        await db.link_streamer_identity(605, "2005", "delta", verified_at=now)
        await db.add_streamer_community(603, -1003, "Сообщество Plus", "supergroup", now=now)
        await db.add_streamer_community(604, -1004, "Бесплатное сообщество", "supergroup", now=now)
        await db.add_streamer_community(605, -1005, "Тестовое сообщество", "supergroup", now=now)
        await db.add_channel(-1003, "beta")
        await db.add_channel(-1004, "gamma")
        await db.add_channel(-1005, "delta")
        await db.issue_test_streamer_plus(
            "2003", "browser-streamer-plus", starts_at=now - 5,
            expires_at=now + 3600, issued_by=425785231, now=now,
        )
        bot = FixtureBot()
        app = web.Application()
        install_mini_app_routes(
            app, db, "123456:test-telegram-token", bot=bot,
            twitch=FixtureTwitch(), bot_username="TwitchSignalTestbot",
            billing_test_enabled=True, billing_test_user_ids=frozenset({501, 603, 605}),
            preview_status_provider=(
                (lambda login: preview_status if login == "alpha" else "unknown")
                if preview_status else None
            ),
        )

        async def complete_fixture_community(request):
            # Loopback-only browser fixture: emulate Telegram's service message, never send one.
            body = await request.json()
            row = await db.get_community_intent(body.get("intent_id", ""))
            if row is None:
                return web.json_response({"connected": False}, status=404)
            connected = await complete_community_intent(
                db, bot, row[1], row[2], -1001, now=time.time(),
            )
            return web.json_response({"connected": connected})

        app.router.add_post("/_qa/complete-community", complete_fixture_community)
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
