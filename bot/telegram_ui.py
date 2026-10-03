"""Shared Telegram navigation. UI drafts never authorize domain mutations."""
from __future__ import annotations

import time
import secrets
from aiogram.enums import ChatType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

HOME_TEXT = "TwitchSignalBot\n\nСледи за стримерами или подключи свой канал."


def own_private(message, actor_id=None):
    actor_id = actor_id if actor_id is not None else (message.from_user.id if message.from_user else None)
    return message.chat.type == ChatType.PRIVATE and actor_id == message.chat.id


def menu_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Меню")]],
                               resize_keyboard=True, is_persistent=True, one_time_keyboard=False)


def home_keyboard(chat_type, *, app_url=None):
    app = (InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=app_url))
           if chat_type == ChatType.PRIVATE and app_url and app_url.endswith('/app')
           else InlineKeyboardButton(text="Открыть приложение", callback_data="menu:open_app"))
    return InlineKeyboardMarkup(inline_keyboard=[[app],
        [InlineKeyboardButton(text="➕ Добавить стримера", callback_data="menu:add")],
        [InlineKeyboardButton(text="🎥 Я стример", callback_data="menu:streamer")],
        [InlineKeyboardButton(text="Ещё", callback_data="menu:more")]])


def more_keyboard(*, admin_url=None, legacy_viewer_url=None):
    pairs = [("📡 Мои стримеры","menu:list"),("🔴 Кто сейчас в эфире","menu:live"),
             ("📊 Отчёты","menu:report"),("🌙 Тихие часы","menu:quiet_hours"),
             ("💬 Мои подключения","menu:manage_group"),("⭐ Plus и подписка","menu:plus"),
             ("ℹ️ Что умеет бот","menu:about"),("❓ Помощь","menu:help")]
    rows = [[InlineKeyboardButton(text=t, callback_data=c) for t,c in pairs[i:i+2]] for i in range(0,len(pairs),2)]
    if legacy_viewer_url:
        rows.append([InlineKeyboardButton(text="Мои оповещения", web_app=WebAppInfo(url=legacy_viewer_url))])
    if admin_url:
        rows.append([InlineKeyboardButton(text="🛡 Админ-панель", web_app=WebAppInfo(url=admin_url))])
    rows.append([InlineKeyboardButton(text="← На главную", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(target="menu:more", text="← Назад"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text,callback_data=target)]])


async def cancel_ui(state, *, actor_id, db=None, oauth_server=None):
    data = await state.get_data()
    # Clear first: any late legacy OAuth/import result sees a different generation.
    await state.clear()
    if data.get('legacy_oauth_state') and oauth_server is not None:
        oauth_server.discard_state(data['legacy_oauth_state'])
    if data.get('telegram_oauth_intent') and oauth_server is not None:
        await oauth_server.cancel_streamer_connect_intent(actor_id, data['telegram_oauth_intent'])
    if data.get('telegram_community_intent') and db is not None:
        await db.cancel_community_intent(data['telegram_community_intent'], actor_id)
    return data


async def begin_legacy_oauth(state):
    if state is None: return None
    await state.clear()
    generation=secrets.token_hex(16)
    await state.update_data(legacy_oauth_generation=generation)
    return generation


async def legacy_oauth_current(state,generation):
    return state is None or (await state.get_data()).get('legacy_oauth_generation')==generation
