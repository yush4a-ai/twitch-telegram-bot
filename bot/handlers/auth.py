from __future__ import annotations

import logging
import asyncio
from urllib.parse import urlsplit, parse_qs
from aiogram.fsm.context import FSMContext
import time

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.enums import ChatType
from aiogram.types import Message
from ..telegram_ui import begin_legacy_oauth, legacy_oauth_current
from ..telegram_home import send_menu_keyboard, recover_menu_keyboard

from ..config import Config
from ..database import Database
from ..oauth import OAuthCallbackServer, OAuthFlowError, run_authorization_flow
from ..mini_app_streamer import complete_community_intent

logger = logging.getLogger(__name__)
OAUTH_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

router = Router(name="auth")


@router.message(F.chat_shared)
async def on_streamer_community_shared(message: Message, db: Database, state: FSMContext | None = None) -> None:
    if (
        message.chat.type != ChatType.PRIVATE or message.from_user is None
        or message.from_user.id != message.chat.id or message.chat_shared is None
    ):
        return
    shared = message.chat_shared
    previous = await (await db.conn.execute(
        "SELECT status FROM streamer_community_intents WHERE telegram_user_id=? AND request_id=?",
        (message.from_user.id, shared.request_id),
    )).fetchone()
    replay=bool(previous and previous[0]=='connected')
    saved = replay or await complete_community_intent(
        db, message.bot, message.from_user.id, shared.request_id,
        shared.chat_id, now=time.time(),
    )
    if state is not None:
        data=await state.get_data()
        intent=data.get('telegram_community_intent')
        row=await db.get_community_intent(intent) if intent else None
        if (row and row[1]==message.from_user.id and row[2]==shared.request_id
            and (await state.get_data()).get('telegram_community_intent')==intent):
            await state.clear()
    if replay:
        await recover_menu_keyboard(message,state)
    else:
        await send_menu_keyboard(message,'Telegram-канал подключён. Настройки публикаций доступны в приложении.' if saved
                                 else 'Канал не подключён. Выберите его заново и проверьте права бота.',force=True,state=state)


async def _run_auth_flow(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer,
    *, streamer_user_id: int | None = None, state: FSMContext | None = None,
) -> None:
    generation=await begin_legacy_oauth(state, actor_id=streamer_user_id or message.chat.id,
                                      db=db, oauth_server=oauth_server, message=message)
    async def send_url(url: str) -> None:
        if not await legacy_oauth_current(state,generation): raise asyncio.CancelledError
        if state is not None:
            oauth_state=parse_qs(urlsplit(url).query).get('state',[None])[0]
            await state.update_data(legacy_oauth_state=oauth_state)
        await message.answer(
            "Перейдите по ссылке, войдите в свой Twitch-аккаунт и разрешите доступ. После этого "
            f"бот сможет считать число новых фолловеров (ссылка активна 5 минут):\n{url}"
        )

    async with aiohttp.ClientSession(timeout=OAUTH_HTTP_TIMEOUT) as session:
        try:
            result = await run_authorization_flow(
                config.twitch_client_id,
                config.twitch_client_secret,
                session,
                oauth_server,
                on_url_ready=send_url,
            )
        except OAuthFlowError as e:
            await message.answer(f"Авторизация не удалась: {e}")
            return
        except Exception:
            logger.exception("Ошибка при авторизации Twitch")
            await message.answer("Что-то пошло не так при авторизации. Попробуйте ещё раз.")
            return

    if not await legacy_oauth_current(state,generation): return
    if streamer_user_id is None:
        await db.save_user_token(
            result.login, result.broadcaster_id, result.access_token,
            result.refresh_token, result.expires_at,
            telegram_user_id=streamer_user_id or message.chat.id,
        )
        await message.answer(f"Готово! Twitch-аккаунт «{result.login}» авторизован для подсчёта фолловеров.")
        return
    if not await db.save_verified_streamer_connection(
        streamer_user_id, result, verified_at=time.time(),
    ):
        await message.answer("Этот Twitch-аккаунт или ваш Telegram уже связан с другим аккаунтом. Настройки не изменены.")
        return
    await message.answer(f"Готово! Twitch-аккаунт «{result.login}» подключён к вашему кабинету стримера.")


@router.message(Command("auth_twitch"))
async def cmd_auth_twitch(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer, state: FSMContext | None = None
) -> None:
    await _run_auth_flow(message, db, config, oauth_server, state=state)


@router.message(Command("streamer_connect"))
async def cmd_streamer_connect(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer, state: FSMContext | None = None
) -> None:
    if (
        message.chat.type != ChatType.PRIVATE
        or message.from_user is None
        or message.from_user.id != message.chat.id
    ):
        await message.answer("Подключите кабинет стримера в личном чате с ботом.")
        return
    telegram_user_id = message.from_user.id
    await _run_auth_flow(
        message, db, config, oauth_server, streamer_user_id=telegram_user_id, state=state,
    )
