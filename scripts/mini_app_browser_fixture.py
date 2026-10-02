"""Local-only browser fixture for the new Mini App shell; no external sends."""

import asyncio
import json
import ipaddress
import os
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from bot.mini_app_streamer import complete_community_intent
from bot.viewer_history import ViewerHistoryService
from bot.viewer_reminders import ViewerReminderService
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationWorker, NotificationOutcome
from bot.oauth_result import result_page, UI_DIR as OAUTH_UI_DIR, MESSAGES as OAUTH_MESSAGES


FIXTURE_BOT_TOKEN = "123456:test-telegram-token"
SCENARIOS = frozenset({
    "free-empty", "free-six", "plus-two-hundred", "streamer-plus",
    "independent-viewer", "legacy-group", "channel-permissions",
    "payment-off", "legal-unready", "reminder-inflight", "history",
    "streamer-unconnected", "channel-empty",
})


class FixtureBot:
    id = 999

    def __init__(self, *, channel=False):
        self.channel = channel
        self.sent_calls = []
        self.permission = "ready"

    async def get_chat(self, chat_id):
        if self.permission == "network_error":
            raise TimeoutError("local Telegram outage")
        return SimpleNamespace(
            id=chat_id,
            type="channel" if self.channel and chat_id != -1002 else "supergroup",
            title="Канал с длинным названием о стримах, играх и совместных эфирах",
            username="verified_channel" if chat_id == -1001 else None,
        )

    async def get_chat_member(self, chat_id, user_id):
        if user_id == self.id:
            status = {"bot_absent": "left", "bot_member": "member"}.get(self.permission, "administrator")
            return SimpleNamespace(status=status, can_post_messages=self.permission != "missing_post_right",
                                   can_edit_messages=False)
        return SimpleNamespace(status="administrator")

    async def save_prepared_keyboard_button(self, *, user_id, button):
        return SimpleNamespace(id=f"fixture-prepared-{button.request_chat.request_id}")

    async def send_message(self, **kwargs):
        self.sent_calls.append(("send_message", kwargs))
        raise AssertionError("No external sender in local browser fixture")

    async def send_invoice(self, **kwargs):
        self.sent_calls.append(("send_invoice", kwargs))
        raise AssertionError("No invoice in local browser fixture")


class FixtureTwitch:
    _channels = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
    _categories = {"100": "Minecraft", "200": "Just Chatting", "300": "Art"}

    def __init__(self, channels=None):
        self._channels = tuple(channels or self._channels)

    async def get_display_names(self, logins):
        return {
            login: (
                "Очень длинное русское имя стримера с несколькими словами и подробным описанием"
                if login == "beta" else login.title()
            ) for login in logins if login in self._channels
        }

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


@dataclass
class FixtureState:
    scenario: str
    now: float
    directory: tempfile.TemporaryDirectory
    bot: FixtureBot
    twitch: FixtureTwitch
    external_payment_calls: list = field(default_factory=list)


FIXTURE_STATE_KEY = web.AppKey("redesign_fixture_state", FixtureState)
FIXTURE_STOP_KEY = web.AppKey("redesign_fixture_stop", asyncio.Event)


