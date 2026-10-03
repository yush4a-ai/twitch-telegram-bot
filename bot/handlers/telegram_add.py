"""Confirmed personal tracking over the existing atomic tracking service."""
from ..telegram_home import edit_menu
import html
import secrets
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..telegram_ui import own_private, back_keyboard
from .streams import _extract_login_text, _is_valid_login, _tracking_limit


def result_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить ещё",callback_data="menu:add")],
        [InlineKeyboardButton(text="Мои стримеры",callback_data="menu:list")],
        [InlineKeyboardButton(text="На главную",callback_data="menu:home")]])


def current(data, token):
    return data.get('confirm_add') and data.get('add_query_token') == token and data.get('add_expires_at',0) > time.time()


async def show_found(message, state, login, display_name, token, *, edit=False):
    data = await state.get_data()
    if not current(data,token): return
    confirmation = secrets.token_hex(8)
    await state.update_data(add_confirm_token=confirmation,add_login=login)
    send = (lambda *args, **kwargs: edit_menu(message,*args,**kwargs)) if edit else message.answer
    await send(f"Найден стример: <b>{html.escape(display_name)}</b>\nTwitch: {html.escape(login)}\n\nДобавить его?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Добавить",callback_data='addconfirm:'+confirmation)],
            [InlineKeyboardButton(text="Отменить",callback_data='menu:home')]]))


async def process_confirmed_input(message, state, db, twitch):
    if not own_private(message): return
    token = secrets.token_hex(8)
    await state.update_data(add_query_token=token, add_confirm_token=None,add_candidates={},add_expires_at=time.time()+600)
    query = (message.text or '').strip()[:300]
    login = _extract_login_text(query)
    tracked = await db.list_channels(message.chat.id)
    if login in tracked:
        await state.clear()
        await message.answer(f"Стример «{html.escape(login)}» уже в твоём списке.",reply_markup=result_keyboard())
        return
    limit = await _tracking_limit(db,message.chat.id)
    if len(tracked) >= limit:
        await state.clear()
        await message.answer(f"В списке уже {limit} стримеров. Удали одного, чтобы добавить нового.",reply_markup=result_keyboard())
        return
    try:
        exists = await twitch.channel_exists(login) if login else False
        if not current(await state.get_data(),token): return
        if exists:
            await show_found(message,state,login,login,token)
            return
        found = await twitch.search_channels(query) if len(query)>=3 else []
    except Exception:
        if current(await state.get_data(),token):
            await message.answer("Twitch пока не отвечает. Попробуй ещё раз.",reply_markup=back_keyboard('menu:home'))
        return
    if not current(await state.get_data(),token): return
    candidates = {r.login:str(r.display_name)[:100] for r in found[:6] if _is_valid_login(r.login)}
    await state.update_data(add_candidates=candidates)
    if not candidates:
        await message.answer("Стример не найден. Проверь ник или пришли другую ссылку Twitch.",reply_markup=back_keyboard('menu:home'))
        return
    rows = [[InlineKeyboardButton(text=name[:100],callback_data=f'addpick:{token}:{login}')] for login,name in candidates.items()]
    rows.append([InlineKeyboardButton(text="← На главную",callback_data='menu:home')])
    await message.answer("Выбери стримера, затем подтверди добавление.",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def cb_pick_add(callback,state):
    if callback.message is None or not own_private(callback.message,callback.from_user.id):
        await callback.answer("Открой свой личный чат с ботом.",show_alert=True); return
    parts = (callback.data or '').split(':')
    data = await state.get_data()
    if len(parts)!=3 or not current(data,parts[1]) or parts[2] not in data.get('add_candidates',{}):
        await callback.answer("Выбор устарел. Добавь стримера заново.",show_alert=True); return
    await show_found(callback.message,state,parts[2],data['add_candidates'][parts[2]],parts[1],edit=True)
    await callback.answer()


async def cb_confirm_add(callback,state,db):
    if callback.message is None or not own_private(callback.message,callback.from_user.id):
        await callback.answer("Открой свой личный чат с ботом.",show_alert=True); return
    data = await state.get_data()
    token = (callback.data or '').removeprefix('addconfirm:')
    login = data.get('add_login')
    if (not data.get('confirm_add') or not isinstance(login,str) or not _is_valid_login(login)
        or token != data.get('add_confirm_token') or data.get('add_expires_at',0)<=time.time()):
        await callback.answer("Подтверждение устарело. Добавь стримера заново.",show_alert=True); return
    # This explicit confirmation claims the UI draft once; Menu itself never inserts.
    await state.clear()
    limit = await _tracking_limit(db,callback.from_user.id)
    result = await db.add_channel_with_limit(callback.from_user.id,login,limit)
    text = ("Готово. Я сообщу, когда стример начнёт эфир." if result=='created'
            else f"В списке уже {limit} стримеров. Удали одного, чтобы добавить нового." if result=='limit'
            else f"Стример «{html.escape(login)}» уже в твоём списке.")
    await edit_menu(callback.message,text,reply_markup=result_keyboard())
    await callback.answer()
