"""Brief public help; contacts and documents come only from canonical config."""
from ..telegram_home import edit_menu
import html
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..legal_documents import get_support_state
from ..telegram_ui import back_keyboard
from .streams import _owner_admin_url


def help_screen(config=None):
    text=('Помощь\n\nКак следить за стримером?\nНажми «➕ Добавить оповещения» и пришли ник или ссылку Twitch.\n\n'
          'Как подключить свой канал?\nОткрой «Я стример», подключи Twitch и выбери Telegram-канал.\n\n'
          'Где настройки и отчёты?\nВ приложении и разделе «Ещё».')
    support=get_support_state(config)
    rows=[[InlineKeyboardButton(text='Команды бота',callback_data='help:commands')]]
    if support.telegram_url:
        rows.append([InlineKeyboardButton(text='Написать в поддержку',url=support.telegram_url)])
    if support.email: text+='\n\nПоддержка: '+html.escape(support.email)
    base=getattr(config,'oauth_public_base_url','').rstrip('/')
    if base.startswith('https://'):
        for document in support.documents:
            if document.ready:
                rows.append([InlineKeyboardButton(text=document.title,url=base+document.url)])
    rows.append([InlineKeyboardButton(text='← Назад',callback_data='menu:more')])
    return text,InlineKeyboardMarkup(inline_keyboard=rows)


async def cb_help(callback,config=None):
    text,kb=help_screen(config)
    await edit_menu(callback.message,text,reply_markup=kb)
    await callback.answer()


async def cb_commands(callback,config=None):
    text=('Команды бота\n\n/track ник — добавить стримера\n/untrack ник — удалить\n/list — мой список\n/live — кто в эфире\n'
          '/report ник — отчёт и HTML-файл\n/import_follows — импорт подписок Twitch\n/auth_twitch — доступ к числу фолловеров\n'
          '/streamer_connect — подключить свой Twitch\n/myid — мой Telegram ID\n/paysupport — поддержка по подписке\n'
          '/help — помощь\n/start — главное меню')
    if getattr(config,'growth_enabled',False) and getattr(config,'admin_telegram_bot_username','')=='TwitchSignalTestbot':
        text+='\n/invite — ссылка-приглашение'
    if _owner_admin_url(callback.message,config,actor_id=callback.from_user.id):
        text+='\n\n/admin — админ-панель\n/stats — статистика бота\n/health — состояние бота'
    await edit_menu(callback.message,text,reply_markup=back_keyboard('menu:help'))
    await callback.answer()