async def build_fixture(scenario: str, *, now: float | None = None) -> tuple[web.Application, Database]:
    """Create a disposable local app; never load environment credentials or live data."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown browser fixture scenario")
    observed_at = time.time() if now is None else now
    directory = tempfile.TemporaryDirectory(prefix="ts-redesign-browser-")
    db = Database(str(Path(directory.name) / "fixture.db"))
    try:
        await db.connect()
        channels = list(FixtureTwitch._channels)
        if scenario == "plus-two-hundred":
            channels += [f"track{index:03}" for index in range(194)]
        if scenario != "free-empty":
            for login in channels:
                await db.add_channel(501, login)
            await db.set_live_state(
                501, "alpha", True, "fixture-alpha", 701,
                "Длинное название эфира: прохождение, обсуждение игры и общение со зрителями",
                broadcaster_id="1000", last_seen_live_at=observed_at,
            )
            await db.set_live_state(
                501, "beta", True, "fixture-beta", 702,
                "Последние данные пока не обновились", broadcaster_id="1001",
                last_seen_live_at=observed_at - 601,
            )
            await db.set_notify_enabled(501, "zeta", False)
            await db.add_stream_sample(
                501, "alpha", "fixture-alpha", observed_at, 27,
                "Длинное название эфира: прохождение, обсуждение игры и общение со зрителями",
                "Minecraft",
            )
        if scenario in {"plus-two-hundred", "independent-viewer"}:
            await db.issue_test_viewer_plus(
                501, "redesign-viewer", starts_at=observed_at - 5,
                expires_at=observed_at + 3600, issued_by=425785231, now=observed_at,
            )
            await db.replace_video_selection(
                501, [(str(1000 + index), login) for index, login in enumerate(channels[:3])],
                expected_version=0,
            )
        if scenario != "streamer-unconnected":
            await db.link_streamer_identity(501, "2001", "alpha", verified_at=observed_at)
            if scenario != "channel-empty":
                await db.add_streamer_community(
                    501, -1001, "Очень длинное название Telegram-канала для уведомлений о новых эфирах",
                    "channel", now=observed_at,
                )
        if scenario == "legacy-group":
            await db.add_streamer_community(501, -1002, "Существующая группа", "supergroup", now=observed_at)
            await db.add_channel(-1002, "alpha")
        if scenario in {"streamer-plus", "independent-viewer"}:
            await db.issue_test_streamer_plus(
                "2001", "redesign-streamer", starts_at=observed_at - 5,
                expires_at=observed_at + 1800, issued_by=425785231, now=observed_at,
                beneficiary_telegram_user_id=501,
            )
        if scenario in {"reminder-inflight", "history"}:
            await db.issue_test_viewer_plus(
                501, "redesign-settings", starts_at=observed_at - 1000,
                expires_at=observed_at + 3600, issued_by=425785231, now=observed_at,
            )
            queue = NotificationQueue(db)
            if scenario == "reminder-inflight":
                reminders = ViewerReminderService(db)
                selected_at = observed_at - 901
                await db.set_live_state(501, "alpha", True, "fixture-alpha", 701,
                                        "Title", broadcaster_id="1000", last_seen_live_at=selected_at)
                saved = await reminders.set_reminder(501, "1000", "fixture-alpha", 15, now=selected_at)
                await db.set_live_state(501, "alpha", True, "fixture-alpha", 701,
                                        "Title", broadcaster_id="1000", last_seen_live_at=observed_at)
                await queue.enqueue("viewer_reminder", 501, "alpha", "fixture-alpha", saved.version,
                                    due_at=saved.due_at, now=observed_at)
                job = (await queue.claim_due(observed_at, limit=1, lease_seconds=180))[0]
                if not await reminders.begin_delivery(job, now=observed_at):
                    raise AssertionError("fixture reminder must cross the real delivery fence")
            else:
                await db.issue_test_viewer_plus(
                    202, "redesign-history-foreign", starts_at=observed_at - 1000,
                    expires_at=observed_at + 3600, issued_by=425785231, now=observed_at,
                )
                for index in range(26):
                    event_at = observed_at - 30 + index
                    outcome = index % 3
                    kind = "viewer_category_change" if outcome == 2 else "go_live"
                    async def sender(_job, selected_outcome=outcome):
                        if selected_outcome == 2:
                            raise TimeoutError("fake sender has no confirmed response")
                        return NotificationOutcome.SENT if selected_outcome == 0 else NotificationOutcome.STALE
                    await queue.enqueue(kind, 501 if index < 25 else 202,
                                        "alpha" if index < 25 else "foreign", f"fixture-history-{index}", 1,
                                        due_at=event_at, now=event_at)
                    worker = NotificationWorker(queue, sender, max_concurrency=1,
                                                per_chat_interval=0, group_chat_interval=0, global_interval=0,
                                                clock=lambda value=event_at: value)
                    if await worker.run_once() != 1:
                        raise AssertionError("fixture history requires a terminal fake delivery")
        state = FixtureState(scenario, observed_at, directory, FixtureBot(channel=True), FixtureTwitch(channels))
        app = web.Application()
        app[FIXTURE_STATE_KEY] = state
        app[FIXTURE_STOP_KEY] = asyncio.Event()
        install_mini_app_routes(
            app, db, FIXTURE_BOT_TOKEN, bot=state.bot, twitch=state.twitch,
            bot_username="TwitchSignalTestbot", billing_test_enabled=False,
        )

        async def cleanup(_app):
            await db.close()
            directory.cleanup()

        app.on_cleanup.append(cleanup)

        async def shutdown(request):
            try:
                allowed = ipaddress.ip_address(request.remote or "").is_loopback
            except ValueError:
                allowed = False
            if not allowed:
                return web.json_response({"error": "local_only"}, status=403)
            app[FIXTURE_STOP_KEY].set()
            return web.json_response({"stopped": True})

        app.router.add_post("/_qa/shutdown", shutdown)
        async def channel_control(request):
            if not ipaddress.ip_address(request.remote or "0.0.0.0").is_loopback:
                return web.json_response({"error": "local_only"}, status=403)
            values = await request.json()
            mode = values.get("permission", "ready")
            if mode not in {"ready", "bot_absent", "bot_member", "missing_post_right", "network_error"}:
                return web.json_response({"error": "invalid_permission"}, status=400)
            state.bot.permission = mode
            intent = await db.get_community_intent(values.get("intent_id", ""))
            if values.get("expire") and intent and intent[1] == 501:
                await db.conn.execute("UPDATE streamer_community_intents SET expires_at=? WHERE intent_id=?",
                                      (time.time() - 1, intent[0]))
                await db.conn.commit()
            connected = False
            if values.get("complete") and intent and intent[1] == 501:
                connected = await complete_community_intent(db, state.bot, 501, intent[2], -1003,
                                                          now=time.time())
            return web.json_response({"connected": connected, "sent_calls": len(state.bot.sent_calls)})
        app.router.add_post("/_qa/channel-control", channel_control)
        async def oauth_picture(request):
            status = request.match_info["status"]
            if status not in OAUTH_MESSAGES:
                raise web.HTTPNotFound()
            response = web.Response(text=result_page(status, bot_username="TwitchSignalTestbot"),
                                    content_type="text/html", headers={"Cache-Control": "no-store"})
            response.set_cookie("qa_oauth_status", status, httponly=True, samesite="Strict", path="/twitch/result")
            return response
        async def oauth_picture_status(request):
            status = request.cookies.get("qa_oauth_status")
            if status not in OAUTH_MESSAGES:
                raise web.HTTPForbidden()
            return web.json_response({"status":status}, headers={"Cache-Control":"no-store"})
        async def oauth_picture_asset(request):
            filename = request.match_info["filename"]
            if filename not in {"ui.css", "ui.js"}:
                raise web.HTTPNotFound()
            return web.FileResponse(OAUTH_UI_DIR / filename)
        app.router.add_get("/_qa/oauth-result/{status}", oauth_picture)
        app.router.add_get("/twitch/result/status", oauth_picture_status)
        app.router.add_get("/twitch/result/{filename}", oauth_picture_asset)
        return app, db
    except BaseException:
        await db.close()
        directory.cleanup()
        raise


async def main() -> None:
    scenario = os.getenv("MINI_APP_QA_SCENARIO")
    if scenario:
        app, _db = await build_fixture(scenario)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        print(json.dumps({"url": f"http://127.0.0.1:{runner.addresses[0][1]}/app", "scenario": scenario}), flush=True)
        try:
            await app[FIXTURE_STOP_KEY].wait()
        finally:
            await runner.cleanup()
        return
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
        if os.getenv("MINI_APP_QA_PRESET_SECOND_COMMUNITY"):
            await db.add_streamer_community(603, -1002, "Второе сообщество", "supergroup", now=now)
            await db.add_channel(-1002, "beta")
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
            billing_test_enabled=True,
            billing_test_user_ids=frozenset({501, 603, 605, 504} if os.getenv("MINI_APP_QA_TRIAL") else {501, 603, 605}),
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
