"""Read-only Home state and bounded, per-bot Telegram menu presentation."""
from __future__ import annotations

import asyncio
import html
import logging
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, InaccessibleMessage, FSInputFile, InputMediaPhoto

from .telegram_ui import HOME_TEXT, home_keyboard, menu_keyboard

BANNER_PATH = Path(__file__).resolve().parent / 'assets' / 'telegram-welcome.png'
HOME_CARD_PATH = BANNER_PATH.with_name('telegram-home.png')
CHANNEL_GUIDE_PATH = BANNER_PATH.with_name('telegram-channel-guide.png')
# Сообщество проекта: одна ссылка внизу главного экрана, чтобы человек знал,
# куда писать и где читать новости.
COMMUNITY_URL = 'https://t.me/signalbot_dev'
COMMUNITY_LINE = f'💬 <a href="{COMMUNITY_URL}">Сообщество проекта</a>: новости и обратная связь'
logger = logging.getLogger(__name__)
_menus = OrderedDict()


@dataclass(frozen=True)
class HomeState:
    tracked: int = 0
    live: tuple[tuple[str, str | None], ...] = ()
    verified: bool = False
    notifications: int | None = None
    live_known: bool = True
    communities: int | None = None


@dataclass(frozen=True)
class HomeView:
    text: str
    banner: bool


