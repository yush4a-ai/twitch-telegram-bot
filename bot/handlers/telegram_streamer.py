"""Telegram adapters for the existing verified OAuth and channel intent services."""
from ..telegram_home import edit_menu
import secrets
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat, ReplyKeyboardMarkup, WebAppInfo
from ..telegram_ui import own_private, back_keyboard, cancel_ui
from ..oauth import OAuthFlowError
from .streams import _viewer_url


async def private_callback(callback):
    if callback.message is not None and own_private(callback.message,callback.from_user.id): return True
    await callback.answer("Открой свой личный чат с ботом.",show_alert=True)
    return False


async def cb_streamer(callback,db,config=None,state=None,oauth_server=None):
    if not await private_callback(callback): return
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    identity=await db.get_streamer_identity(callback.from_user.id)
    if identity:
        text=f"Twitch подключён: {identity[1]}"
        items=[('Telegram-канал','streamer:channel'),('Настройки публикаций','streamer:posts'),
               ('Тариф для стримера','plus:show:streamer_plus'),('Назад','menu:home')]
    else:
        text="Подключи Twitch, чтобы бот мог создавать публикации о твоих эфирах."
        items=[('Подключить Twitch','streamer:connect'),('Что получит стример?','streamer:benefits'),('Назад','menu:home')]
    await edit_menu(callback.message,text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t,callback_data=c)] for t,c in items]))
    await callback.answer()


async def cb_streamer_benefits(callback):
    if not await private_callback(callback): return
    await edit_menu(callback.message,"Подключи Twitch и Telegram-канал — бот сможет публиковать сообщения о начале твоих эфиров.\n\n"
        "Бесплатное подключение доступно без Plus. Стример Plus добавляет видео, свой текст, кнопки, варианты оформления и статистику.",
        reply_markup=back_keyboard('menu:streamer'))
    await callback.answer()


async def cb_streamer_connect(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    if await db.get_streamer_identity(actor):
        await cb_streamer(callback,db); return
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server,message=callback.message)
    if oauth_server is None:
        await edit_menu(callback.message,"Подключение Twitch пока недоступно. Попробуй позже.",reply_markup=back_keyboard('menu:streamer'))
        await callback.answer(); return
    generation=secrets.token_hex(8)
    await state.update_data(connect_generation=generation)
    try:
        intent,url,expires=await oauth_server.create_streamer_connect_intent(actor)
    except (OAuthFlowError,ValueError):
        if (await state.get_data()).get('connect_generation')==generation:
            await state.clear()
            await edit_menu(callback.message,"Подключение Twitch пока недоступно. Попробуй позже.",reply_markup=back_keyboard('menu:streamer'))
        await callback.answer(); return
    if (await state.get_data()).get('connect_generation')!=generation:
        await oauth_server.cancel_streamer_connect_intent(actor,intent)
        await callback.answer(); return
    await state.update_data(telegram_oauth_intent=intent)
    await edit_menu(callback.message,"Открой Twitch и разреши подключение. Затем нажми «Проверить подключение». Ссылка действует 10 минут.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Подключить Twitch",url=url)],
            [InlineKeyboardButton(text="Проверить подключение",callback_data='streamer:check')],
            [InlineKeyboardButton(text="← Назад",callback_data='menu:streamer')]]))
    await callback.answer()


async def cb_streamer_check(callback,state,db,config=None):
    if not await private_callback(callback): return
    data=await state.get_data()
    intent=data.get('telegram_oauth_intent')
    row=await db.get_streamer_connect_intent(intent) if intent else None
    if row is None or row[1]!=callback.from_user.id:
        await callback.answer("Подключение отменено. Начни заново.",show_alert=True); return
    if row[4]=='connected' and await db.get_streamer_identity(callback.from_user.id):
        await state.clear();await cb_streamer(callback,db,config);return
    if row[4] in {'pending','verifying'} and row[3]>time.time():
        await callback.answer("Жду разрешение Twitch. Открой ссылку и заверши подключение.",show_alert=True); return
    await state.clear()
    await edit_menu(callback.message,"Подключение не завершено. Попробуй ещё раз.",reply_markup=back_keyboard('menu:streamer'))
    await callback.answer()


async def show_channel_selector(message,state,row,db,oauth_server=None):
    # The row is an owned, pending channel intent read by the server. Reopening
    # this same selector keeps it; all other UI drafts must be cancelled first.
    await cancel_ui(state, actor_id=row[1], db=db, oauth_server=oauth_server,
                    message=message, preserve_community_intent=row[0])
    await state.update_data(telegram_community_intent=row[0])
    button=KeyboardButton(text="Выбрать Telegram-канал",request_chat=KeyboardButtonRequestChat(
        request_id=row[2],chat_is_channel=True,bot_is_member=True,request_title=True))
    await message.answer("Добавь бота в администраторы своего Telegram-канала с правом публикации сообщений. Затем выбери канал.",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[button],[KeyboardButton(text='Меню')]],
                                        resize_keyboard=True,is_persistent=True,one_time_keyboard=False))


async def cb_streamer_channel(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    if await db.get_streamer_identity(actor) is None:
        await callback.answer("Сначала подключи Twitch.",show_alert=True); return
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server,message=callback.message)
    generation=secrets.token_hex(8)
    await state.update_data(channel_generation=generation)
    intent=secrets.token_hex(16);request_id=secrets.randbelow(2**31-1)+1
    if not await db.create_community_intent(intent,actor,request_id,'channel',now=time.time()):
        await callback.answer("Сначала подключи Twitch.",show_alert=True);return
    if (await state.get_data()).get('channel_generation')!=generation:
        await db.cancel_community_intent(intent,actor);await callback.answer();return
    row=await db.get_community_intent(intent)
    if (await state.get_data()).get('channel_generation')!=generation:
        await db.cancel_community_intent(intent,actor);await callback.answer();return
    await show_channel_selector(callback.message,state,row,db,oauth_server)
    await callback.answer()


async def cb_streamer_posts(callback,db,config=None):
    if not await private_callback(callback): return
    if await db.get_streamer_identity(callback.from_user.id) is None:
        await callback.answer("Сначала подключи Twitch.",show_alert=True);return
    url=_viewer_url(callback.message,config,actor_id=callback.from_user.id)
    rows=[]
    if url and url.endswith('/app'):
        rows.append([InlineKeyboardButton(text="Открыть настройки",web_app=WebAppInfo(url=url))])
    rows.append([InlineKeyboardButton(text="← Назад",callback_data='menu:streamer')])
    await edit_menu(callback.message,"В приложении выбери «Стример» → «Посты». Там можно настроить текст, кнопки и оформление публикаций.",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()
