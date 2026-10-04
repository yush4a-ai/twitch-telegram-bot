"""Brief public help; contacts and documents come only from canonical config."""
from ..telegram_home import edit_menu
import html
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..legal_documents import get_support_state
from ..telegram_ui import back_keyboard, cancel_ui
from .streams import _owner_admin_url, ABOUT_TEXT


TOPICS = {
    'about': ('Что умеет бот', '<b>Личные оповещения</b>\n'+ABOUT_TEXT+'\n\n'
              '<b>Без покупки</b>\n<blockquote>В Free — до 50 стримеров и фото эфира.\n'
              'Подключение своего Telegram-канала и существующие отчёты доступны без покупки.</blockquote>'),
    'import': ('Добавление и импорт', '<b>По нику или ссылке</b>\n'
               'Добавь стримера по нику или ссылке Twitch и подтверди выбор.\n\n'
               '<b>Из своего Twitch</b>\nМожно добавлять по одному или импортировать подписки своего Twitch-аккаунта.\n'
               'Для импорта разреши чтение своих подписок в Twitch. Ссылка действует <b>5 минут</b>.\n\n'
               '<blockquote>Бот покажет найденные подписки, повторы и свободные места.\n'
               'Список изменится только после подтверждения. Повторы не добавляются.\n'
               'В Free — до 50, в Зритель Plus — до 200 стримеров.</blockquote>\n\n'
               '<b>Отмена и доступ</b>\n«Отменить» или «Меню» прекращает незавершённый импорт.\n'
               'Подключение своего Twitch не открывает число фолловеров чужих стримеров.'),
    'quiet': ('Тихие часы', '<b>Как настроить</b>\n'
              'Открой «Ещё» → «Настройки оповещений», укажи свой UTC и выбери интервал.\n'
              'Перед сохранением проверь местное время на экране подтверждения.\n\n'
              '<b>Что приостанавливается</b>\n<blockquote>Личные оповещения о начале эфира, категории и напоминания.\n'
              'Для отдельных стримеров можно включить исключение.\n'
              'Отчёты о завершённых эфирах попадут в сводку после тихих часов.</blockquote>\n\n'
              '<b>Что работает отдельно</b>\nРейды не входят в этот режим.\n'
              'Публикации в Telegram-каналах идут по настройкам канала.'),
    'reports': ('Отчёты и HTML', '<b>Где открыть</b>\nВ «Ещё» → «Отчёты» выбери стримера.\n\n'
                '<b>Данные эфира</b>\nНачало и завершение в UTC, длительность, пик и среднее число зрителей.\n'
                'Если часть данных чата потеряна после перезапуска, это отмечено в тексте.\n\n'
                '<b>Два формата</b>\n<blockquote>Краткий — сводка в сообщении.\n'
                'Развёрнутый — также HTML-файл с графиком и данными чата.\n'
                'Файл доступен в течение <b>24 часов</b> после эфира.\n'
                'Эти отчёты не требуют покупки тарифа.</blockquote>\n\n'
                'Автоотчёт включается отдельно в настройках стримера. HTML-файл можно сохранить как резерв.\n\n'
                '<b>Новые фолловеры</b>\nДля их подсчёта свой Twitch должен подключить сам стример.\n'
                'Авторизация зрителя этого доступа не даёт.'),
}


def help_screen(config=None):
    text=('<b>Помощь</b>\n\n<b>Следить за стримером</b>\nНажми «➕ Добавить оповещения» и пришли ник или ссылку Twitch.\n\n'
          '<b>Подключить свой канал</b>\nОткрой «Я стример», подключи Twitch и выбери Telegram-канал.\n\n'
          '<b>Настройки и отчёты</b>\nВ приложении и разделе «Ещё».')
    support=get_support_state(config)
    rows=[[InlineKeyboardButton(text=title,callback_data='help:topic:'+key)] for key,(title,_) in TOPICS.items()]
    rows.append([InlineKeyboardButton(text='Команды бота',callback_data='help:commands')])
    if support.telegram_url:
        rows.append([InlineKeyboardButton(text='Написать в поддержку',url=support.telegram_url)])
    if support.email: text+='\n\n<b>Поддержка</b>\n'+html.escape(support.email)
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
    text=('<b>Команды бота</b>\n\n<b>Стримеры и отчёты</b>\n'
          '<blockquote>/track ник — добавить стримера\n/untrack ник — удалить\n/list — мой список\n/live — кто в эфире\n'
          '/report ник — отчёт и HTML-файл\n/import_follows — импорт подписок Twitch</blockquote>\n\n'
          '<b>Свой Twitch</b>\n/auth_twitch — фолловеры своего Twitch-канала\n'
          '/streamer_connect — подключить свой Twitch\n\n<b>Навигация и поддержка</b>\n'
          '/myid — мой Telegram ID\n/paysupport — поддержка по подписке\n/help — помощь\n/start — главное меню')
    if getattr(config,'growth_enabled',False) and getattr(config,'admin_telegram_bot_username','')=='TwitchSignalTestbot':
        text+='\n/invite — ссылка-приглашение'
    if _owner_admin_url(callback.message,config,actor_id=callback.from_user.id):
        text+='\n\n<b>Для владельца бота</b>\n/admin — админ-панель\n/stats — статистика бота\n/health — состояние бота'
    await edit_menu(callback.message,text,reply_markup=back_keyboard('menu:help'))
    await callback.answer()


async def cb_help_topic(callback):
    topic = TOPICS.get((callback.data or '').removeprefix('help:topic:'))
    if topic is None or callback.message is None:
        await callback.answer('Открой помощь заново.', show_alert=True)
        return
    title, text = topic
    await edit_menu(callback.message, '<b>'+html.escape(title)+'</b>\n\n'+text, reply_markup=back_keyboard('menu:help'))
    await callback.answer()
