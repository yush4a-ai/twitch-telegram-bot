"""Signed Free viewer actions backed by the bot's existing tracking rows."""

from __future__ import annotations

import logging
import asyncio
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from aiohttp import web

from .capabilities import CapabilityService
from .category_alert_store import CategoryAlertPreference, CategoryAlertStore
from .database import Database
from .deep_links import TWITCH_LOGIN_RE
from .mini_app_auth import verified_payload
from .viewer_reminders import ReminderInFlightError, ViewerReminderService
from .viewer_folders import FolderConflict, FolderLimit, FolderNameTaken, ViewerFolderService
from .viewer_history import ViewerHistoryService


logger = logging.getLogger(__name__)
_SEARCH_WINDOW_SECONDS = 10.0
_SEARCH_WINDOW_LIMIT = 6
_NAMES_TIMEOUT = 5.0


def _avatar_url(value):
    if not isinstance(value, str) or len(value)>2048:
        return None
    try:
        url=urlsplit(value)
        if (url.scheme=='https' and url.hostname=='static-cdn.jtvnw.net'
                and url.port is None and url.username is None and url.password is None
                and url.path.startswith('/jtv_user_pictures/') and not url.query and not url.fragment):
            return value
    except ValueError:
        pass
    return None


class _PublicNames:
    """Small public metadata cache; never a source of user rights or live state."""

    def __init__(self, twitch, *, clock=time.monotonic):
        self.twitch, self.clock = twitch, clock
        self.entries = OrderedDict()
        self.lock = asyncio.Lock()

    async def get(self, logins):
        requested = tuple(dict.fromkeys(logins))
        if not requested:
            return {}
        try:
            # The whole budget includes waiting for an overlapping metadata request.
            async with asyncio.timeout(_NAMES_TIMEOUT):
                async with self.lock:
                    now = self.clock()
                    missing = [login for login in requested if login not in self.entries or now - self.entries[login][0] >= 300]
                    profiles = getattr(self.twitch, "get_public_profiles", None)
                    method = profiles if callable(profiles) else getattr(self.twitch, "get_display_names", None)
                    found = await method(missing) if missing and callable(method) else {}
                    if not isinstance(found, dict):
                        found = {}
                    for login in missing:
                        entry = found.get(login)
                        name = entry.get('display_name') if isinstance(entry, dict) else entry
                        avatar = _avatar_url(entry.get('profile_image_url')) if isinstance(entry, dict) else None
                        name = name.strip() if isinstance(name, str) and 0 < len(name) <= 256 else login
                        self.entries[login] = (self.clock(), name or login, avatar)
        except Exception:
            logger.warning("Mini App public names temporarily unavailable")
        now = self.clock()
        result = {}
        for login in requested:
            entry = self.entries.get(login)
            if entry is None or now - entry[0] >= 300:
                entry = self.entries[login] = (now, login, None)
            self.entries.move_to_end(login)
            result[login] = entry[1]
        while len(self.entries) > 2000:
            self.entries.popitem(last=False)
        return result

    def avatar(self, login):
        entry=self.entries.get(login)
        return entry[2] if entry and self.clock()-entry[0]<300 else None


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
    *, preview_status_provider=None,
) -> None:
    search_times: dict[int, deque[float]] = {}
    category_store = CategoryAlertStore(db, poll_interval=60)
    reminders = ViewerReminderService(db)
    folders = ViewerFolderService(db)
    history = ViewerHistoryService(db)
    public_names = _PublicNames(twitch)

    def folder_payload(saved) -> dict[str, object]:
        return {
            "id": saved.id, "name": saved.name, "version": saved.version,
            "games": list(saved.rule.games),
            "title_keywords": list(saved.rule.title_keywords),
            "exclude_keywords": list(saved.rule.exclude_keywords),
        }

    def reminder_payload(saved) -> dict[str, object] | None:
        if saved is None:
            return None
        return {
            "delay_minutes": saved.delay_minutes, "due_at": saved.due_at,
            "version": saved.version, "status": saved.status,
        }

    async def video_delivery_status(
        user_id: int, login: str, *, is_live: bool, effective: bool,
    ) -> str:
        if not is_live:
            return "offline"
        post = await db.get_live_post_state(user_id, login)
        if post is not None and post.media_transition_pending:
            return "unknown"
        if post is not None and post.message_kind == "animation":
            return "video" if effective else "returning_photo"
        if not effective:
            return "photo"
        try:
            status = preview_status_provider(login) if preview_status_provider else "unknown"
        except Exception:
            return "unknown"
        return status if status in {"limited", "unavailable", "preparing"} else "unknown"

    async def read(request: web.Request) -> tuple[int | None, dict[str, object] | None, web.Response | None]:
        user_id, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return None, None, web.json_response({"error": "unauthorized"}, status=status)
        return user_id, values, None

    async def state(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        rows = await db.list_personal_channel_status(user_id)
        display_names = await public_names.get([row[0] for row in rows])
        now = time.time()
        flags = await capabilities.for_user(user_id, now=now)
        saved_category_preferences = await category_store.list_preferences(user_id)
        category_preferences = {
            row[0]: saved_category_preferences.get(row[0], CategoryAlertPreference(False, (), 0))
            for row in rows
        }
        video = await db.get_video_selection(user_id, now=now)
        favorites = await db.list_viewer_favorites(user_id)
        own_reminders = await reminders.for_user(user_id)
        own_folders = await folders.list_folders(user_id)
        folder_memberships = await folders.memberships(user_id)
        filters = await db.list_viewer_filters(user_id) if flags.viewer_filters else {}
        quiet = await db.get_quiet_hours(user_id)
        live = {
            login: {"title": title, "viewer_count": viewers, "category": category}
            for login, title, viewers, category in await db.list_live_channels(user_id)
        }
        subscriptions = [
            {
                "login": login,
                "display_name": display_names[login],
                "avatar_url": public_names.avatar(login),
                "notify_enabled": notify_enabled,
                "is_favorite": login in favorites,
                "paused_by_plan": paused_by_plan,
                "video_selected": login in video.selected_logins,
                "video_effective": login in video.selected_logins and video.selected_ids[video.selected_logins.index(login)] in video.effective_ids,
                "video_delivery_status": await video_delivery_status(
                    user_id, login, is_live=is_live,
                    effective=(login in video.selected_logins and video.selected_ids[video.selected_logins.index(login)] in video.effective_ids),
                ),
                "reminder": reminder_payload(own_reminders.get(login)),
                "folder_id": folder_memberships.get(login),
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
                "category_alert": {
                    "enabled": category_preferences[login].enabled,
                    "effective": category_preferences[login].enabled and flags.viewer_category_alerts,
                    "category_ids": list(category_preferences[login].category_ids),
                    "category_names": list(category_preferences[login].category_names),
                    "version": category_preferences[login].version,
                },
            }
            for login, notify_enabled, is_live, observed_at, paused_by_plan in rows
        ]
        return web.json_response({
            "subscriptions": subscriptions,
            "channel_limit": flags.viewer_channel_limit,
            "viewer_plus_active": flags.viewer_plus_active,
            "folders": [folder_payload(folder) for folder in own_folders],
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

    async def reset_filter(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "login", "expected_version"}:
            return web.json_response({"error": "invalid_settings"}, status=400)
        login = normalize_twitch_login(values["login"])
        version = values["expected_version"]
        if login is None or type(version) is not int or version < 1:
            return web.json_response({"error": "invalid_settings"}, status=400)
        try:
            removed = await db.delete_viewer_filter(
                user_id, login, expected_version=version, now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_settings"}, status=400)
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        if not removed:
            return web.json_response({"error": "version_conflict"}, status=409)
        return web.json_response({"filter": None})

    async def category_search(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not (await capabilities.for_user(user_id, now=time.time())).viewer_category_alerts:
            return web.json_response({"error": "plus_required"}, status=403)
        query = values.get("query")
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 80:
            return web.json_response({"error": "invalid_query"}, status=400)
        now = time.monotonic()
        recent = search_times.setdefault(user_id, deque())
        while recent and now - recent[0] >= _SEARCH_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= _SEARCH_WINDOW_LIMIT:
            return web.json_response({"error": "rate_limited"}, status=429)
        recent.append(now)
        if twitch is None:
            return web.json_response({"error": "search_unavailable"}, status=503)
        try:
            found = await twitch.search_categories(query, limit=8)
        except Exception:
            logger.exception("Mini App category search failed")
            return web.json_response({"error": "search_unavailable"}, status=503)
        return web.json_response({"results": [
            {"id": category_id, "name": name} for category_id, name in found
        ]})

    async def category_alert(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        login = normalize_twitch_login(values.get("login"))
        enabled = values.get("enabled")
        ids = values.get("category_ids")
        version = values.get("expected_version")
        if (login is None or type(enabled) is not bool or type(version) is not int
                or version < 0 or not isinstance(ids, list) or len(ids) > 5
                or any(not isinstance(value, str) or not value.isascii()
                       or not value.isdecimal() or not 1 <= len(value) <= 32
                       or int(value) <= 0 for value in ids)
                or len(set(ids)) != len(ids)):
            return web.json_response({"error": "invalid_settings"}, status=400)
        if login not in await db.list_channels(user_id):
            return web.json_response({"error": "not_subscribed"}, status=404)
        if not (await capabilities.for_user(user_id, now=time.time())).viewer_category_alerts:
            return web.json_response({"error": "plus_required"}, status=403)
        if twitch is None and ids:
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        try:
            names = await twitch.get_categories(ids) if ids else {}
        except Exception:
            logger.exception("Mini App category lookup failed")
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        if any(value not in names for value in ids):
            return web.json_response({"error": "unknown_category"}, status=400)
        try:
            saved = await category_store.save_preference(
                user_id, login, enabled=enabled, category_ids=ids,
                category_names=[names[value] for value in ids],
                expected_version=version, now=time.time(),
            )
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        except LookupError:
            return web.json_response({"error": "not_subscribed"}, status=404)
        except ValueError:
            return web.json_response({"error": "invalid_settings"}, status=400)
        if saved is None:
            return web.json_response({"error": "version_conflict"}, status=409)
        return web.json_response({
            "enabled": saved.enabled, "effective": saved.enabled,
            "category_ids": list(saved.category_ids),
            "category_names": list(saved.category_names), "version": saved.version,
        })

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
        undo = await db.remove_channel_with_undo(user_id, login)
        if undo is None:
            return web.json_response({"error": "not_subscribed"}, status=404)
        video = await db.get_video_selection(user_id)
        return web.json_response({
            "removed": True, "login": login, **undo,
            "video_selection": {
                "version": video.version, "selected_ids": list(video.selected_ids),
                "selected_logins": list(video.selected_logins),
                "effective_ids": list(video.effective_ids), "limit": video.limit,
            },
        })

    async def undo_unfollow(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        token = values.get('undo_token')
        if set(values) != {'init_data', 'undo_token'} or not isinstance(token, str) or len(token) != 43:
            return web.json_response({'error': 'invalid_undo'}, status=400)
        try:
            result, login = await db.undo_channel_removal(user_id, token)
        except Exception:
            logger.exception('Mini App undo write failed')
            return web.json_response({'error': 'save_unavailable'}, status=503)
        if result != 'restored':
            return web.json_response({'error': result}, status=409)
        return web.json_response({'restored': True, 'login': login})

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

    async def favorite(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "login", "is_favorite"}:
            return web.json_response({"error": "invalid_settings"}, status=400)
        login = normalize_twitch_login(values["login"])
        is_favorite = values["is_favorite"]
        if login is None or type(is_favorite) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        try:
            updated = await db.set_viewer_favorite(user_id, login, is_favorite)
        except Exception:
            logger.exception("Mini App favorite write failed")
            return web.json_response({"error": "save_unavailable"}, status=503)
        if not updated:
            return web.json_response({"error": "not_subscribed"}, status=404)
        return web.json_response({"is_favorite": is_favorite})

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
        current = await db.get_video_selection(user_id)
        saved_ids = dict(zip(current.selected_logins, current.selected_ids))
        if twitch is None and any(login not in saved_ids for login in normalized):
            return web.json_response({"error": "lookup_unavailable"}, status=503)
        try:
            choices = [
                (saved_ids[login] if login in saved_ids else await twitch.get_user_id(login), login)
                for login in normalized
            ]
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

    async def set_reminder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "login", "delay_minutes"}:
            return web.json_response({"error": "invalid_reminder"}, status=400)
        login = normalize_twitch_login(values.get("login"))
        delay = values.get("delay_minutes")
        if login is None or type(delay) is not int or delay not in (15, 30):
            return web.json_response({"error": "invalid_reminder"}, status=400)
        now = time.time()
        if not await db.has_viewer_plus(user_id, now=now):
            return web.json_response({"error": "plus_required"}, status=403)
        cursor = await db.conn.execute(
            "SELECT last_broadcaster_id,last_stream_id FROM tracked_channels "
            "WHERE chat_id=? AND twitch_login=?", (user_id, login),
        )
        identity = await cursor.fetchone()
        if identity is None or not identity[0] or not identity[1]:
            return web.json_response({"error": "live_unavailable"}, status=409)
        try:
            saved = await reminders.set_reminder(
                user_id, identity[0], identity[1], delay, now=now,
            )
        except ReminderInFlightError:
            return web.json_response({"error": "reminder_in_flight"}, status=409)
        except PermissionError:
            return web.json_response({"error": "live_unavailable"}, status=409)
        except ValueError:
            return web.json_response({"error": "invalid_reminder"}, status=400)
        return web.json_response({"reminder": reminder_payload(saved)})

    async def create_folder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "name"}:
            return web.json_response({"error": "invalid_folder"}, status=400)
        try:
            saved = await folders.create(user_id, values["name"], now=time.time())
        except ValueError:
            return web.json_response({"error": "invalid_folder"}, status=400)
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        except FolderNameTaken:
            return web.json_response({"error": "folder_name_taken"}, status=409)
        except FolderConflict:
            return web.json_response({"error": "folder_conflict"}, status=409)
        except FolderLimit:
            return web.json_response({"error": "folder_limit"}, status=409)
        return web.json_response({"folder": folder_payload(saved)})

    async def viewer_history(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not set(values) <= {"init_data", "limit", "before_id"} or "init_data" not in values:
            return web.json_response({"error": "invalid_history_page"}, status=400)
        try:
            page = await history.list_events(
                user_id, before_id=values.get("before_id"),
                limit=values.get("limit", 20), now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_history_page"}, status=400)
        except PermissionError:
            return web.json_response({"error": "plus_required"}, status=403)
        return web.json_response({
            "events": [
                {
                    "id": event.id, "kind": event.kind, "login": event.login,
                    "logical_stream_id": event.logical_stream_id,
                    "category_name": event.category_name,
                    "outcome": event.outcome,
                    "happened_at": event.happened_at,
                }
                for event in page.events
            ],
            "next_before_id": page.next_before_id,
        })

    async def rename_folder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "folder_id", "name", "expected_version"}:
            return web.json_response({"error": "invalid_folder"}, status=400)
        if type(values["expected_version"]) is not int or values["expected_version"] < 1:
            return web.json_response({"error": "invalid_folder"}, status=400)
        try:
            saved = await folders.rename(
                user_id, values["folder_id"], values["name"],
                expected_version=values["expected_version"], now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_folder"}, status=400)
        except PermissionError:
            return web.json_response({"error": "folder_denied"}, status=403)
        except FolderNameTaken:
            return web.json_response({"error": "folder_name_taken"}, status=409)
        except FolderConflict:
            return web.json_response({"error": "folder_conflict"}, status=409)
        return web.json_response({"folder": folder_payload(saved)})

    async def folder_rule(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "folder_id", "expected_version", "games", "title_keywords", "exclude_keywords"}:
            return web.json_response({"error": "invalid_folder"}, status=400)
        if type(values["expected_version"]) is not int or values["expected_version"] < 1:
            return web.json_response({"error": "invalid_folder"}, status=400)
        try:
            saved = await folders.save_rule(
                user_id, values["folder_id"],
                expected_version=values["expected_version"],
                games=values["games"], title_keywords=values["title_keywords"],
                exclude_keywords=values["exclude_keywords"], now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_folder"}, status=400)
        except PermissionError:
            return web.json_response({"error": "folder_denied"}, status=403)
        except FolderConflict:
            return web.json_response({"error": "folder_conflict"}, status=409)
        return web.json_response({"folder": folder_payload(saved)})

    async def move_folder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "login", "folder_id", "expected_folder_id"}:
            return web.json_response({"error": "invalid_folder"}, status=400)
        login = normalize_twitch_login(values["login"])
        if login is None:
            return web.json_response({"error": "invalid_folder"}, status=400)
        try:
            folder_id = await folders.move(
                user_id, login, values["folder_id"],
                expected_folder_id=values["expected_folder_id"], now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_folder"}, status=400)
        except PermissionError:
            return web.json_response({"error": "folder_denied"}, status=403)
        except FolderConflict:
            return web.json_response({"error": "folder_conflict"}, status=409)
        return web.json_response({"folder_id": folder_id})

    async def delete_folder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "folder_id", "expected_version"}:
            return web.json_response({"error": "invalid_folder"}, status=400)
        if type(values["expected_version"]) is not int or values["expected_version"] < 1:
            return web.json_response({"error": "invalid_folder"}, status=400)
        try:
            await folders.delete(
                user_id, values["folder_id"],
                expected_version=values["expected_version"], now=time.time(),
            )
        except ValueError:
            return web.json_response({"error": "invalid_folder"}, status=400)
        except PermissionError:
            return web.json_response({"error": "folder_denied"}, status=403)
        except FolderConflict:
            return web.json_response({"error": "folder_conflict"}, status=409)
        return web.json_response({"deleted": True})

    async def cancel_reminder(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data", "login"}:
            return web.json_response({"error": "invalid_reminder"}, status=400)
        login = normalize_twitch_login(values.get("login"))
        if login is None:
            return web.json_response({"error": "invalid_reminder"}, status=400)
        try:
            cancelled = await reminders.cancel_reminder(user_id, login, now=time.time())
        except ReminderInFlightError:
            return web.json_response({"error": "reminder_in_flight"}, status=409)
        if not cancelled:
            return web.json_response({"error": "reminder_not_scheduled"}, status=404)
        saved = (await reminders.for_user(user_id))[login]
        return web.json_response({"reminder": reminder_payload(saved)})

    app.router.add_post("/app/api/viewer/state", state)
    app.router.add_post("/app/api/viewer/search", search)
    app.router.add_post("/app/api/viewer/follow", follow)
    app.router.add_post("/app/api/viewer/unfollow", unfollow)
    app.router.add_post("/app/api/viewer/unfollow/undo", undo_unfollow)
    app.router.add_post("/app/api/viewer/notify", notify)
    app.router.add_post("/app/api/viewer/favorite", favorite)
    app.router.add_post("/app/api/viewer/filter", save_filter)
    app.router.add_post("/app/api/viewer/filter/reset", reset_filter)
    app.router.add_post("/app/api/viewer/category-search", category_search)
    app.router.add_post("/app/api/viewer/category-alert", category_alert)
    app.router.add_post("/app/api/viewer/video-selection", video_selection)
    app.router.add_post("/app/api/viewer/plan-activate", plan_activate)
    app.router.add_post("/app/api/viewer/reminder", set_reminder)
    app.router.add_post("/app/api/viewer/reminder/cancel", cancel_reminder)
    app.router.add_post("/app/api/viewer/folder/create", create_folder)
    app.router.add_post("/app/api/viewer/folder/rename", rename_folder)
    app.router.add_post("/app/api/viewer/folder/rule", folder_rule)
    app.router.add_post("/app/api/viewer/folder/move", move_folder)
    app.router.add_post("/app/api/viewer/folder/delete", delete_folder)
    app.router.add_post("/app/api/viewer/history", viewer_history)
    app.router.add_post("/app/api/viewer/quiet-hours", quiet_hours)
    app.router.add_post("/app/api/viewer/digest", digest)
