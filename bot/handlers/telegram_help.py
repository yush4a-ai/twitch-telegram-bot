"""Brief public help; contacts and documents come only from canonical config."""
from ..telegram_home import edit_menu
import html
import json
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from ..legal_documents import get_support_state
from ..telegram_ui import back_keyboard, cancel_ui
from .streams import _owner_admin_url, ABOUT_TEXT


DATA_SCREEN = ('<b>Мои данные</b>\n\n'
               'Бот хранит то, что нужно для оповещений: профиль Telegram (ID, имя, @username, язык), '
               'список стримеров, настройки отчётов и тихих часов, а также доступ Plus, если он выдан.\n\n'
               '<b>Что удаляется по вашему запросу</b>\n'
               '<blockquote>Профиль, список стримеров, настройки, избранное, история отчётов и наблюдений, '
               'подключение Twitch вместе с токенами, выданные бесплатные доступы.</blockquote>\n'
               '<b>Что остаётся</b>\n'
               '<blockquote>Записи об оплате: их хранение требует закон. Обезличенная статистика без вашего ID.</blockquote>')


TOPICS = {
    'about': ('Что умеет бот', '<b>Личные оповещения</b>\n'+ABOUT_TEXT+'\n\n'
              '<b>Без покупки</b>\n<blockquote>В Free: до 50 стримеров и фото эфира.\n'
              'Подключение своего Telegram-канала и существующие отчёты доступны без покупки.</blockquote>'),
    'import': ('Добавление и импорт', '<b>По нику или ссылке</b>\n'
               'Добавьте стримера по нику или ссылке Twitch и подтвердите выбор.\n\n'
               '<b>Из своего Twitch</b>\nМожно добавлять по одному или импортировать подписки своего Twitch-аккаунта.\n'
               'Для импорта разрешите чтение своих подписок в Twitch. Ссылка действует <b>5 минут</b>.\n\n'
               '<blockquote>Бот покажет найденные подписки, повторы и свободные места.\n'
               'Список изменится только после подтверждения. Повторы не добавляются.\n'
               'В Free до 50, в Зритель Plus до 200 стримеров.</blockquote>\n\n'
               '<b>Отмена и доступ</b>\n«Отменить» или «Меню» прекращает незавершённый импорт.\n'
               'Подключение своего Twitch не открывает число фолловеров чужих стримеров.'),
    'quiet': ('Тихие часы', '<b>Как настроить</b>\n'
              'Откройте «Ещё» → «Настройки», укажите свой UTC и выберите интервал.\n'
              'Перед сохранением проверьте местное время на экране подтверждения.\n\n'
              '<b>Что приостанавливается</b>\n<blockquote>Личные оповещения о начале эфира, рейдах, категории и напоминания.\n'
              'Для отдельных стримеров можно включить исключение.\n'
              'Отчёты о завершённых эфирах попадут в сводку после тихих часов.</blockquote>\n\n'
              'Рейды также учитывают тихие часы и выбранные исключения.\n\n'
              '<b>Что работает отдельно</b>\n'
              'Публикации в Telegram-каналах идут по настройкам канала.'),
    'reports': ('Отчёты и HTML', '<b>Где открыть</b>\nВ «Ещё» → «Отчёты» выберите стримера.\n\n'
                '<b>Данные эфира</b>\nНачало и завершение в UTC, длительность, пик и среднее число зрителей.\n'
                'Если часть данных чата потеряна после перезапуска, это отмечено в тексте.\n\n'
                '<b>Два формата</b>\n<blockquote>Краткий: сводка в сообщении.\n'
                'Развёрнутый: также HTML-файл с графиком и данными чата.\n'
                'Файл доступен в течение <b>24 часов</b> после эфира.\n'
                'Эти отчёты не требуют покупки тарифа.</blockquote>\n\n'
                'Автоотчёт включается отдельно в настройках стримера. HTML-файл можно сохранить как резерв.\n\n'
                '<b>Новые фолловеры</b>\nДля их подсчёта свой Twitch должен подключить сам стример.\n'
                'Авторизация зрителя этого доступа не даёт.'),
}


