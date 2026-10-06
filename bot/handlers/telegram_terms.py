"""Условия подписки: короткий текст, документы и поддержка до покупки.

Находки D24 и B4: человек должен видеть тариф, цену, период, состав,
поддержку и доступные документы до создания счёта. Ссылки и контакты берутся
только из настроек: ничего не выдумывается, недоступный документ не
показывается ссылкой.
"""
from ..telegram_home import edit_menu
import html
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..legal_documents import get_support_state
from ..plan_catalog import catalog_payload
from ..telegram_ui import back_keyboard
from .telegram_streamer import private_callback


INFO_PREFIX = 'plus:info'
AGREE_PREFIX = 'plus:agree'
SCREENS = ('terms', 'docs', 'support')
# Документы, которые человек должен увидеть до оплаты цифрового товара.
PURCHASE_DOCUMENTS = ('privacy', 'agreement')
PRODUCT_IDS = tuple(product['product_id'] for product in catalog_payload()['products'])
SOURCES = ('more', 'streamer')
_BACK_TOKEN = re.compile(r'(?:plus|buy-(?:' + '|'.join(PRODUCT_IDS) + r')-(?:' + '|'.join(SOURCES) + r'))\Z')

TERMS_TEXT = (
    '<b>Условия подписки</b>\n\n'
    'Вы покупаете доступ к возможностям Plus на один месяц. Цена и состав указаны на экране тарифа '
    'до покупки: Зритель Plus 150 ₽, Стример Plus 300 ₽.\n'
    'Автопродление выключено: доступ заканчивается сам, повторных списаний нет.\n'
    'Telegram Stars проводит Telegram. СБП и банковская карта подключаются провайдером отдельно '
    'и сейчас могут быть недоступны: тогда интерфейс сообщит об этом, а заказ не создастся.\n'
    'Перед созданием счёта бот попросит подтвердить эти условия. Без подтверждения заказ не создаётся.\n'
    'Правила возврата и порядок обращения описаны в документах, а при их недоступности '
    'можно задать вопрос поддержке.'
)
DOCS_TEXT = (
    '<b>Документы</b>\n\n'
    'Политика конфиденциальности и Пользовательское соглашение открываются до оплаты.'
)
DOCS_PENDING = 'Часть документов ещё готовит свою редакцию: они станут доступны после утверждения.'
CONFIRM_TEXT = (
    '<b>Подтвердите условия</b>\n\n'
    'Нажимая «Я принимаю условия», вы подтверждаете, что прочитали тариф, '
    'Политику конфиденциальности и Пользовательское соглашение и принимаете их.'
)


def _base_url(config):
    base = str(getattr(config, 'oauth_public_base_url', '') or '').rstrip('/')
    return base if base.startswith('https://') else ''


def ready_documents(config):
    """Документы, которые прошли общий гейт и открываются по https.

    Пустая база или неподтверждённая редакция означает, что ссылку показать
    нечем: интерфейс не должен обещать недоступный документ.
    """
    base = _base_url(config)
    if not base:
        return ()
    return tuple((document.id, document.title, base + document.url)
                 for document in get_support_state(config).documents if document.ready)


def documents_pending(config):
    """Не все обязательные до покупки документы открыты."""
    ready = {document_id for document_id, _title, _url in ready_documents(config)}
    return not set(PURCHASE_DOCUMENTS) <= ready


def info_data(screen, back='plus'):
    if screen not in SCREENS or not isinstance(back, str) or _BACK_TOKEN.fullmatch(back) is None:
        raise ValueError('unknown subscription info screen')
    return f'{INFO_PREFIX}:{screen}:{back}'


def parse_info(data):
    parts = (data or '').split(':')
    if (len(parts) != 4 or parts[0] != 'plus' or parts[1] != 'info' or parts[2] not in SCREENS
            or _BACK_TOKEN.fullmatch(parts[3]) is None):
        return None
    return parts[2], parts[3]


def back_route(token):
    if token == 'plus':
        return 'menu:plus'
    parts = token.split('-')
    if len(parts) == 3 and parts[0] == 'buy' and parts[1] in PRODUCT_IDS and parts[2] in SOURCES:
        return f'plus:show:{parts[1]}:{parts[2]}'
    return 'menu:plus'


def buy_token(product_id, source):
    """Токен возврата на выбор способа оплаты того же тарифа."""
    if product_id not in PRODUCT_IDS or source not in SOURCES:
        raise ValueError('unknown tariff origin')
    return f'buy-{product_id}-{source}'


def terms_row(config, back='plus'):
    """Одна компактная строка: условия, доступные документы и поддержка."""
    row = [InlineKeyboardButton(text='Условия', callback_data=info_data('terms', back))]
    if ready_documents(config):
        row.append(InlineKeyboardButton(text='Документы', callback_data=info_data('docs', back)))
    row.append(InlineKeyboardButton(text='Поддержка', callback_data=info_data('support', back)))
    return row


def support_screen_text(config):
    state = get_support_state(config)
    lines = ['<b>Поддержка по подписке и оплате</b>']
    if state.telegram_url:
        lines.append(html.escape(state.telegram_url))
    if state.email:
        lines.append(html.escape(state.email))
    if not state.available:
        lines.append('Контакт поддержки пока не указан.')
    lines.append('Вопрос об оплате можно задать командой /paysupport.')
    return '\n\n'.join(lines)


def terms_screen(config):
    """Короткий текст условий с кнопками на документы и поддержку."""
    rows = [[InlineKeyboardButton(text=title, url=url)]
            for _document_id, title, url in ready_documents(config)]
    rows.append([InlineKeyboardButton(text='Поддержка', callback_data=info_data('support', 'plus'))])
    return TERMS_TEXT, InlineKeyboardMarkup(inline_keyboard=rows)


def documents_screen(config, back='plus'):
    documents = ready_documents(config)
    text = DOCS_TEXT
    if documents_pending(config):
        text += '\n\n<blockquote>' + DOCS_PENDING + '</blockquote>'
    rows = [[InlineKeyboardButton(text=title, url=url)] for _document_id, title, url in documents]
    rows.append([InlineKeyboardButton(text='Поддержка', callback_data=info_data('support', back))])
    rows.append([InlineKeyboardButton(text='← Назад', callback_data=back_route(back))])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def on_terms(message, config=None):
    text, keyboard = terms_screen(config)
    await message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)


async def cb_plus_info(callback, state=None, config=None, db=None, oauth_server=None):
    if not await private_callback(callback):
        return
    parsed = parse_info(callback.data)
    if parsed is None or callback.message is None:
        await callback.answer('Откройте тариф заново.', show_alert=True)
        return
    screen, back = parsed
    if screen == 'terms':
        text = TERMS_TEXT
        rows = [[InlineKeyboardButton(text=title, url=url)]
                for _document_id, title, url in ready_documents(config)]
        rows.append([InlineKeyboardButton(text='Поддержка', callback_data=info_data('support', back))])
        rows.append([InlineKeyboardButton(text='← Назад', callback_data=back_route(back))])
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    elif screen == 'docs':
        text, keyboard = documents_screen(config, back)
    else:
        text = support_screen_text(config)
        keyboard = back_keyboard(back_route(back))
    await edit_menu(callback.message, text, reply_markup=keyboard)
    await callback.answer()
