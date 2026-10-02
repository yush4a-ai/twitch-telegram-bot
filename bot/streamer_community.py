"""Fail-closed Telegram community permission check for Streamer Plus."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from aiogram.exceptions import TelegramForbiddenError


@dataclass(frozen=True)
class VerifiedCommunity:
    chat_id: int
    chat_type: str
    title: str


@dataclass(frozen=True)
class CommunityPermissionResult:
    status: str
    community: VerifiedCommunity | None = None
    public_url: str | None = None


async def check_community_permission(bot, chat_id: int, telegram_user_id: int) -> CommunityPermissionResult:
    if type(chat_id) is not int or chat_id >= 0 or type(telegram_user_id) is not int or telegram_user_id <= 0:
        return CommunityPermissionResult("user_denied")
    try:
        async with asyncio.timeout(5):
            chat = await bot.get_chat(chat_id)
            if getattr(chat, "id", chat_id) != chat_id:
                return CommunityPermissionResult("network_error")
            chat_type = getattr(chat.type, "value", chat.type)
            if chat_type not in {"group", "supergroup", "channel"}:
                return CommunityPermissionResult("wrong_chat_type")
            user = await bot.get_chat_member(chat_id, telegram_user_id)
            if user.status not in {"creator", "administrator"}:
                return CommunityPermissionResult("user_denied")
            bot_member = await bot.get_chat_member(chat_id, bot.id)
            if bot_member.status in {"left", "kicked"}:
                return CommunityPermissionResult("bot_absent")
            if bot_member.status not in {"creator", "administrator"}:
                return CommunityPermissionResult("bot_member")
            if (chat_type == "channel" and bot_member.status != "creator"
                    and getattr(bot_member, "can_post_messages", None) is not True):
                return CommunityPermissionResult("missing_post_right")
        title = getattr(chat, "title", None)
        community = VerifiedCommunity(
            chat_id, chat_type,
            title.strip()[:100] if isinstance(title, str) and title.strip() else "Сообщество",
        )
        username = getattr(chat, "username", None)
        public_url = (
            f"https://t.me/{username}" if chat_type == "channel"
            and getattr(chat, "id", None) == chat_id and isinstance(username, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username) else None
        )
        return CommunityPermissionResult("ready", community, public_url)
    except TelegramForbiddenError:
        return CommunityPermissionResult("bot_absent")
    except Exception:
        return CommunityPermissionResult("network_error")


async def verify_community_permission(bot, chat_id: int, telegram_user_id: int) -> VerifiedCommunity | None:
    """Legacy callers retain the fail-closed VerifiedCommunity/None interface."""
    return (await check_community_permission(bot, chat_id, telegram_user_id)).community