def help_screen(config=None):
    text=('<b>Помощь</b>\n\n<b>Следить за стримером</b>\nНажмите «➕ Добавить оповещения» и пришлите ник или ссылку Twitch.\n\n'
          '<b>Подключить свой канал</b>\nОткройте «Я стример», подключите Twitch и выберите Telegram-канал.\n\n'
          '<b>Настройки и отчёты</b>\nВ приложении и разделе «Ещё».')
    support=get_support_state(config)
    # Сайт продукта: адрес приходит из настроек, поэтому в коде его нет.
    site=getattr(config,'public_site_url',None)
    if site:
        text+='\n\n<b>Подробные инструкции</b>\n'+html.escape(site)
    rows=[[InlineKeyboardButton(text=title,callback_data='help:topic:'+key)] for key,(title,_) in TOPICS.items()]
    rows.append([InlineKeyboardButton(text='Команды бота',callback_data='help:commands')])
    rows.append([InlineKeyboardButton(text='Мои данные',callback_data='help:data')])
    if site:
        rows.append([InlineKeyboardButton(text='Открыть сайт',url=site)])
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
          '<blockquote>/track ник: добавить стримера\n/untrack ник: удалить\n/list: мой список\n/live: кто в эфире\n'
          '/report ник: отчёт и HTML-файл\n/import_follows: импорт подписок Twitch</blockquote>\n\n'
          '<b>Свой Twitch</b>\n/auth_twitch: фолловеры своего Twitch-канала\n'
          '/streamer_connect: подключить свой Twitch\n\n<b>Навигация и поддержка</b>\n'
          '/myid: мой Telegram ID\n/paysupport: поддержка по подписке\n/terms: условия подписки и документы\n/help: помощь\n/start: главное меню')
    if getattr(config,'growth_enabled',False) and getattr(config,'admin_telegram_bot_username','')=='SignalStreamsBot':
        text+='\n/invite: ссылка-приглашение'
    if _owner_admin_url(callback.message,config,actor_id=callback.from_user.id):
        text+='\n\n<b>Для владельца бота</b>\n/admin: админ-панель\n/stats: статистика бота\n/health: состояние бота'
    await edit_menu(callback.message,text,reply_markup=back_keyboard('menu:help'))
    await callback.answer()


async def cb_help_topic(callback):
    topic = TOPICS.get((callback.data or '').removeprefix('help:topic:'))
    if topic is None or callback.message is None:
        await callback.answer('Откройте помощь заново.', show_alert=True)
        return
    title, text = topic
    await edit_menu(callback.message, '<b>'+html.escape(title)+'</b>\n\n'+text, reply_markup=back_keyboard('menu:help'))
    await callback.answer()


def data_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Выгрузить файлом', callback_data='privacy:export')],
        [InlineKeyboardButton(text='Удалить мои данные', callback_data='privacy:delete')],
        [InlineKeyboardButton(text='← Назад', callback_data='menu:help')],
    ])


async def cb_help_data(callback, db=None):
    """Экран данных человека: что хранится, выгрузка и удаление."""
    if callback.message is None:
        await callback.answer()
        return
    text = DATA_SCREEN
    if db is not None:
        try:
            data = await db.export_person_data(callback.from_user.id)
        except Exception:
            data = None
        if data is not None:
            channels = len(data.get('channels') or [])
            text += f'\n\nВаш Telegram ID: <code>{callback.from_user.id}</code> · стримеров в списке: <b>{channels}</b>'
    await edit_menu(callback.message, text, reply_markup=data_keyboard())
    await callback.answer()


async def cb_privacy_export(callback, db=None):
    """Право на доступ: отдаём человеку его данные файлом."""
    if db is None or callback.message is None:
        await callback.answer('Попробуйте позже.', show_alert=True)
        return
    data = await db.export_person_data(callback.from_user.id)
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8')
    await callback.message.answer_document(
        BufferedInputFile(payload, filename='my-twitchsignal-data.json'),
        caption='Ваши данные из TwitchSignalBot.',
    )
    await callback.answer('Файл отправлен')


async def cb_privacy_delete(callback):
    """Перед удалением человек видит, что именно исчезнет."""
    if callback.message is None:
        await callback.answer()
        return
    text = ('<b>Удалить данные?</b>\n\n'
            'Профиль, список стримеров, настройки, история отчётов и подключение Twitch будут удалены. '
            'Вернуть их нельзя.\n\n'
            'Записи об оплате останутся: этого требует закон.')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Да, удалить всё', callback_data='privacy:delete:confirm')],
        [InlineKeyboardButton(text='Отмена', callback_data='help:data')],
    ])
    await edit_menu(callback.message, text, reply_markup=keyboard)
    await callback.answer()


async def cb_privacy_delete_confirm(callback, db=None):
    if db is None or callback.message is None:
        await callback.answer('Попробуйте позже.', show_alert=True)
        return
    removed = await db.delete_person_data(callback.from_user.id)
    total = sum(removed.values())
    text = (f'<b>Данные удалены</b>\n\nУдалено записей: <b>{total}</b>.\n'
            'Если захотите вернуться, нажмите /start: бот начнёт с чистого листа.')
    await edit_menu(callback.message, text, reply_markup=back_keyboard('menu:more'))
    await callback.answer('Готово')
