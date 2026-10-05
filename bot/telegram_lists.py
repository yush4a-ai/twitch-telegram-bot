"""Bounded actor-owned navigation for native lists; domain rights stay in streams."""
from __future__ import annotations

import html
import math
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from types import SimpleNamespace

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard

from .telegram_home import edit_menu
from .telegram_ui import cancel_ui, own_private

PAGE_SIZE = 8
_contexts = OrderedDict()
_deletes = OrderedDict()


class ListSearch(StatesGroup):
    waiting = State()


def actor_key(message, actor):
    return (getattr(getattr(message, 'bot', None), 'id', 0), message.chat.id, actor)


@dataclass
class ListContext:
    token: str
    owner: tuple
    target: int
    back: str
    query: str = ''
    live_only: bool = False
    page: int = 0
    expires: float = 0

    def page_callback(self, page=None):
        return f'listpage:{self.target}:{self.token}:{self.page if page is None else page}'


def put(store, token, value):
    store[token] = value
    store.move_to_end(token)
    while len(store) > 1024:
        store.popitem(last=False)


def discard_deletions(message, actor):
    owner = actor_key(message, actor)
    for token, intent in list(_deletes.items()):
        if intent[0] == owner:
            _deletes.pop(token, None)


def new_context(callback, target, *, back=None):
    token = secrets.token_hex(6)
    ctx = ListContext(token, actor_key(callback.message, callback.from_user.id), target,
                      back or ('menu:more' if callback.message.chat.id == target else 'menu:manage_group'),
                      expires=time.monotonic()+1800)
    put(_contexts, token, ctx)
    return ctx


def get_context(message, actor, token, target):
    ctx = _contexts.get(token)
    if ctx and ctx.expires <= time.monotonic():
        _contexts.pop(token, None)
        return None
    if ctx and ctx.owner == actor_key(message, actor) and ctx.target == target:
        _contexts.move_to_end(token)
        return ctx
    return None


async def can_read(callback, target):
    from .handlers.streams import _check_read_permission
    if callback.message is None:
        return False
    if callback.message.chat.type == 'private' and not own_private(callback.message, callback.from_user.id):
        return False
    if target > 0 and target != callback.message.chat.id:
        return False
    return await _check_read_permission(callback, target)


async def context_title(db, target):
    if target > 0:
        return '<b>Личные оповещения</b>'
    row = await (await db.conn.execute('SELECT title FROM telegram_channels WHERE chat_id=?', (target,))).fetchone()
    if row:
        return f'<b>Публикации в канале «{html.escape(row[0][:100])}»</b>'
    row = await (await db.conn.execute('SELECT title,chat_type FROM streamer_communities WHERE chat_id=? LIMIT 1', (target,))).fetchone()
    if row:
        kind = 'канале' if row[1] == 'channel' else 'группе'
        return f'<b>Публикации в {kind} «{html.escape(row[0][:100])}»</b>'
    return f'<b>Оповещения сообщества {target}</b>'


