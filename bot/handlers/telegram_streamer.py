"""Telegram adapters for the existing verified OAuth and channel intent services."""
from ..telegram_home import edit_menu,send_brand_card,CHANNEL_GUIDE_PATH
import asyncio
import html
import secrets
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat, ReplyKeyboardMarkup, WebAppInfo
from ..telegram_ui import own_private, back_keyboard, cancel_ui
from ..oauth import OAuthFlowError
from .streams import _viewer_url


async def private_callback(callback):
    if callback.message is not None and own_private(callback.message,callback.from_user.id): return True
    await callback.answer("Откройте свой личный чат с ботом.",show_alert=True)
    return False


async def cb_streamer(callback,db,config=None,state=None,oauth_server=None):
    if not await private_callback(callback): return
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    identity=await db.get_streamer_identity(callback.from_user.id)
    if identity:
        text=f"<b>Ваш Twitch-канал</b>\n\n<blockquote>Twitch подключён: <b>{html.escape(identity[1])}</b></blockquote>"
        communities=await db.list_streamer_communities(callback.from_user.id)
        if communities:
            text+='\n\n<b>Telegram-подключения</b>\n'+'\n'.join(html.escape(str(title)[:100]) for _,title,_ in communities)
            text+='\n\nПроверьте текущие права и включены ли публикации.'
        else:
            text+='\n\n<b>Следующий шаг</b>\nВыберите Telegram-канал, затем настройте публикации.'
        items=[('Telegram-канал','streamer:channel'),('Настройки публикаций','streamer:posts'),
               ('Проверить готовность','streamer:readiness'),
               ('Тариф для стримера','plus:show:streamer_plus:streamer'),('Назад','menu:home')]
    else:
        text="<b>Ваш Twitch-канал</b>\n\n<blockquote>Подключите Twitch, чтобы бот мог создавать публикации о ваших эфирах.</blockquote>\n\nЗатем выберите свой Telegram-канал."
        items=[('Подключить Twitch','streamer:connect'),('Что получит стример?','streamer:benefits'),('Назад','menu:home')]
    await edit_menu(callback.message,text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t,callback_data=c)] for t,c in items]))
    await callback.answer()


async def cb_streamer_benefits(callback):
    if not await private_callback(callback): return
    await edit_menu(callback.message,"<b>Публикации о ваших эфирах</b>\n\n<b>Без покупки</b>\n"
        "<blockquote>Подключите Twitch и Telegram-канал: бот сможет публиковать сообщения о начале ваших эфиров.\n"
        "Бесплатное подключение доступно без Plus.</blockquote>\n\n<b>Стример Plus</b>\n"
        "Видео, свой текст, кнопки, варианты оформления и статистика.",
        reply_markup=back_keyboard('menu:streamer'))
    await callback.answer()


