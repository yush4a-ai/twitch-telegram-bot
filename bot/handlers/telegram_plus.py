"""Read-only Plus navigation over the same catalog and subscription contract."""
import secrets
import time
from datetime import datetime, timedelta, timezone
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from ..plan_catalog import catalog_payload
from ..subscription_state import SubscriptionService
from ..viewer_trial import ViewerTrialService
from ..telegram_ui import back_keyboard, cancel_ui
from .telegram_streamer import private_callback
from .streams import _viewer_url


def product_view(product_id):
    return next(p for p in catalog_payload()['products'] if p['product_id']==product_id)


def benefits(product):
    features=catalog_payload()['features']
    return '\n'.join('• '+features[feature]['title'] for feature in product['feature_ids'])


def offer_keyboard(product):
    viewer=product['product_id']=='viewer_plus'
    secondary=product_view('streamer_plus' if viewer else 'viewer_plus')
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Оформить {product['title']} — {product['price_label']}",callback_data='plus:buy:'+product['product_id'])],
        [InlineKeyboardButton(text='Plus для стримеров' if viewer else f"Только Viewer Plus — {secondary['price_label']}",
                              callback_data='plus:show:'+secondary['product_id'])],
        [InlineKeyboardButton(text='← Назад',callback_data='menu:more')]])


async def access_label(db,user_id,product_id,source,now):
    if source=='trial': return 'Ознакомительный доступ'
    if source=='test':
        trial=await ViewerTrialService(db).status(user_id,now=now)
        return 'Ознакомительный доступ' if product_id=='viewer_plus' and trial.active else 'Тестовый доступ'
    # Legacy mock checkout grants used source=paid. Do not call those monetary.
    row=await (await db.conn.execute(
        "SELECT 1 FROM billing_orders o JOIN entitlement_grants g ON g.grant_id=o.grant_id "
        "WHERE o.telegram_user_id=? AND o.plan=? AND o.provider='mock' "
        "AND g.revoked_at IS NULL AND g.starts_at<=? AND g.expires_at>? LIMIT 1",
        (user_id,product_id,now,now),
    )).fetchone()
    return 'Тестовый доступ' if row else 'Активна'


async def cb_plus(callback,state,db,config=None,oauth_server=None):
    if not await private_callback(callback): return
    actor=callback.from_user.id
    await cancel_ui(state,actor_id=actor,db=db,oauth_server=oauth_server)
    now=time.time();status=await SubscriptionService(db).state(actor,now=now)
    if callback.data=='menu:plus' and (status['streamer']['active'] or status['viewer']['active']):
        key='streamer' if status['streamer']['active'] else 'viewer'
        product=product_view(key+'_plus');active=status[key]
        label=await access_label(db,actor,product['product_id'],active['source'],now)
        end=datetime.fromtimestamp(active['expires_at'],timezone.utc).astimezone(timezone(timedelta(hours=3)))
        text=f"Моя подписка\n\n{product['title']}\nСтатус: {label}\nДо {end:%d.%m.%Y %H:%M} (МСК)\n\n"
        if key=='streamer': text+='Viewer Plus включён.\n\n'
        text+=benefits(product)
        if key=='streamer': text+='\n\n'+benefits(product_view('viewer_plus'))
        text+='\n\nАвтопродление выключено.'
        rows=[];url=_viewer_url(callback.message,config,actor_id=actor)
        if url and url.endswith('/app'):
            rows.append([InlineKeyboardButton(text='Управление подпиской',web_app=WebAppInfo(url=url+'?screen=subscription'))])
        rows.extend([[InlineKeyboardButton(text='Возможности Plus',callback_data='plus:show:'+product['product_id'])],
                     [InlineKeyboardButton(text='← Назад',callback_data='menu:more')]])
        await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        primary='streamer_plus' if status['streamer']['linked'] else 'viewer_plus'
        product_id=callback.data.removeprefix('plus:show:') if callback.data.startswith('plus:show:') else primary
        try: product=product_view(product_id)
        except StopIteration:
            await callback.answer('Тариф недоступен. Открой Plus заново.',show_alert=True);return
        text=f"Возможности Plus\n\n{product['title']}\n{product['price_label']} / {product['period_label']}\n\n"
        if product['includes']: text+='Viewer Plus уже включён для твоего Telegram-аккаунта.\n\n'
        text+=benefits(product)
        if product['includes']: text+='\n\n'+benefits(product_view('viewer_plus'))
        await callback.message.edit_text(text,reply_markup=offer_keyboard(product))
    await callback.answer()


async def cb_buy(callback,state,db,oauth_server=None):
    if not await private_callback(callback): return
    product_id=(callback.data or '').removeprefix('plus:buy:')
    try: product=product_view(product_id)
    except StopIteration:
        await callback.answer('Тариф недоступен. Открой Plus заново.',show_alert=True);return
    await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server)
    nonce=secrets.token_hex(8)
    await state.update_data(purchase_product=product_id,purchase_nonce=nonce,purchase_expires_at=time.time()+600)
    rows=[[InlineKeyboardButton(text=m['title'],callback_data=f"plus:pay:{nonce}:{m['id']}")] for m in catalog_payload()['methods']]
    rows.append([InlineKeyboardButton(text='← Назад',callback_data='menu:plus')])
    await callback.message.edit_text(f"{product['title']}\n{product['price_label']} / {product['period_label']}\n\nВыбери способ оплаты.\n"
        "Telegram Stars — через Telegram. СБП и банковская карта — через Platega.\n\nАвтопродление выключено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def cb_payment_method(callback,state,billing_service):
    if not await private_callback(callback): return
    parts=(callback.data or '').split(':');data=await state.get_data()
    if (len(parts)!=4 or parts[2]!=data.get('purchase_nonce') or data.get('purchase_expires_at',0)<=time.time()
        or parts[3] not in {m['id'] for m in catalog_payload()['methods']}):
        await callback.answer('Выбор оплаты устарел. Открой Plus заново.',show_alert=True);return
    result=billing_service.public_purchase(data['purchase_product'],parts[3])
    await state.clear()
    await callback.message.edit_text(result['message']+'\n\nПлатёж не создан. Деньги не списаны.',reply_markup=back_keyboard('menu:plus'))
    await callback.answer()
