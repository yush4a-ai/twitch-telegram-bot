"""Brief public help; contacts and documents come only from canonical config."""
from ..telegram_home import edit_menu
import html
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..legal_documents import get_support_state
from ..telegram_ui import back_keyboard, cancel_ui
from .streams import _owner_admin_url, ABOUT_TEXT


TOPICS = {
    'about': ('Что умеет бот', ABOUT_TEXT+'\n\nВ Free — до 50 стримеров и фото эфира. '
              'Подключение своего Telegram-канала и существующие отчёты доступны без покупки.'),
    'import': ('Добавление и импорт', 'Добавь стримера по нику или ссылке Twitch и подтверди выбор. '
               'Можно добавлять по одному или импортировать подписки своего Twitch-аккаунта.\n\n'
               'Для импорта сначала разреши чтение своих подписок в Twitch. Ссылка действует 5 минут. '
               'Бот покажет найденные подписки, повторы и свободные места; список изменится только после подтверждения. '
               'Повторы не добавляются. В Free — до 50, в Зритель Plus — до 200 стримеров.\n\n'
               '«Отменить» или «Меню» прекращает незавершённый импорт. '
               'Подключение своего Twitch не открывает число фолловеров чужих стримеров.'),
    'quiet': ('Тихие часы', 'Открой «Ещё» → «Настройки оповещений», укажи свой UTC и выбери интервал. '
              'Перед сохранением проверь местное время на экране подтверждения.\n\n'
              'На это время приостанавливаются личные оповещения о начале эфира, категории и напоминания. '
              'Для отдельных стримеров можно включить исключение. Отчёты о завершённых эфирах попадут в сводку после тихих часов. '
              'Рейды не входят в этот режим. Публикации в Telegram-каналах идут по настройкам канала.'),
    'reports': ('Отчёты и HTML', 'В «Ещё» → «Отчёты» выбери стримера. '
                'В отчёте указаны начало и завершение в UTC, длительность, пик и среднее число зрителей. '
                'Если часть данных чата потеряна после перезапуска, это отмечено в тексте.\n\n'
                'Краткий отчёт содержит сводку. Развёрнутый — также HTML-файл с графиком и данными чата, '
                'доступный в течение 24 часов после эфира. Автоотчёт включается отдельно в настройках стримера. '
                'Эти отчёты не требуют покупки тарифа. HTML-файл можно сохранить как резерв.\n\n'
                'Для числа новых фолловеров свой Twitch должен подключить сам стример — '
                'авторизация зрителя этого доступа не даёт.'),
}


def help_screen(config=None):
    text=('Помощь\n\nКак следить за стримером?\nНажми «➕ Добавить оповещения» и пришли ник или ссылку Twitch.\n\n'
          'Как подключить свой канал?\nОткрой «Я стример», подключи Twitch и выбери Telegram-канал.\n\n'
          'Где настройки и отчёты?\nВ приложении и разделе «Ещё».')
    support=get_support_state(config)
    rows=[[InlineKeyboardButton(text=title,callback_data='help:topic:'+key)] for key,(title,_) in TOPICS.items()]
    rows.append([InlineKeyboardButton(text='Команды бота',callback_data='help:commands')])
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


async def cb_help(callback,config=None,state=None,db=None,oauth_server=None):
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    text,kb=help_screen(config)
    await edit_menu(callback.message,text,reply_markup=kb)
    await callback.answer()


async def cb_commands(callback,config=None):
    text=('Команды бота\n\n/track ник — добавить стримера\n/untrack ник — удалить\n/list — мой список\n/live — кто в эфире\n'
          '/report ник — отчёт и HTML-файл\n/import_follows — импорт подписок Twitch\n/auth_twitch — фолловеры своего Twitch-канала\n'
          '/streamer_connect — подключить свой Twitch\n/myid — мой Telegram ID\n/paysupport — поддержка по подписке\n'
          '/help — помощь\n/start — главное меню')
    if getattr(config,'growth_enabled',False) and getattr(config,'admin_telegram_bot_username','')=='TwitchSignalTestbot':
        text+='\n/invite — ссылка-приглашение'
    if _owner_admin_url(callback.message,config,actor_id=callback.from_user.id):
        text+='\n\n/admin — админ-панель\n/stats — статистика бота\n/health — состояние бота'
    await edit_menu(callback.message,text,reply_markup=back_keyboard('menu:help'))
    await callback.answer()


async def cb_help_topic(callback):
    topic = TOPICS.get((callback.data or '').removeprefix('help:topic:'))
    if topic is None or callback.message is None:
        await callback.answer('Открой помощь заново.', show_alert=True)
        return
    title, text = topic
    await edit_menu(callback.message, title+'\n\n'+text, reply_markup=back_keyboard('menu:help'))
    await callback.answer()
