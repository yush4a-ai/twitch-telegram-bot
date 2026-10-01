"""Signed Streamer Plus editor, local post example and confirmed delivery stats."""

from __future__ import annotations

import time
from html.parser import HTMLParser

from aiohttp import web

from .database import Database
from .live_post import LivePostContent
from .mini_app_auth import verified_payload
from .streamer_community import verify_community_permission
from .streamer_post import compose_streamer_post
from .streamer_template import validate_streamer_template


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(markup: str) -> str:
    parser = _PlainText()
    parser.feed(markup)
    return "".join(parser.parts)


def install_mini_app_streamer_plus_routes(
    app: web.Application, db: Database, bot_token: str, bot=None,
    *, bot_username: str = "",
) -> None:
    async def read(request: web.Request):
        user_id, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return None, None, web.json_response({"error": "unauthorized"}, status=status)
        return user_id, values, None

    async def placement(user_id: int, values: dict):
        chat_id = values.get("chat_id")
        if type(chat_id) is not int or chat_id >= 0:
            return None, web.json_response({"error": "invalid_chat"}, status=400)
        identity = await db.get_streamer_identity(user_id)
        if identity is None:
            return None, web.json_response({"error": "not_linked"}, status=403)
        stored = await db.list_streamer_communities(user_id)
        community = next((row for row in stored if row[0] == chat_id), None)
        if community is None:
            return None, web.json_response({"error": "placement_denied"}, status=403)
        if bot is None or await verify_community_permission(bot, chat_id, user_id) is None:
            return None, web.json_response({"error": "permission_denied"}, status=403)
        return (chat_id, identity[1], community[2]), None

    async def template(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        own, error = await placement(user_id, values)
        if error is not None:
            return error
        chat_id, _login, _kind = own
        plus = await db.has_streamer_plus(user_id)
        if values.get("headline") is None:
            saved = await db.get_streamer_template(user_id, chat_id)
            version, headline, body, buttons = saved if saved else (0, "", "", [])
            return web.json_response({
                "version": version, "headline": headline, "body": body,
                "buttons": buttons, "can_edit": plus,
            })
        if not plus:
            return web.json_response({"error": "plus_required"}, status=403)
        try:
            version = await db.save_streamer_template(
                user_id, chat_id, expected_version=values.get("version"),
                headline=values.get("headline"), body=values.get("body"),
                buttons=values.get("buttons"),
            )
        except (ValueError, TypeError, UnicodeError):
            return web.json_response({"error": "invalid_template"}, status=400)
        if version is None:
            if not await db.has_streamer_plus(user_id):
                return web.json_response({"error": "plus_required"}, status=403)
            return web.json_response({"error": "stale_template"}, status=409)
        return web.json_response({"version": version})

    async def post_example(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        own, error = await placement(user_id, values)
        if error is not None:
            return error
        chat_id, login, kind = own
        plus = await db.has_streamer_plus(user_id)
        # Poller imports OAuth through token storage; load it after route setup.
        from .poller import StreamPoller
        composer = StreamPoller(bot, db, None, 60, bot_username=bot_username)
        is_channel = kind == "channel"
        base = LivePostContent(
            html=await composer._build_live_text(
                login, "Название трансляции", "Категория Twitch", 42, None,
                include_track_link=is_channel, is_channel=is_channel,
            ),
            reply_markup=await composer._build_keyboard(login),
        )
        content = base
        if plus:
            saved = await db.get_streamer_template(user_id, chat_id)
            if saved:
                try:
                    custom = validate_streamer_template(saved[1], saved[2], saved[3])
                    content = compose_streamer_post(base, custom)
                except (ValueError, TypeError, UnicodeError):
                    pass
        buttons = [
            {"label": button.text, "url": button.url}
            for row in content.reply_markup.inline_keyboard for button in row
            if button.url
        ] if content.reply_markup else []
        return web.json_response({
            "text": _plain_text(content.html), "buttons": buttons,
            "custom_active": content is not base,
            "animation_available": plus,
            "animation_enabled": plus and await db.get_preview_enabled(chat_id, login),
            "example_only": True,
        })

    async def preview(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        own, error = await placement(user_id, values)
        if error is not None:
            return error
        chat_id, login, _kind = own
        enabled = values.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid_preview"}, status=400)
        if enabled and not await db.has_streamer_plus(user_id):
            return web.json_response({"error": "plus_required"}, status=403)
        if await db.get_live_post_state(chat_id, login) is None:
            return web.json_response({"error": "publication_disabled"}, status=409)
        await db.set_preview_enabled(chat_id, login, enabled)
        return web.json_response({"enabled": enabled})

    async def stats(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        if await db.get_streamer_identity(user_id) is None:
            return web.json_response({"error": "not_linked"}, status=403)
        if not await db.has_streamer_plus(user_id):
            return web.json_response({"error": "plus_required"}, status=403)
        values = await db.get_streamer_delivery_stats(user_id, since=time.time() - 30 * 86400)
        return web.json_response({"period_days": 30, **values})

    app.router.add_post("/app/api/streamer/template", template)
    app.router.add_post("/app/api/streamer/post-example", post_example)
    app.router.add_post("/app/api/streamer/preview", preview)
    app.router.add_post("/app/api/streamer/stats", stats)
