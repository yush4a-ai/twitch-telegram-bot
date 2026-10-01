"""Signed Free viewer actions backed by the bot's existing tracking rows."""

from __future__ import annotations

import logging
import time
from collections import deque
from urllib.parse import urlsplit

from aiohttp import web

from .capabilities import CapabilityService
from .database import Database
from .deep_links import TWITCH_LOGIN_RE
from .mini_app_auth import verified_payload


logger = logging.getLogger(__name__)
_SEARCH_WINDOW_SECONDS = 10.0
_SEARCH_WINDOW_LIMIT = 6


def normalize_twitch_login(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 200:
        return None
    text = value.strip()
    if text.lower().startswith(("twitch.tv/", "www.twitch.tv/")):
        text = "https://" + text
    if "://" in text:
        try:
            url = urlsplit(text)
            if (
                url.scheme not in {"http", "https"}
                or url.hostname not in {"twitch.tv", "www.twitch.tv"}
                or url.username is not None
                or url.password is not None
                or url.port is not None
            ):
                return None
            text = url.path.strip("/")
            if "/" in text:
                return None
        except ValueError:
            return None
    elif "/" in text or "." in text or "@" in text:
        return None
    login = text.lower()
    return login if TWITCH_LOGIN_RE.fullmatch(login) else None


def install_mini_app_viewer_routes(
    app: web.Application,
    db: Database,
    bot_token: str,
    capabilities: CapabilityService,
    twitch,
) -> None:
    search_times: dict[int, deque[float]] = {}

    async def read(request: web.Request) -> tuple[int | None, dict[str, object] | None, web.Response | None]:
        user_id, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return None, None, web.json_response({"error": "unauthorized"}, status=status)
        return user_id, values, None

    async def state(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        now = time.time()
        flags = await capabilities.for_user(user_id, now=now)
        rows = await db.list_personal_channel_status(user_id)
        live = {
            login: {"title": title, "viewer_count": viewers, "category": category}
            for login, title, viewers, category in await db.list_live_channels(user_id)
        }
        subscriptions = [
            {
                "login": login,
                "notify_enabled": notify_enabled,
                "is_live": is_live,
                "status": (
                    "live" if is_live and observed_at is not None and now - observed_at <= 300
                    else "stale" if is_live else "offline"
                ),
                "observed_at": observed_at if is_live else None,
                "live": live.get(login) if is_live else None,
            }
            for login, notify_enabled, is_live, observed_at in rows
        ]
        return web.json_response({
            "subscriptions": subscriptions,
            "channel_limit": flags.viewer_channel_limit,
            "viewer_plus_active": flags.viewer_plus_active,
        })

    async def search(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        query = normalize_twitch_login(values.get("query"))
        if query is None:
            return web.json_response({"error": "invalid_login"}, status=400)
        now = time.monotonic()
        if len(search_times) > 10000:
            search_times.clear()
        recent = search_times.setdefault(user_id, deque())
        while recent and now - recent[0] >= _SEARCH_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= _SEARCH_WINDOW_LIMIT:
            return web.json_response({"error": "rate_limited"}, status=429)
        recent.append(now)
        if twitch is None:
            return web.json_response({"error": "search_unavailable"}, status=503)
        try:
            found = await twitch.search_channels(query, limit=6)
        except Exception:
            logger.exception("Mini App Twitch search failed")
            return web.json_response({"error": "search_unavailable"}, status=503)
        results = [
            {
                "login": login,
                "display_name": str(item.display_name)[:100],
                "is_live": bool(item.is_live),
            }
            for item in found
            if (login := normalize_twitch_login(item.login)) is not None
        ]
        return web.json_response({"results": results})

    async def follow(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        if login is None:
            return web.json_response({"error": "invalid_login"}, status=400)
        if login in await db.list_channels(user_id):
            return web.json_response({"result": "already", "login": login})
        if twitch is None:
            return web.json_response({"error": "search_unavailable"}, status=503)
        try:
            exists = await twitch.channel_exists(login)
        except Exception:
            logger.exception("Mini App Twitch lookup failed")
            return web.json_response({"error": "search_unavailable"}, status=503)
        if not exists:
            return web.json_response({"error": "not_found"}, status=404)
        flags = await capabilities.for_user(user_id, now=time.time())
        result = await db.add_channel_with_limit(user_id, login, flags.viewer_channel_limit)
        if result == "limit":
            return web.json_response({"error": "channel_limit"}, status=409)
        return web.json_response({"result": result, "login": login})

    async def unfollow(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        if login is None:
            return web.json_response({"error": "invalid_login"}, status=400)
        if not await db.remove_channel(user_id, login):
            return web.json_response({"error": "not_subscribed"}, status=404)
        return web.json_response({"removed": True, "login": login})

    async def notify(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        enabled = values.get("enabled")
        if login is None or type(enabled) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        try:
            updated = await db.set_personal_notify_if_subscribed(user_id, login, enabled)
        except Exception:
            logger.exception("Mini App notification write failed")
            return web.json_response({"error": "save_unavailable"}, status=503)
        if not updated:
            return web.json_response({"error": "not_subscribed"}, status=404)
        return web.json_response({"notify_enabled": enabled})

    app.router.add_post("/app/api/viewer/state", state)
    app.router.add_post("/app/api/viewer/search", search)
    app.router.add_post("/app/api/viewer/follow", follow)
    app.router.add_post("/app/api/viewer/unfollow", unfollow)
    app.router.add_post("/app/api/viewer/notify", notify)