async def cb_streamer_connect(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    if await db.get_streamer_identity(actor):
        await cb_streamer(callback,db); return
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server,message=callback.message)
    if oauth_server is None:
        await edit_menu(callback.message,"Подключение Twitch пока недоступно. Попробуйте позже.",reply_markup=back_keyboard('menu:streamer'))
        await callback.answer(); return
    generation=secrets.token_hex(8)
    await state.update_data(connect_generation=generation)
    try:
        intent,url,expires=await oauth_server.create_streamer_connect_intent(actor)
    except (OAuthFlowError,ValueError):
        if (await state.get_data()).get('connect_generation')==generation:
            await state.clear()
            await edit_menu(callback.message,"Подключение Twitch пока недоступно. Попробуйте позже.",reply_markup=back_keyboard('menu:streamer'))
        await callback.answer(); return
    if (await state.get_data()).get('connect_generation')!=generation:
        await oauth_server.cancel_streamer_connect_intent(actor,intent)
        await callback.answer(); return
    await state.update_data(telegram_oauth_intent=intent)
    await edit_menu(callback.message,"<b>Подключение Twitch</b>\n\n"
        "<b>1.</b> Откройте Twitch и разрешите подключение.\n<b>2.</b> Вернитесь сюда и нажмите «Проверить подключение».\n\n"
        "<blockquote>Ссылка действует <b>10 минут</b>.</blockquote>",
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
        await callback.answer("Подключение отменено. Начните заново.",show_alert=True); return
    if row[4]=='connected' and await db.get_streamer_identity(callback.from_user.id):
        await state.clear();await cb_streamer(callback,db,config);return
    if row[4] in {'pending','verifying'} and row[3]>time.time():
        await callback.answer("Жду разрешение Twitch. Откройте ссылку и завершите подключение.",show_alert=True); return
    await state.clear()
    await edit_menu(callback.message,"Подключение не завершено. Попробуйте ещё раз.",reply_markup=back_keyboard('menu:streamer'))
    await callback.answer()


async def show_channel_selector(message,state,row,db,oauth_server=None):
    # The row is an owned, pending channel intent read by the server. Reopening
    # this same selector keeps it; all other UI drafts must be cancelled first.
    await cancel_ui(state, actor_id=row[1], db=db, oauth_server=oauth_server,
                    message=message, preserve_community_intent=row[0])
    await state.update_data(telegram_community_intent=row[0])
    await send_brand_card(message,'channel-guide',CHANNEL_GUIDE_PATH,
        '<b>Публикации в вашем Telegram-канале</b>\n\n'
        '<b>1.</b> Добавьте бота в администраторы.\n<b>2.</b> Разрешите «Публикация сообщений».\n'
        '<b>3.</b> Выберите канал кнопкой под строкой ввода.\n\n<blockquote>Подключение бесплатно.</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Как подключить?',callback_data='streamer:channelhelp')],
            [InlineKeyboardButton(text='Отменить',callback_data='menu:streamer')]]))
    button=KeyboardButton(text="Выбрать Telegram-канал",request_chat=KeyboardButtonRequestChat(
        request_id=row[2],chat_is_channel=True,bot_is_member=True,request_title=True))
    from ..telegram_home import send_channel_keyboard
    await send_channel_keyboard(message,state,row,
        ReplyKeyboardMarkup(keyboard=[[button],[KeyboardButton(text='Меню')]],
                            resize_keyboard=True,is_persistent=True,one_time_keyboard=False))


async def cb_streamer_channel(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    if await db.get_streamer_identity(actor) is None:
        await callback.answer("Сначала подключите Twitch.",show_alert=True); return
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server,message=callback.message)
    generation=secrets.token_hex(8)
    await state.update_data(channel_generation=generation)
    intent=secrets.token_hex(16);request_id=secrets.randbelow(2**31-1)+1
    if not await db.create_community_intent(intent,actor,request_id,'channel',now=time.time()):
        await callback.answer("Сначала подключите Twitch.",show_alert=True);return
    if (await state.get_data()).get('channel_generation')!=generation:
        await db.cancel_community_intent(intent,actor);await callback.answer();return
    row=await db.get_community_intent(intent)
    if (await state.get_data()).get('channel_generation')!=generation:
        await db.cancel_community_intent(intent,actor);await callback.answer();return
    await show_channel_selector(callback.message,state,row,db,oauth_server)
    await callback.answer()


