"""Read-only Plus navigation over the same catalog and subscription contract."""
from ..telegram_home import edit_menu
import secrets
import time
import html
from datetime import datetime, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from ..plan_catalog import catalog_payload, PAYMENT_UNAVAILABLE_MESSAGE, PLUS_TERMS_VERSION
from ..subscription_state import SubscriptionService
from ..telegram_ui import back_keyboard, cancel_ui
from .telegram_streamer import private_callback
from .telegram_terms import CONFIRM_TEXT, buy_token, terms_row
from .streams import _viewer_url


def product_view(product_id):
    return next(p for p in catalog_payload()['products'] if p['product_id']==product_id)


def benefits(product):
    features=catalog_payload()['features']
    groups=[]
    for block in product['benefit_blocks']:
        lines=['<b>'+html.escape(block['title'])+'</b>']
        for feature_id in block['feature_ids']:
            feature=features[feature_id]
            label=(html.escape(feature['title'])+': ' if len(block['feature_ids'])>1 else '')
            lines.append(label+html.escape(feature['description']))
        groups.append('<blockquote>'+'\n'.join(lines)+'</blockquote>')
    return '\n\n'.join(groups)


def stars_checkout_ready(billing_service) -> bool:
    """Можно ли сейчас продавать звёздами в боте.

    Нужен явный флаг владельца: наличие провайдера и утверждённых условий само
    по себе оплату не открывает.
    """
    policy = getattr(billing_service, "runtime_policy", None)
    policy = policy() if callable(policy) else policy
    return bool(
        policy is not None
        and getattr(policy, "allow_public_stars", False)
        and getattr(policy, "allow_invoice", False)
        and getattr(policy, "mode", "offline") != "offline"
        and getattr(policy, "target_verified", False)
    )


def bank_channel_ready(bank_billing_service) -> bool:
    """Готов ли банковский канал: СБП и карта продаются в приложении.

    Проверяем сам сервис, а не ключи: маршрут уведомлений и провайдер должны
    быть действительно подключены, иначе обещание способа оплаты было бы ложным.
    """
    if bank_billing_service is None:
        return False
    policy = getattr(bank_billing_service, "runtime_policy", None)
    policy = policy() if callable(policy) else policy
    ready = getattr(bank_billing_service, "public_callback_ready", None)
    return bool(
        callable(ready) and ready()
        and getattr(policy, "allow_external_create", False)
    )


def payment_status_text(billing_service, bank_billing_service=None) -> str:
    stars = stars_checkout_ready(billing_service)
    bank = bank_channel_ready(bank_billing_service)
    if stars and bank:
        return ("Telegram Stars: доступно, счёт придёт в этот чат.\n"
                "СБП и банковская карта: в приложении.")
    if stars:
        return ("Telegram Stars: доступно, счёт придёт в этот чат.\n"
                "СБП и банковская карта: пока недоступны.")
    if bank:
        return ("СБП и банковская карта: в приложении.\n"
                "Telegram Stars: пока недоступны.")
    return PAYMENT_UNAVAILABLE_MESSAGE


def checkout_view(result, back: str):
    """Что показать после попытки создать счёт звёздами."""
    state = getattr(result, "state", "unavailable")
    reason = getattr(result, "reason_code", None)
    hosted = getattr(result, "hosted_url", None)
    if state == "pending" and hosted:
        text = ("<b>Счёт готов</b>\n\nОплатите звёздами по кнопке ниже. "
                "После оплаты доступ включится автоматически.")
        rows = [[InlineKeyboardButton(text="Оплатить звёздами", url=hosted)],
                [InlineKeyboardButton(text="← Назад", callback_data=back)]]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)
    if state == "pending":
        text = ("<b>Счёт отправлен</b>\n\nПроверьте сообщение со счётом в этом чате. "
                "После оплаты доступ включится автоматически.")
    elif reason == "already_active":
        text = ("<b>Подписка уже действует</b>\n\n"
                "Продлевать не нужно: доступ активен до конца оплаченного периода.")
    elif state in {"creation_unknown", "manual_review"}:
        text = ("<b>Не удалось создать счёт</b>\n\n"
                "Повторно счёт не отправляем, чтобы не списать дважды. "
                "Напишите в поддержку: /paysupport")
    else:
        text = ("<b>Оплата недоступна</b>\n\n" + PAYMENT_UNAVAILABLE_MESSAGE
                + "\n\n<blockquote>Платёж не создан. Деньги не списаны.</blockquote>")
    return text, back_keyboard(back)


def product_route(data, action):
    parts=(data or '').split(':')
    if len(parts) not in {3,4} or parts[:2]!=['plus',action]:
        raise ValueError('invalid product route')
    source=parts[3] if len(parts)==4 and parts[3] in {'more','streamer'} else 'more'
    return parts[2],source


def return_route(source):
    return 'menu:streamer' if source=='streamer' else 'menu:more'