async def render_context(ctx, db, *, allow_add=False):
    channels = await db.list_channels_with_routing(ctx.target)
    rows = [row for row in channels if (not ctx.query or ctx.query.casefold() in row[0].casefold())
            and (not ctx.live_only or row[-1])]
    pages = max(1, math.ceil(len(rows)/PAGE_SIZE))
    ctx.page = min(max(0, ctx.page), pages-1)
    text = await context_title(db, ctx.target) + f'\n\n<blockquote>В списке: {len(channels)} · Найдено: {len(rows)}\nСтраница {ctx.page+1} из {pages}</blockquote>'
    if ctx.query:
        text += '\nПоиск: ' + html.escape(ctx.query)
    if ctx.live_only:
        text += '\nПоказаны эфиры по последней проверке.'
    if not rows:
        text += '\n\nСтримеров пока нет. Добавьте первого.' if not channels else '\n\nСовпадений нет. Измените поиск или выберите «Все».'
    else:
        text += '\n\nВыберите стримера, чтобы настроить оповещения.'
    keyboard = []
    for row in rows[ctx.page*PAGE_SIZE:(ctx.page+1)*PAGE_SIZE]:
        login, enabled, *rest = row
        status = ('Эфир · ' if row[-1] else '') + ('оповещения вкл' if enabled else 'оповещения выкл')
        keyboard.append([Button(text=f'{login} · {status}', callback_data=f'channelcard:{ctx.target}:{login}')])
    keyboard.append([Button(text='Все', style='primary' if not ctx.live_only else None,
                            callback_data=f'listfilter:{ctx.target}:{ctx.token}:all'),
                     Button(text='В эфире', style='primary' if ctx.live_only else None,
                            callback_data=f'listfilter:{ctx.target}:{ctx.token}:live')])
    pager = []
    if ctx.page:
        pager.append(Button(text='← Раньше', callback_data=ctx.page_callback(ctx.page-1)))
    pager.append(Button(text=f'{ctx.page+1} / {pages}', callback_data=ctx.page_callback()))
    if ctx.page+1 < pages:
        pager.append(Button(text='Дальше →', callback_data=ctx.page_callback(ctx.page+1)))
    keyboard.append(pager)
    keyboard.append([Button(text='Поиск по нику', callback_data=f'listsearch:{ctx.target}:{ctx.token}')])
    if ctx.query:
        keyboard.append([Button(text='Сбросить поиск', callback_data=f'listfilter:{ctx.target}:{ctx.token}:reset')])
    if allow_add:
        keyboard.append([Button(text='➕ Добавить оповещения', callback_data=f'menu:add:{ctx.target}')])
    keyboard.append([Button(text='← Назад', callback_data=ctx.back)])
    return text, Keyboard(inline_keyboard=keyboard)


async def show_context(callback, db, ctx):
    from .handlers.streams import _check_manage_permission
    if not await can_read(callback, ctx.target):
        await callback.answer('Нет доступа к этому чату.', show_alert=True)
        return
    text, keyboard = await render_context(ctx, db, allow_add=await _check_manage_permission(callback, ctx.target))
    await edit_menu(callback.message, text, reply_markup=keyboard)
    await callback.answer()


def source_from_message(callback, target):
    markup = getattr(callback.message, 'reply_markup', None)
    for row in getattr(markup, 'inline_keyboard', []) or []:
        for button in row:
            parts = (button.callback_data or '').split(':')
            if len(parts) == 4 and parts[0] == 'listpage' and parts[1] == str(target):
                ctx = get_context(callback.message, callback.from_user.id, parts[2], target)
                if ctx:
                    return ctx
    return new_context(callback, target)


async def callback_context(callback):
    parts = (callback.data or '').split(':')
    try:
        target = int(parts[1])
        ctx = get_context(callback.message, callback.from_user.id, parts[2], target)
    except (ValueError, IndexError, AttributeError):
        ctx = None
    if ctx is None:
        await callback.answer('Список устарел. Откройте «Мои стримеры» заново.', show_alert=True)
    return ctx, parts


async def cb_list_page(callback, db, state=None, oauth_server=None):
    ctx, parts = await callback_context(callback)
    if ctx is None:
        return
    try:
        page = int(parts[3])
        if len(parts) != 4 or not 0 <= page <= 10000:
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer('Страница недоступна.', show_alert=True)
        return
    discard_deletions(callback.message, callback.from_user.id)
    if state is not None:
        await cancel_ui(state, actor_id=callback.from_user.id, db=db, oauth_server=oauth_server, message=callback.message)
    ctx.page = page
    await show_context(callback, db, ctx)


async def cb_list_filter(callback, db, state=None, oauth_server=None):
    ctx, parts = await callback_context(callback)
    if ctx is None:
        return
    if len(parts) != 4 or parts[3] not in {'all','live','reset'}:
        await callback.answer('Фильтр недоступен.', show_alert=True)
        return
    discard_deletions(callback.message, callback.from_user.id)
    if state is not None:
        await cancel_ui(state, actor_id=callback.from_user.id, db=db, oauth_server=oauth_server, message=callback.message)
    if parts[3] == 'reset':
        ctx.query = ''
    else:
        ctx.live_only = parts[3] == 'live'
    ctx.page = 0
    await show_context(callback, db, ctx)


