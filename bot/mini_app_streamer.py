"""Signed Mini App streamer onboarding over the existing bot and database."""

from __future__ import annotations

import asyncio
import secrets
import time

from aiohttp import web
from aiogram.types import KeyboardButton, KeyboardButtonRequestChat

from .database import Database
from .mini_app_auth import verified_payload
from .mini_app_streamer_plus import install_mini_app_streamer_plus_routes
from .streamer_community import check_community_permission, CommunityPermissionResult


async def complete_community_intent(
    db: Database, bot, telegram_user_id: int, request_id: int,
    chat_id: int, *, now: float,
) -> bool:
    """A Telegram chat_shared callback is a suggestion, not permission proof."""
    started = time.monotonic()
    claimed = await db.claim_community_intent(
        telegram_user_id, request_id, chat_id, now=now,
    )
    if claimed is None:
        return False
    intent_id, expected_type = claimed
    result = (await check_community_permission(bot, chat_id, telegram_user_id)
              if expected_type == "channel" else CommunityPermissionResult("wrong_chat_type"))
    verified = result.community
    if verified is None or verified.chat_type != "channel":
        reason = result.status if verified is None else "wrong_chat_type"
        await db.finish_community_intent(intent_id, "failed" if reason == "network_error" else "denied",
                                        chat_id=chat_id, permission_reason=reason)
        return False
    try:
        saved = await db.add_streamer_community(
            telegram_user_id, verified.chat_id, verified.title, verified.chat_type,
            now=now, intent_id=intent_id, verification_started=started,
        )
    except ValueError:
        saved = False
    if not saved:
        current = await db.get_community_intent(intent_id)
        if current is not None and current[6] == "verifying" and current[4] > now + max(0, time.monotonic() - started):
            await db.finish_community_intent(intent_id, "failed", chat_id=chat_id)
    return saved


