"""Fail-closed Telegram community permission check for Streamer Plus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedCommunity:
    chat_id: int
    chat_type: str
    title: str


async def verify_community_permission(bot, chat_id: int, telegram_user_id: int) -> VerifiedCommunity | None:
    if type(chat_id) is not int or chat_id >= 0 or type(telegram_user_id) is not int or telegram_user_id <= 0:
        return None
    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_id), timeout=5)
        chat_type = getattr(chat.type, "value", chat.type)
        if chat_type not in {"group", "supergroup", "channel"}:
            return None
        user = await asyncio.wait_for(bot.get_chat_member(chat_id, telegram_user_id), timeout=5)
        bot_member = await asyncio.wait_for(bot.get_chat_member(chat_id, bot.id), timeout=5)
        if user.status not in {"creator", "administrator"}:
            return None
        if bot_member.status not in {"creator", "administrator"}:
            return None
        if chat_type == "channel" and (
            bot_member.can_post_messages is not True
            or bot_member.can_edit_messages is not True
        ):
            return None
        title = getattr(chat, "title", None)
        return VerifiedCommunity(
            chat_id, chat_type,
            title.strip()[:100] if isinstance(title, str) and title.strip() else "Сообщество",
        )
    except Exception:
        # Telegram may be unavailable or bot may have lost access. Never infer rights.
        return None
