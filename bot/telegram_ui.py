"""Shared Telegram navigation. UI drafts never authorize domain mutations."""
from __future__ import annotations

import time
import secrets
from aiogram.enums import ChatType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

HOME_TEXT = "<b>Оповещения о Twitch</b>\n\nСледи за стримерами или подключи свой канал."


def own_private(message, actor_id=None):
    actor_id = actor_id if actor_id is not None else (message.from_user.id if message.from_user else None)
    return message.chat.type == ChatType.PRIVATE and actor_id == message.chat.id


def menu_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Меню")]],
                               resize_keyboard=True, is_persistent=True, one_time_keyboard=False)


def home_keyboard(chat_type, *, app_url=None):
    app = (InlineKeyboardButton(text="Открыть приложение", style="primary", web_app=WebAppInfo(url=app_url))
           if chat_type == ChatType.PRIVATE and app_url and app_url.endswith('/app')
           else InlineKeyboardButton(text="Открыть приложение", style="primary", callback_data="menu:open_app"))
    return InlineKeyboardMarkup(inline_keyboard=[[app],
        [InlineKeyboardButton(text="➕ Добавить оповещения", style="success", callback_data="menu:add")],
        [InlineKeyboardButton(text="🎥 Я стример", callback_data="menu:streamer")],
        [InlineKeyboardButton(text="Ещё", callback_data="menu:more")]])


def more_keyboard(*, admin_url=None, legacy_viewer_url=None):
    pairs = [("📡 Мои стримеры","menu:list"),("🔴 Сейчас в эфире","menu:live"),
             ("🔔 Настройки","menu:quiet_hours"),("💬 Telegram-каналы","menu:manage_group"),
             ("⭐ Тариф","menu:plus"),("❓ Помощь","menu:help")]
    rows = [[InlineKeyboardButton(text=t, callback_data=c) for t,c in pairs[i:i+2]]
            for i in range(0,len(pairs),2)]
    rows.append([InlineKeyboardButton(text="📊 Отчёты",callback_data="menu:report")])
    if legacy_viewer_url:
        rows.append([InlineKeyboardButton(text="Мои оповещения", web_app=WebAppInfo(url=legacy_viewer_url))])
    if admin_url:
        rows.append([InlineKeyboardButton(text="🛡 Админ-панель", web_app=WebAppInfo(url=admin_url))])
    rows.append([InlineKeyboardButton(text="← На главную", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(target="menu:more", text="← Назад"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text,callback_data=target)]])


async def cancel_ui(state, *, actor_id, db=None, oauth_server=None, message=None,
                    preserve_community_intent=None):
    if message is not None:
        from .telegram_lists import discard_deletions
        discard_deletions(message, actor_id)
    data = await state.get_data()
    # Clear first: any late legacy OAuth/import result sees a different generation.
    await state.clear()
    cancel_channel=(data.get('telegram_community_intent') is not None
                    and data['telegram_community_intent']!=preserve_community_intent)
    restore=cancel_channel and message is not None and own_private(message,actor_id)
    if restore:
        from .telegram_home import store_for,send_menu_keyboard
        store=store_for(message)
        if store: store.keyboard_unknown(message.chat.id)
    try:
        if data.get('legacy_oauth_state') and oauth_server is not None:
            oauth_server.discard_state(data['legacy_oauth_state'])
        if data.get('telegram_oauth_intent') and oauth_server is not None:
            await oauth_server.cancel_streamer_connect_intent(actor_id, data['telegram_oauth_intent'])
        if cancel_channel and db is not None:
            await db.cancel_community_intent(data['telegram_community_intent'], actor_id)
    except Exception:
        if restore:
            try:
                await send_menu_keyboard(message,'Не удалось завершить отмену подключения. Можно вернуться кнопкой «Меню».',force=True,state=state)
            except Exception:
                # Preserve the original cleanup error; the delivery remains unknown.
                pass
        raise
    else:
        if restore:
            await send_menu_keyboard(message,'Выбор канала отменён.',force=True,state=state)
    return data


async def begin_legacy_oauth(state, *, actor_id, db=None, oauth_server=None, message=None):
    if state is None: return None
    await cancel_ui(state, actor_id=actor_id, db=db, oauth_server=oauth_server, message=message)
    generation=secrets.token_hex(16)
    await state.update_data(legacy_oauth_generation=generation)
    return generation


async def legacy_oauth_current(state,generation):
    return state is None or (await state.get_data()).get('legacy_oauth_generation')==generation