async def cb_channel_help(callback,state,db):
    if not await private_callback(callback): return
    row=await db.get_community_intent((await state.get_data()).get('telegram_community_intent',''))
    pending=bool(row and row[1]==callback.from_user.id and row[6]=='pending' and row[4]>time.time())
    await edit_menu(callback.message,
        '<b>Как подключить Telegram-канал</b>\n\n'
        '<b>1. Добавьте бота</b>\nУправление каналом → «Администраторы» → «Добавить администратора». Найдите этого бота по имени.\n\n'
        '<b>2. Разрешите публикацию</b>\nВключите «Публикация сообщений». Вы тоже должны быть владельцем или администратором канала.\n\n'
        '<b>3. Выберите канал</b>\nВернитесь сюда и нажмите «Выбрать Telegram-канал» под строкой ввода. Выберите свой канал в окне Telegram.\n\n'
        '<b>4. Проверьте результат</b>\nБот проверит права и подтвердит сохранение. Если канал не виден, проверьте, добавлен ли именно этот бот.\n\n'
        '<blockquote>Новое подключение поддерживает каналы. Ранее подключённые группы сохраняются.\n'
        '«Меню» отменяет незавершённый выбор.</blockquote>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='← Назад к выбору' if pending else '← Назад',callback_data='streamer:channelresume' if pending else 'menu:streamer')],
            [InlineKeyboardButton(text='Отменить',callback_data='menu:streamer')]]))
    await callback.answer()


async def cb_channel_resume(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    row=await db.get_community_intent((await state.get_data()).get('telegram_community_intent',''))
    if not row or row[1]!=callback.from_user.id or row[6]!='pending' or row[4]<=time.time():
        await callback.answer('Выбор завершён или устарел. Начните подключение заново.',show_alert=True);return
    await show_channel_selector(callback.message,state,row,db,oauth_server)
    await callback.answer()


async def cb_streamer_readiness(callback,db,state=None,oauth_server=None):
    if not await private_callback(callback): return
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    identity=await db.get_streamer_identity(callback.from_user.id)
    if not identity:
        await callback.answer('Сначала подключите Twitch.',show_alert=True);return
    rows=await db.list_streamer_communities(callback.from_user.id)
    from ..streamer_community import check_community_permission
    await callback.answer('Проверяю права…')
    checks=await asyncio.gather(*(check_community_permission(callback.bot,chat,callback.from_user.id) for chat,_,_ in rows))
    labels={'user_denied':'Нужны ваши права администратора','bot_absent':'Добавьте бота в канал',
        'bot_member':'Назначьте бота администратором','missing_post_right':'Нет права публикации сообщений',
        'network_error':'Проверка прав пока недоступна','wrong_chat_type':'Неподдерживаемое подключение'}
    lines=['<b>Публикации в Telegram</b>','']
    for (chat,title,_),check in zip(rows,checks):
        settings=await db.list_channels_with_routing(chat)
        enabled=next((row[1] for row in settings if row[0]==identity[1]),None)
        status=(labels.get(check.status,'Проверка прав пока недоступна') if check.status!='ready' else
                'Публикации выключены' if enabled is False else 'Стример не добавлен в публикации' if enabled is None else
                'Права проверены. Публикации включены в настройках')
        lines.append('<b>'+html.escape(str(title)[:100])+'</b>\n'+status+'\n')
    if not rows: lines.append('Telegram-канал пока не выбран.')
    await edit_menu(callback.message,'\n'.join(lines),reply_markup=back_keyboard('menu:streamer'))


async def cb_streamer_posts(callback,db,config=None,state=None,oauth_server=None):
    if not await private_callback(callback): return
    if await db.get_streamer_identity(callback.from_user.id) is None:
        await callback.answer("Сначала подключите Twitch.",show_alert=True);return
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,
                        oauth_server=oauth_server,message=callback.message)
    url=_viewer_url(callback.message,config,actor_id=callback.from_user.id)
    rows=[]
    if url and url.endswith('/app'):
        rows.append([InlineKeyboardButton(text="Настроить оформление в приложении",web_app=WebAppInfo(url=url))])
    rows.append([InlineKeyboardButton(text="← Назад",callback_data='menu:streamer')])
    await edit_menu(callback.message,"<b>Оформление публикаций</b>\n\n<blockquote>В приложении выберите «Стример» → «Посты».</blockquote>\n\nТам можно настроить текст, кнопки и оформление публикаций.",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()