async def cb_list_search(callback, state, db, oauth_server=None):
    ctx, parts = await callback_context(callback)
    if ctx is None or not await can_read(callback, ctx.target):
        return
    await cancel_ui(state, actor_id=callback.from_user.id, db=db, oauth_server=oauth_server, message=callback.message)
    await state.set_state(ListSearch.waiting)
    await state.update_data(list_token=ctx.token, list_target=ctx.target)
    await edit_menu(callback.message, '<b>Поиск в списке</b>\n\nПришлите ник или его часть.\n\n<blockquote>Поиск только по этому списку, без добавления стримеров.</blockquote>',
                    reply_markup=Keyboard(inline_keyboard=[[Button(text='Отменить', callback_data=ctx.page_callback())]]))
    await callback.answer()


async def process_list_search(message, state, db):
    data = await state.get_data()
    actor = message.from_user.id if message.from_user else None
    ctx = get_context(message, actor, data.get('list_token'), data.get('list_target'))
    if ctx is None:
        await state.clear()
        await message.answer('Поиск отменён. Откройте «Мои стримеры» заново.')
        return
    proxy = SimpleNamespace(message=message, from_user=message.from_user, bot=message.bot)
    if not await can_read(proxy, ctx.target):
        await state.clear()
        return
    query = (message.text or '').strip()
    if not 1 <= len(query) <= 25 or not all(c.isascii() and (c.isalnum() or c == '_') for c in query):
        await message.answer('Пришлите до 25 букв, цифр или подчёркиваний из Twitch-ника.')
        return
    ctx.query, ctx.page = query, 0
    await state.clear()
    from .handlers.streams import _check_manage_permission
    text, keyboard = await render_context(ctx, db, allow_add=await _check_manage_permission(proxy, ctx.target))
    await message.answer(text, reply_markup=keyboard)


async def ask_delete(callback, db, target, login):
    discard_deletions(callback.message, callback.from_user.id)
    ctx = source_from_message(callback, target)
    token = secrets.token_hex(8)
    put(_deletes, token, (ctx.owner, target, login, ctx.token, time.monotonic()+600))
    text = await context_title(db, target) + f'\n\nУдалить <b>{html.escape(login)}</b> из этого списка?\n\n<blockquote>Оповещения и настройки этого стримера будут удалены.</blockquote>'
    await edit_menu(callback.message, text, reply_markup=Keyboard(inline_keyboard=[
        [Button(text=f'Удалить {login}', style='danger', callback_data='untrackconfirm:'+token)],
        [Button(text='Отменить', callback_data=ctx.page_callback())]]))
    await callback.answer()


async def cb_delete_confirm(callback, db):
    from .handlers.streams import _check_manage_permission
    token = (callback.data or '').removeprefix('untrackconfirm:')
    intent = _deletes.get(token)
    if not intent or callback.message is None or intent[0] != actor_key(callback.message, callback.from_user.id) or intent[4] <= time.monotonic():
        await callback.answer('Подтверждение устарело. Откройте список заново.', show_alert=True)
        return
    if not await _check_manage_permission(callback, intent[1]):
        await callback.answer('Настройки этого чата вам недоступны.', show_alert=True)
        return
    # Permission lookup yields: Cancel/Menu or another confirmation may claim it.
    claimed = _deletes.pop(token, None)
    if claimed is not intent or intent[4] <= time.monotonic():
        await callback.answer('Подтверждение устарело. Откройте список заново.', show_alert=True)
        return
    await db.remove_channel(intent[1], intent[2])
    ctx = get_context(callback.message, callback.from_user.id, intent[3], intent[1]) or new_context(callback, intent[1])
    await show_context(callback, db, ctx)