def install_mini_app_streamer_routes(
    app: web.Application, db: Database, bot_token: str, bot=None,
    *, bot_username: str = "", oauth_server=None,
) -> None:
    async def read(request: web.Request):
        user_id, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return None, None, web.json_response({"error": "unauthorized"}, status=status)
        return user_id, values, None

    async def profile(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        identity = await db.get_streamer_identity(user_id)
        if identity is None:
            return web.json_response({
                "connected": False, "twitch_login": None,
                "plus_active": False, "communities": [],
            })
        stored = await db.list_streamer_communities(user_id)
        semaphore = asyncio.Semaphore(3)

        async def check(row):
            async with semaphore:
                permission = (await check_community_permission(bot, row[0], user_id)
                              if bot else CommunityPermissionResult("network_error"))
                post_state = await db.get_live_post_state(row[0], identity[1])
                return permission, post_state

        checked = await asyncio.gather(*(check(row) for row in stored))
        return web.json_response({
            "connected": True, "twitch_login": identity[1],
            "plus_active": await db.has_streamer_plus(user_id),
            "communities": [
                {"chat_id": row[0], "title": row[1], "chat_type": row[2],
                 "permission_ok": result[0].community is not None,
                 "permission_status": result[0].status, "public_url": result[0].public_url,
                 "publishing": result[1] is not None and result[1].notify_enabled}
                for row, result in zip(stored, checked)
            ],
        })

    async def connect_intent(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        if await db.get_streamer_identity(user_id) is not None:
            return web.json_response({"error": "already_linked"}, status=409)
        if oauth_server is None:
            return web.json_response({"error": "connect_unavailable"}, status=503)
        try:
            intent_id, url, expires_at = await oauth_server.create_streamer_connect_intent(user_id)
        except Exception:
            return web.json_response({"error": "connect_unavailable"}, status=503)
        return web.json_response({
            "intent_id": intent_id, "authorize_url": url, "expires_at": expires_at,
        })

    async def connect_intent_status(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        intent_id = values.get("intent_id")
        if not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80:
            return web.json_response({"error": "invalid_intent"}, status=400)
        row = await db.get_streamer_connect_intent(intent_id)
        if row is None or row[1] != user_id:
            return web.json_response({"error": "intent_denied"}, status=403)
        stale = row[4] in {"pending", "verifying"} and time.time() >= row[3]
        status = "expired" if stale else row[4]
        return web.json_response({"status": status, "twitch_login": row[5] if status == "connected" else None})

    async def cancel_connect_intent(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        intent_id = values.get("intent_id")
        if not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80:
            return web.json_response({"error": "invalid_intent"}, status=400)
        if oauth_server is None or not await oauth_server.cancel_streamer_connect_intent(user_id, intent_id):
            return web.json_response({"error": "intent_denied"}, status=403)
        return web.json_response({"status": "cancelled"})

    async def community_intent(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if await db.get_streamer_identity(user_id) is None:
            return web.json_response({"error": "not_linked"}, status=403)
        chat_type = values.get("chat_type")
        if chat_type != "channel":
            return web.json_response({"error": "invalid_chat_type"}, status=400)
        intent_id = secrets.token_urlsafe(18)
        request_id = secrets.randbelow(2**31 - 1) + 1
        try:
            created = await db.create_community_intent(
                intent_id, user_id, request_id, chat_type, now=time.time(),
            )
        except Exception:
            return web.json_response({"error": "intent_unavailable"}, status=503)
        if not created:
            return web.json_response({"error": "not_linked"}, status=403)
        prepared_id = None
        if bot is not None and hasattr(bot, "save_prepared_keyboard_button"):
            button = KeyboardButton(
                text="Выбрать Telegram-канал",
                request_chat=KeyboardButtonRequestChat(
                    request_id=request_id,
                    chat_is_channel=True,
                    bot_is_member=True, request_title=True,
                ),
            )
            try:
                prepared = await bot.save_prepared_keyboard_button(
                    user_id=user_id, button=button,
                )
                prepared_id = prepared.id
                await db.set_community_prepared_id(intent_id, prepared_id)
            except Exception:
                prepared_id = None
        fallback = (
            f"https://t.me/{bot_username}?start=tscommunity_{intent_id}"
            if bot_username else None
        )
        return web.json_response({
            "intent_id": intent_id, "prepared_id": prepared_id,
            "fallback_url": fallback, "expires_at": time.time() + 600,
        })

    async def community_intent_status(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        intent_id = values.get("intent_id")
        if not isinstance(intent_id, str) or len(intent_id) > 80:
            return web.json_response({"error": "invalid_intent"}, status=400)
        row = await db.get_community_intent(intent_id)
        if row is None or row[1] != user_id:
            return web.json_response({"error": "intent_denied"}, status=403)
        stale = row[6] in {"pending", "verifying"} and time.time() >= row[4]
        status = "expired" if stale else row[6]
        return web.json_response({"status": status, "chat_id": row[8] if status == "connected" else None,
                                  "permission_reason": row[9]})

    async def cancel_community_intent(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        intent_id = values.get("intent_id")
        if not isinstance(intent_id, str) or not 16 <= len(intent_id) <= 80:
            return web.json_response({"error": "invalid_intent"}, status=400)
        if not await db.cancel_community_intent(intent_id, user_id):
            return web.json_response({"error": "intent_denied"}, status=403)
        return web.json_response({"status": "cancelled"})

    async def communities(request: web.Request) -> web.Response:
        return await profile(request)

    async def toggle_community(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        chat_id = values.get("chat_id")
        enabled = values.get("enabled")
        if type(chat_id) is not int or chat_id >= 0 or type(enabled) is not bool:
            return web.json_response({"error": "invalid_settings"}, status=400)
        identity = await db.get_streamer_identity(user_id)
        stored = await db.list_streamer_communities(user_id) if identity else []
        community = next((row for row in stored if row[0] == chat_id), None)
        if community is None:
            return web.json_response({"error": "placement_denied"}, status=403)
        login = identity[1]
        if not enabled:
            if await db.get_live_post_state(chat_id, login) is not None:
                await db.set_notify_enabled(chat_id, login, False)
            return web.json_response({"publishing": False})
        if bot is None:
            return web.json_response({"error": "verification_unavailable"}, status=503)
        result = await check_community_permission(bot, chat_id, user_id)
        if result.status == "network_error":
            return web.json_response({"error": "verification_unavailable"}, status=503)
        verified = result.community
        if verified is None or verified.chat_type != community[2]:
            return web.json_response({"error": "permission_denied"}, status=403)
        if verified.chat_type == "channel":
            await db.register_telegram_channel(chat_id, verified.title)
        result = await db.add_channel_with_limit(chat_id, login, 50)
        if result == "limit":
            return web.json_response({"error": "channel_limit"}, status=409)
        await db.set_notify_enabled(chat_id, login, True)
        return web.json_response({"publishing": True, "result": result})

    app.router.add_post("/app/api/streamer/profile", profile)
    app.router.add_post("/app/api/streamer/connect-intent", connect_intent)
    app.router.add_post("/app/api/streamer/connect-intent/status", connect_intent_status)
    app.router.add_post("/app/api/streamer/connect-intent/cancel", cancel_connect_intent)
    app.router.add_post("/app/api/streamer/community-intent", community_intent)
    app.router.add_post("/app/api/streamer/community-intent/status", community_intent_status)
    app.router.add_post("/app/api/streamer/community-intent/cancel", cancel_community_intent)
    app.router.add_post("/app/api/streamer/communities", communities)
    app.router.add_post("/app/api/streamer/communities/toggle", toggle_community)
    install_mini_app_streamer_plus_routes(
        app, db, bot_token, bot, bot_username=bot_username,
    )