async def load_home_state(db, user_id: int) -> HomeState:
    """Existing own-chat queries; no Twitch/Telegram request or new database."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('invalid home user')
    channels = await db.list_channels_with_routing(user_id)
    try:
        live = await db.list_live_channels(user_id)
        live_known = True
    except Exception as error:
        logger.warning('Home live state unavailable (%s)',type(error).__name__)
        live = []
        live_known = False
    identity = await db.get_streamer_identity(user_id)
    communities = len(await db.list_streamer_communities(user_id)) if identity else None
    return HomeState(len(channels), tuple((row[0], row[3]) for row in live), identity is not None,
                     sum(bool(row[1]) for row in channels),live_known,communities)


def build_home(state: HomeState) -> HomeView:
    if state.tracked:
        lines = ['<b>Твои оповещения</b>', f'В списке: <b>{state.tracked}</b>']
        if state.notifications is not None:
            lines.append(f'Оповещения о старте: <b>{state.notifications} из {state.tracked}</b>')
            if not state.notifications: lines.append('Оповещения о старте выключены.')
        if not state.live_known:
            lines += ['', 'Статус эфиров пока недоступен. Попробуй позже.']
        elif state.live:
            lines += ['', f'<b>В эфире: {len(state.live)}</b>']
            entries = []
            for login, category in state.live[:3]:
                url = 'https://www.twitch.tv/' + quote(login, safe='')
                entry = '<a href="' + url + '"><b>' + html.escape(login[:60]) + '</b></a>'
                if category: entry += '\n' + html.escape(category[:100])
                entries.append(entry)
            lines.append('<blockquote>' + '\n\n'.join(entries) + '</blockquote>')
            lines.append('<i>По последней проверке</i>')
            if len(state.live) > 3: lines.append(f'И ещё {len(state.live)-3} в эфире')
        else:
            lines += ['', 'По последней проверке эфиров нет.']
        text = '\n'.join(lines)
    elif state.verified:
        text = '<b>Твои стримеры</b>\nПока никого не отслеживаешь.'
    else:
        text = HOME_TEXT
    if state.verified:
        # Identity is verified. Publishing permissions require a fresh Telegram check,
        # so Home never asserts that publishing is enabled from a stored toggle.
        text += '\n\n<b>Твой Twitch подключён</b>'
        if state.communities == 0:
            text += '\nTelegram-канал пока не выбран. Продолжи в «Я стример».'
        elif state.communities:
            text += f' · Telegram-подключений: <b>{state.communities}</b>\nПрава и публикации: «Я стример».'
    return HomeView(text + '\n\n' + COMMUNITY_LINE, not state.tracked and not state.verified)


class MenuStore:
    def __init__(self):
        self.messages = OrderedDict()
        self.locks = weakref.WeakValueDictionary()
        self.keyboards = OrderedDict()
        self.keyboard_locks = weakref.WeakValueDictionary()
        self.asset_file_ids = {}

    def lock(self, chat_id):
        lock = self.locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock(); self.locks[chat_id] = lock
        return lock

    def get(self, chat_id):
        entry = self.messages.get(chat_id)
        if entry and time.monotonic()-entry[0] < 24*3600:
            self.messages.move_to_end(chat_id)
            return entry[1],self.keyboard_state(chat_id)[0]=='menu'
        self.messages.pop(chat_id,None)
        return None,self.keyboard_state(chat_id)[0]=='menu'

    def remember(self, message):
        self.messages[message.chat.id] = (time.monotonic(),message)
        self.messages.move_to_end(message.chat.id)
        while len(self.messages)>1024: self.messages.popitem(last=False)

    def keyboard_initialized(self, chat_id):
        # Last successful delivery, not a claim about client visibility.
        self.keyboard_sent(chat_id,'menu')

    def keyboard_state(self, chat_id):
        entry=self.keyboards.get(chat_id)
        if entry and time.monotonic()-entry[0]<24*3600:
            self.keyboards.move_to_end(chat_id)
            return entry[1:]
        self.keyboards.pop(chat_id,None)
        return None,None,None

    def keyboard_sent(self, chat_id, mode, intent=None, expires_at=None):
        self.keyboards[chat_id]=(time.monotonic(),mode,intent,expires_at)
        self.keyboards.move_to_end(chat_id)
        while len(self.keyboards)>1024: self.keyboards.popitem(last=False)

    def keyboard_unknown(self, chat_id):
        self.keyboards.pop(chat_id,None)

    def keyboard_lock(self, chat_id):
        lock=self.keyboard_locks.get(chat_id)
        if lock is None:
            lock=asyncio.Lock(); self.keyboard_locks[chat_id]=lock
        return lock


def store_for(message):
    if not isinstance(message,(Message,InaccessibleMessage)): return None
    bot = message.bot
    store = _menus.get(bot.id)
    if store is None:
        store = MenuStore(); _menus[bot.id] = store
    _menus.move_to_end(bot.id)
    while len(_menus)>16: _menus.popitem(last=False)
    return store


def _has_media(message):
    return any(getattr(message,key,None) for key in ('photo','animation','video','document','audio'))


def _edit_unavailable(error):
    description = error.message.lower()
    return any(reason in description for reason in (
        'message to edit not found', "message can't be edited", 'message_id_invalid',
        'there is no text in the message to edit', 'there is no caption in the message'))


async def _detach(message):
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        if 'message is not modified' not in error.message.lower() and not _edit_unavailable(error): raise


async def edit_menu(message, text, *, reply_markup=None, force_text=False, **kwargs):
    """Caption-aware legacy-compatible edit. Never truncate a report to fit media."""
    result = None
    needs_new = _has_media(message) and (force_text or len(text)>1024)
    if not needs_new:
        try:
            if _has_media(message):
                caption_options={k:v for k,v in kwargs.items() if k not in {'disable_web_page_preview','link_preview_options'}}
                result = await message.edit_caption(caption=text,reply_markup=reply_markup,**caption_options)
            else:
                result = await message.edit_text(text,reply_markup=reply_markup,**kwargs)
        except TelegramBadRequest as error:
            if 'message is not modified' in error.message.lower():
                result = message
            elif _edit_unavailable(error): needs_new=True
            else: raise
    if needs_new:
        result = await message.answer(text,reply_markup=reply_markup,**kwargs)
        await _detach(message)
    if isinstance(result,Message) and result.chat.type=='private':
        store_for(result).remember(result)
    return result


async def send_menu_keyboard(message, text='Возвращайся сюда кнопкой «Меню».', *, force=False, state=None, wait=True):
    if message.chat.type!='private': return
    store=store_for(message)

    async def send():
        if store and state is not None:
            mode,intent,expiry=store.keyboard_state(message.chat.id)
            if mode=='selector' and expiry>time.time():
                if (await state.get_data()).get('telegram_community_intent')==intent:
                    # A delayed outcome for an older request must not replace
                    # a newer active selector. Its ordinary status still reaches the user.
                    if force: return await message.answer(text)
                    return
        if store and not force and store.keyboard_state(message.chat.id)[0]=='menu': return
        # A timeout may mean the request reached Telegram. Never keep an old
        # successful flag across an attempted replacement with unknown outcome.
        if store: store.keyboard_unknown(message.chat.id)
        result=await message.answer(text,reply_markup=menu_keyboard())
        if store: store.keyboard_initialized(message.chat.id)
        return result

    if store:
        lock=store.keyboard_lock(message.chat.id)
        if not wait and lock.locked(): return
        async with lock: return await send()
    return await send()


async def ensure_menu_keyboard(message):
    return await send_menu_keyboard(message)


async def send_channel_keyboard(message, state, row, keyboard):
    store=store_for(message)

    async def send():
        if (await state.get_data()).get('telegram_community_intent')!=row[0] or row[4]<=time.time(): return
        if store: store.keyboard_unknown(message.chat.id)
        result=await message.answer('<b>Выбери Telegram-канал</b>\nНажми кнопку ниже. Бот должен быть администратором с правом публикации сообщений.',
                                    reply_markup=keyboard)
        if store: store.keyboard_sent(message.chat.id,'selector',row[0],row[4])
        return result

    if store:
        async with store.keyboard_lock(message.chat.id): return await send()
    return await send()


async def recover_menu_keyboard(message, state=None, *, wait=True):
    await send_menu_keyboard(message,state=state,wait=wait)


async def send_brand_card(message, asset, path, text, *, reply_markup=None):
    """Reuse approved, static illustration file IDs only within this bot."""
    store=store_for(message)
    file_id=store.asset_file_ids.get(asset) if store else None
    result=await message.answer_photo(file_id or FSInputFile(path),caption=text,reply_markup=reply_markup)
    if store and isinstance(result,Message) and result.photo:
        store.asset_file_ids[asset]=result.photo[-1].file_id
        store.remember(result)
    return result


async def show_home(message, view, *, app_url=None, callback=False):
    store = store_for(message)
    keyboard = home_keyboard(message.chat.type,app_url=app_url)

    async def present():
        previous,ready = store.get(message.chat.id) if store else (None,False)
        if message.chat.type=='private' and not ready:
            await ensure_menu_keyboard(message)
            ready = True
        if callback:
            previous = message
        elif previous is not None:
            gap = message.message_id-previous.message_id
            elapsed = (message.date-previous.date).total_seconds()
            # Reuse only the immediately nearby card. An old card must not
            # silently change above newer dialogue or channel-selection steps.
            if not (0 < gap <= 2 and 0 <= elapsed <= 45):
                await _detach(previous)
                previous = None
        if message.chat.type=='private' or view.banner:
            asset = 'welcome' if view.banner else 'home'
            path = BANNER_PATH if view.banner else HOME_CARD_PATH
            file_id = store.asset_file_ids.get(asset) if store else None
            photo = file_id or FSInputFile(path)
            if previous is not None:
                try:
                    if (getattr(previous,'photo',None) and store
                        and previous.photo[-1].file_id == file_id):
                        result = await previous.edit_caption(caption=view.text,reply_markup=keyboard)
                    else:
                        result = await previous.edit_media(media=InputMediaPhoto(media=photo,caption=view.text),reply_markup=keyboard)
                except TelegramBadRequest as error:
                    if 'message is not modified' in error.message.lower(): result=previous
                    elif _edit_unavailable(error):
                        result=await message.answer_photo(photo,caption=view.text,reply_markup=keyboard)
                        await _detach(previous)
                    else: raise
            else:
                result=await message.answer_photo(photo,caption=view.text,reply_markup=keyboard)
        elif previous is not None:
            result=await edit_menu(previous,view.text,reply_markup=keyboard,force_text=True)
        else:
            result=await message.answer(view.text,reply_markup=keyboard)
        if store and isinstance(result,Message):
            if result.photo: store.asset_file_ids[asset]=result.photo[-1].file_id
            store.remember(result)
        return result

    if store:
        async with store.lock(message.chat.id): return await present()
    return await present()
