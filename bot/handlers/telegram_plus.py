"""Read-only Plus navigation over the same catalog and subscription contract."""
from ..telegram_home import edit_menu
import secrets
import time
import html
from datetime import datetime, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from ..plan_catalog import catalog_payload, PAYMENT_UNAVAILABLE_MESSAGE
from ..subscription_state import SubscriptionService
from ..telegram_ui import back_keyboard, cancel_ui
from .telegram_streamer import private_callback
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


def product_route(data, action):
    parts=(data or '').split(':')
    if len(parts) not in {3,4} or parts[:2]!=['plus',action]:
        raise ValueError('invalid product route')
    source=parts[3] if len(parts)==4 and parts[3] in {'more','streamer'} else 'more'
    return parts[2],source


def return_route(source):
    return 'menu:streamer' if source=='streamer' else 'menu:more'


def offer_keyboard(product,source='more'):
    viewer=product['product_id']=='viewer_plus'
    secondary=product_view('streamer_plus' if viewer else 'viewer_plus')
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оформить {product['title']}: {product['price_label']}",callback_data=f"plus:buy:{product['product_id']}:{source}")],
        [InlineKeyboardButton(text='Тариф для стримера' if viewer else f"Тариф для зрителя: {secondary['price_label']}",
                              callback_data=f"plus:show:{secondary['product_id']}:{source}")],
        [InlineKeyboardButton(text='← Назад',callback_data=return_route(source))]])


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


async def cb_plus(callback,state,db,config=None,oauth_server=None):
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
            await callback.answer('Тариф недоступен. Открой тариф заново.',show_alert=True);return
        text=f"<b>{html.escape(product['title'])}</b>\n" \
             f"<b>{product['price_label']} / {product['period_label'].removeprefix('1 ')}</b>\n\n" \
             "<b>Что включено</b>\n"
        if product['includes']: text+='В Стример Plus включены все возможности Зритель Plus.\n\n'
        text+=benefits(product)
        if product['includes']: text+='\n\n<b>Возможности зрителя</b>\n'+benefits(product_view('viewer_plus'))
        text+='\n\n<b>Оплата</b>\n'+PAYMENT_UNAVAILABLE_MESSAGE
        await edit_menu(callback.message,text,reply_markup=offer_keyboard(product,source))
    await callback.answer()


async def cb_buy(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    try:
        product_id,source=product_route(callback.data,'buy')
        product=product_view(product_id)
    except (StopIteration,ValueError):
        await callback.answer('Тариф недоступен. Открой тариф заново.',show_alert=True);return
    await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    nonce=secrets.token_hex(8)
    await state.update_data(purchase_product=product_id,purchase_source=source,purchase_nonce=nonce,purchase_expires_at=time.time()+600)
    rows=[[InlineKeyboardButton(text=m['title'],callback_data=f"plus:pay:{nonce}:{m['id']}")] for m in catalog_payload()['methods']]
    rows.append([InlineKeyboardButton(text='← Назад',callback_data=f'plus:show:{product_id}:{source}')])
    await edit_menu(callback.message,f"<b>{html.escape(product['title'])}</b>\n<b>{product['price_label']} / {product['period_label'].removeprefix('1 ')}</b>\n\n"
        "<b>Выбери способ оплаты</b>\n<blockquote>Telegram Stars: через Telegram.\nСБП и банковская карта: через Platega.</blockquote>\n\n"
        +PAYMENT_UNAVAILABLE_MESSAGE+"\n\nАвтопродление выключено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def cb_payment_method(callback,state,billing_service):
    if not await private_callback(callback): return
    parts=(callback.data or '').split(':');data=await state.get_data()
    if (len(parts)!=4 or parts[2]!=data.get('purchase_nonce') or data.get('purchase_expires_at',0)<=time.time()
        or parts[3] not in {m['id'] for m in catalog_payload()['methods']}):
        await callback.answer('Выбор оплаты устарел. Открой тариф заново.',show_alert=True);return
    result=billing_service.public_purchase(data['purchase_product'],parts[3])
    await state.clear()
    back=f"plus:show:{data['purchase_product']}:{data.get('purchase_source','more')}"
    await edit_menu(callback.message,'<b>Оплата недоступна</b>\n\n'+html.escape(result['message'])+'\n\n<blockquote>Платёж не создан. Деньги не списаны.</blockquote>',reply_markup=back_keyboard(back))
    await callback.answer()