def offer_keyboard(product,source='more',config=None):
    viewer=product['product_id']=='viewer_plus'
    secondary=product_view('streamer_plus' if viewer else 'viewer_plus')
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оформить {product['title']}: {product['price_label']}",callback_data=f"plus:buy:{product['product_id']}:{source}")],
        [InlineKeyboardButton(text='Тариф для стримера' if viewer else f"Тариф для зрителя: {secondary['price_label']}",
                              callback_data=f"plus:show:{secondary['product_id']}:{source}")],
        terms_row(config, buy_token(product['product_id'],source)),
        [InlineKeyboardButton(text='← Назад',callback_data=return_route(source))]])


def confirmation_text(product, method_id):
    """Что человек подтверждает перед созданием счёта: тариф, цена и способ."""
    method=next((row for row in catalog_payload()['methods'] if row['id']==method_id),None)
    title=method['title'] if method else method_id
    return (CONFIRM_TEXT+"\n\n"
            f"Тариф: <b>{html.escape(product['title'])}</b>, {product['price_label']} / "
            f"{product['period_label'].removeprefix('1 ')}.\n"
            f"Способ: <b>{html.escape(title)}</b>.\n"
            "Автопродление выключено.\n"
            f"Версия условий: <code>{html.escape(PLUS_TERMS_VERSION)}</code>.")


def confirmation_keyboard(product,source,nonce,config=None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Я принимаю условия',callback_data=f"plus:agree:{nonce}")],
        terms_row(config, buy_token(product['product_id'],source)),
        [InlineKeyboardButton(text='← Назад',callback_data=f"plus:buy:{product['product_id']}:{source}")]])


async def access_label(db,user_id,product_id,active,now):
    if product_id=='viewer_plus':
        grant_ids=[source['grant_id'] for source in active['sources']]
    else:
        row=await SubscriptionService(db).owned_streamer_grant(user_id,now)
        grant_ids=[row[0]] if row else []
    if not grant_ids: return 'Активна'
    # Only the server-selected grants contributing to this access/expiry count.
    # A separate trial or short mock order must not relabel another paid grant.
    rows=await (await db.conn.execute(
        "SELECT g.source,EXISTS (SELECT 1 FROM viewer_test_trials t "
        "WHERE t.grant_id=g.grant_id AND t.telegram_user_id=?),"
        "EXISTS (SELECT 1 FROM billing_orders o WHERE o.grant_id=g.grant_id "
        "AND o.telegram_user_id=? AND o.provider='mock') "
        "FROM entitlement_grants g WHERE g.grant_id IN ("+','.join('?' for _ in grant_ids)+")",
        (user_id,user_id,*grant_ids),
    )).fetchall()
    kinds=['trial' if trial or source=='trial' else 'test' if mock or source in {'test','mock'} else 'active'
           for source,trial,mock in rows]
    if kinds and all(kind=='trial' for kind in kinds): return 'Ознакомительный доступ'
    return 'Активна' if not kinds or 'active' in kinds else 'Тестовый доступ'


