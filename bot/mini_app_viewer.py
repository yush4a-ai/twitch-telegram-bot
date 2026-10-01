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
        video = await db.get_video_selection(user_id, now=now)
        filters = await db.list_viewer_filters(user_id) if flags.viewer_filters else {}
        quiet = await db.get_quiet_hours(user_id)
        live = {
            login: {"title": title, "viewer_count": viewers, "category": category}
            for login, title, viewers, category in await db.list_live_channels(user_id)
        }
        subscriptions = [
            {
                "login": login,
                "notify_enabled": notify_enabled,
                "paused_by_plan": paused_by_plan,
                "video_selected": login in video.selected_logins,
                "video_effective": login in video.selected_logins and video.selected_ids[video.selected_logins.index(login)] in video.effective_ids,
                "is_live": is_live,
                "status": (
                    "live" if is_live and observed_at is not None and now - observed_at <= 300
                    else "stale" if is_live else "offline"
                ),
                "observed_at": observed_at if is_live else None,
                "live": live.get(login) if is_live else None,
                "filter": (
                    {
                        "version": filters[login][0],
                        "games": list(filters[login][1].games),
                        "title_keywords": list(filters[login][1].title_keywords),
                        "exclude_keywords": list(filters[login][1].exclude_keywords),
                    }
                    if login in filters else None
                ),
            }
            for login, notify_enabled, is_live, observed_at, paused_by_plan in rows
        ]
        return web.json_response({
            "subscriptions": subscriptions,
            "channel_limit": flags.viewer_channel_limit,
            "viewer_plus_active": flags.viewer_plus_active,
            "video_selection": {
                "version": video.version, "selected_ids": list(video.selected_ids),
                "selected_logins": list(video.selected_logins),
                "effective_ids": list(video.effective_ids), "limit": video.limit,
            },
            "quiet_hours": (
                {
                    "start_minute": quiet[0], "end_minute": quiet[1],
                    "utc_offset_minutes": quiet[2], "digest_enabled": quiet[3],
                }
                if quiet is not None else None
            ),
        })

    async def save_filter(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        version = values.get("expected_version")
        if login is None or type(version) is not int or version < 0:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if login not in await db.list_channels(user_id):
            return web.json_response({"error": "not_subscribed"}, status=404)
        flags = await capabilities.for_user(user_id, now=time.time())
        if not flags.viewer_filters:
            return web.json_response({"error": "plus_required"}, status=403)
        try:
            saved = await db.save_viewer_filter(
                user_id, login, expected_version=version,
                games=values.get("games"),
                title_keywords=values.get("title_keywords"),
                exclude_keywords=values.get("exclude_keywords"),
            )
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        except ValueError:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if saved is None:
            return web.json_response({"error": "version_conflict"}, status=409)
        return web.json_response({"version": saved})

    async def quiet_hours(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if values.get("clear") is True:
            await db.clear_quiet_hours(user_id)
            return web.json_response({"quiet_hours": None})
        start = values.get("start_minute")
        end = values.get("end_minute")
        offset = values.get("utc_offset_minutes")
        if (
            type(start) is not int or not 0 <= start < 1440
            or type(end) is not int or not 0 <= end < 1440 or start == end
            or type(offset) is not int or not -720 <= offset <= 840
        ):
            return web.json_response({"error": "invalid_settings"}, status=400)
        await db.set_quiet_hours(user_id, start, end, offset)
        stored = await db.get_quiet_hours(user_id)
        return web.json_response({
            "quiet_hours": {
                "start_minute": stored[0], "end_minute": stored[1],
                "utc_offset_minutes": stored[2], "digest_enabled": stored[3],
            },
        })

    async def digest(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        enabled = values.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if not await db.set_quiet_hours_notify_after(user_id, enabled):
            return web.json_response({"error": "quiet_hours_required"}, status=409)
        return web.json_response({"digest_enabled": enabled})

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
        video = await db.get_video_selection(user_id)
        return web.json_response({
            "removed": True, "login": login,
            "video_selection": {
                "version": video.version, "selected_ids": list(video.selected_ids),
                "selected_logins": list(video.selected_logins),
                "effective_ids": list(video.effective_ids), "limit": video.limit,
            },
        })

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

    async def video_selection(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        logins = values.get("selected_logins")
        version = values.get("expected_version")
        if type(version) is not int or version < 0 or not isinstance(logins, list):
            return web.json_response({"error": "invalid_selection"}, status=400)
        if len(logins) > 5:
            return web.json_response({"error": "video_limit"}, status=409)
        normalized = [normalize_twitch_login(login) for login in logins]
        if None in normalized or len(set(normalized)) != len(normalized):
            return web.json_response({"error": "invalid_selection"}, status=400)
        flags = await capabilities.for_user(user_id, now=time.time())
        if flags.viewer_video_slots == 0:
            return web.json_response({"error": "plus_required"}, status=403)
        owned = set(await db.list_channels(user_id))
        if any(login not in owned for login in normalized):
            return web.json_response({"error": "not_subscribed"}, status=400)
        if twitch is None:
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        try:
            choices = [(await twitch.get_user_id(login), login) for login in normalized]
        except Exception:
            logger.exception("Mini App Twitch identity lookup failed")
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        if any(broadcaster_id is None for broadcaster_id, _ in choices):
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        try:
            saved = await db.replace_video_selection(
                user_id, choices, expected_version=version, now=time.time(),
            )
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        except ValueError:
            return web.json_response({"error": "invalid_selection"}, status=400)
        if saved is None:
            return web.json_response({"error": "version_conflict"}, status=409)
        return web.json_response({
            "version": saved.version, "selected_ids": list(saved.selected_ids),
            "selected_logins": list(saved.selected_logins),
            "effective_ids": list(saved.effective_ids), "limit": saved.limit,
        })

    async def plan_activate(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        if login is None:
            return web.json_response({"error": "invalid_login"}, status=400)
        result = await db.promote_personal_channel(user_id, login)
        if result == "not_subscribed":
            return web.json_response({"error": result}, status=404)
        return web.json_response({"result": result})

    app.router.add_post("/app/api/viewer/state", state)
    app.router.add_post("/app/api/viewer/search", search)
    app.router.add_post("/app/api/viewer/follow", follow)
    app.router.add_post("/app/api/viewer/unfollow", unfollow)
    app.router.add_post("/app/api/viewer/notify", notify)
    app.router.add_post("/app/api/viewer/filter", save_filter)
    app.router.add_post("/app/api/viewer/video-selection", video_selection)
    app.router.add_post("/app/api/viewer/plan-activate", plan_activate)
    app.router.add_post("/app/api/viewer/quiet-hours", quiet_hours)
    app.router.add_post("/app/api/viewer/digest", digest)
