"""Small Telegram entry points before legacy FSM input handlers."""
from ..telegram_home import edit_menu
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from ..telegram_ui import own_private, more_keyboard, back_keyboard, cancel_ui
from .streams import cmd_start, _owner_admin_url, _viewer_url


async def on_menu(message, state, db, config=None, oauth_server=None):
    if not own_private(message):
        return
    await cmd_start(message, state, db, config, oauth_server)


async def cb_more(callback, config=None, state=None, db=None, oauth_server=None):
    if callback.message is None:
        await callback.answer("Сообщение недоступно. Нажми «Меню».",show_alert=True)
        return
    if state is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=callback.message)
    url = _viewer_url(callback.message,config,actor_id=callback.from_user.id)
    await edit_menu(callback.message,"Ещё",reply_markup=more_keyboard(
        admin_url=_owner_admin_url(callback.message,config,actor_id=callback.from_user.id),
        legacy_viewer_url=url if url and url.endswith('/viewer') else None))
    await callback.answer()


async def cb_open_app(callback, config=None, state=None, db=None, oauth_server=None):
    msg = callback.message
    if state is not None and msg is not None:
        await cancel_ui(state,actor_id=callback.from_user.id,db=db,oauth_server=oauth_server,message=msg)
    url = _viewer_url(msg,config,actor_id=callback.from_user.id) if msg else None
    if url and url.endswith('/app'):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть приложение",web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="← На главную",callback_data="menu:home")]])
        await edit_menu(msg,"Открывай настройки в приложении.",reply_markup=keyboard)
    elif msg is not None:
        await msg.answer("Открой личный чат с ботом. Приложение доступно через кнопку «Приложение».",
                         reply_markup=back_keyboard('menu:home'))
    await callback.answer()


def build_navigation_router():
    router = Router(name="telegram_navigation")
    router.message.register(on_menu,F.text == "Меню")
    from aiogram.filters import StateFilter
    from ..telegram_lists import ListSearch, cb_list_page, cb_list_filter, cb_list_search, process_list_search, cb_delete_confirm
    router.message.register(process_list_search, StateFilter(ListSearch.waiting))
    router.callback_query.register(cb_list_page, F.data.startswith('listpage:'))
    router.callback_query.register(cb_list_filter, F.data.startswith('listfilter:'))
    router.callback_query.register(cb_list_search, F.data.startswith('listsearch:'))
    router.callback_query.register(cb_delete_confirm, F.data.startswith('untrackconfirm:'))
    router.callback_query.register(cb_more,F.data == "menu:more")
    router.callback_query.register(cb_open_app,F.data == "menu:open_app")
    from .telegram_add import cb_confirm_add, cb_pick_add
    router.callback_query.register(cb_confirm_add,F.data.startswith("addconfirm:"))
    router.callback_query.register(cb_pick_add,F.data.startswith("addpick:"))
    from .telegram_streamer import cb_streamer, cb_streamer_connect, cb_streamer_check, cb_streamer_channel, cb_streamer_posts, cb_streamer_benefits
    router.callback_query.register(cb_streamer,F.data == 'menu:streamer')
    router.callback_query.register(cb_streamer_connect,F.data == 'streamer:connect')
    router.callback_query.register(cb_streamer_check,F.data == 'streamer:check')
    router.callback_query.register(cb_streamer_channel,F.data == 'streamer:channel')
    from .telegram_streamer import cb_channel_help,cb_channel_resume,cb_streamer_readiness
    router.callback_query.register(cb_channel_help,F.data == 'streamer:channelhelp')
    router.callback_query.register(cb_channel_resume,F.data == 'streamer:channelresume')
    router.callback_query.register(cb_streamer_readiness,F.data == 'streamer:readiness')
    router.callback_query.register(cb_streamer_posts,F.data == 'streamer:posts')
    router.callback_query.register(cb_streamer_benefits,F.data == 'streamer:benefits')
    from .telegram_plus import cb_plus, cb_buy, cb_payment_method
    router.callback_query.register(cb_plus,(F.data == 'menu:plus') | F.data.startswith('plus:show:'))
    router.callback_query.register(cb_buy,F.data.startswith('plus:buy:'))
    router.callback_query.register(cb_payment_method,F.data.startswith('plus:pay:'))
    from .telegram_help import cb_help, cb_commands
    router.callback_query.register(cb_help,F.data == 'menu:help')
    router.callback_query.register(cb_commands,F.data == 'help:commands')
    return router