async def cb_plus(callback,state,db,config=None,billing_service=None,oauth_server=None,bank_billing_service=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server,message=callback.message)
    now=time.time();status=await SubscriptionService(db).state(actor,now=now)
    if callback.data=='menu:plus' and (status['streamer']['active'] or status['viewer']['active']):
        key='streamer' if status['streamer']['active'] else 'viewer'
        product=product_view(key+'_plus');active=status[key]
        label=await access_label(db,actor,product['product_id'],active,now)
        end=datetime.fromtimestamp(active['expires_at'],timezone.utc).astimezone(timezone(timedelta(hours=3)))
        text=f"<b>Моя подписка</b>\n\n<b>{html.escape(product['title'])}</b>\n" \
             f"Статус: <b>{html.escape(label)}</b>\nДо <b>{end:%d.%m.%Y %H:%M} (МСК)</b>\n" \
             "Автопродление выключено.\n\n<b>Что включено</b>\n"
        if key=='streamer': text+='Зритель Plus включён.\n\n'
        text+=benefits(product)
        if key=='streamer': text+='\n\n<b>Возможности зрителя</b>\n'+benefits(product_view('viewer_plus'))
        rows=[];url=_viewer_url(callback.message,config,actor_id=actor)
        if url and url.endswith('/app'):
            rows.append([InlineKeyboardButton(text='Управление подпиской',web_app=WebAppInfo(url=url+'?screen=subscription'))])
        rows.extend([[InlineKeyboardButton(text='О тарифе',callback_data='plus:show:'+product['product_id'])],
                     [InlineKeyboardButton(text='← Назад',callback_data='menu:more')]])
        await edit_menu(callback.message,text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        primary='streamer_plus' if status['streamer']['linked'] else 'viewer_plus'
        try:
            product_id,source=product_route(callback.data,'show') if callback.data.startswith('plus:show:') else (primary,'more')
            product=product_view(product_id)
        except (StopIteration,ValueError):
            await callback.answer('Тариф недоступен. Откройте тариф заново.',show_alert=True);return
        text=f"<b>{html.escape(product['title'])}</b>\n" \
             f"<b>{product['price_label']} / {product['period_label'].removeprefix('1 ')}</b>\n\n" \
             "<b>Что включено</b>\n"
        if product['includes']: text+='В Стример Plus включены все возможности Зритель Plus.\n\n'
        text+=benefits(product)
        if product['includes']: text+='\n\n<b>Возможности зрителя</b>\n'+benefits(product_view('viewer_plus'))
        text+='\n\n<b>Оплата</b>\n'+payment_status_text(billing_service, bank_billing_service)
        await edit_menu(callback.message,text,reply_markup=offer_keyboard(product,source,config))
    await callback.answer()


async def cb_buy(callback,state,db,billing_service=None,oauth_server=None,config=None,bank_billing_service=None):
    if not await private_callback(callback): return
    try:
        product_id,source=product_route(callback.data,'buy')
        product=product_view(product_id)
    except (StopIteration,ValueError):
        await callback.answer('Тариф недоступен. Откройте тариф заново.',show_alert=True);return
    await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    nonce=secrets.token_hex(8)
    await state.update_data(purchase_product=product_id,purchase_source=source,purchase_nonce=nonce,purchase_expires_at=time.time()+600)
    rows=[[InlineKeyboardButton(text=m['title'],callback_data=f"plus:pay:{nonce}:{m['id']}")] for m in catalog_payload()['methods']]
    rows.append(terms_row(config, buy_token(product_id,source)))
    rows.append([InlineKeyboardButton(text='← Назад',callback_data=f'plus:show:{product_id}:{source}')])
    await edit_menu(callback.message,f"<b>{html.escape(product['title'])}</b>\n<b>{product['price_label']} / {product['period_label'].removeprefix('1 ')}</b>\n\n"
        "<b>Выберите способ оплаты</b>\n<blockquote>Telegram Stars: через Telegram.\nСБП и банковская карта: через Platega.</blockquote>\n\n"
        +payment_status_text(billing_service, bank_billing_service)+"\n\nАвтопродление выключено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def cb_payment_method(callback,state,billing_service=None,config=None,db=None):
    """Выбор способа оплаты: счёт создаётся только после подтверждения условий."""
    if not await private_callback(callback): return
    parts=(callback.data or '').split(':');data=await state.get_data()
    if (len(parts)!=4 or parts[2]!=data.get('purchase_nonce') or data.get('purchase_expires_at',0)<=time.time()
        or parts[3] not in {m['id'] for m in catalog_payload()['methods']}):
        await callback.answer('Выбор оплаты устарел. Откройте тариф заново.',show_alert=True);return
    product_id=data['purchase_product'];method=parts[3]
    try:
        product=product_view(product_id)
    except StopIteration:
        await callback.answer('Тариф недоступен. Откройте тариф заново.',show_alert=True);return
    source=data.get('purchase_source','more')
    await state.update_data(purchase_method=method)
    await edit_menu(callback.message,confirmation_text(product,method),
        reply_markup=confirmation_keyboard(product,source,data['purchase_nonce'],config))
    await callback.answer()


async def cb_accept_terms(callback,state,billing_service=None,db=None,config=None):
    """Подтверждение условий: единственная точка создания счёта из бота."""
    if not await private_callback(callback): return
    parts=(callback.data or '').split(':');data=await state.get_data()
    methods={m['id'] for m in catalog_payload()['methods']}
    products={p['product_id'] for p in catalog_payload()['products']}
    if (len(parts)!=3 or parts[2]!=data.get('purchase_nonce') or data.get('purchase_expires_at',0)<=time.time()
        or data.get('purchase_method') not in methods or data.get('purchase_product') not in products):
        await callback.answer('Выбор оплаты устарел. Откройте тариф заново.',show_alert=True);return
    product_id=data['purchase_product'];method=data['purchase_method']
    back=f"plus:show:{product_id}:{data.get('purchase_source','more')}"
    if method=='stars' and stars_checkout_ready(billing_service):
        # Единственный открытый способ: звёзды. Счёт создаётся на сервере, а
        # права выдаются только после подтверждения оплаты от Telegram.
        result=await billing_service.prepare_payment(
            callback.from_user.id,product_id,'stars',
            request_key=data['purchase_nonce'],now=time.time())
        await state.clear()
        text,rows=checkout_view(result,back)
        await edit_menu(callback.message,text,reply_markup=rows)
        await callback.answer();return
    result=billing_service.public_purchase(product_id,method)
    await state.clear()
    await edit_menu(callback.message,'<b>Оплата недоступна</b>\n\n'+html.escape(result['message'])+'\n\n<blockquote>Платёж не создан. Деньги не списаны.</blockquote>',reply_markup=back_keyboard(back))
    await callback.answer()
