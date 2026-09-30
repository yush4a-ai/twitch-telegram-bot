from __future__ import annotations

import logging
import time

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.enums import ChatType
from aiogram.types import Message

from ..config import Config
from ..database import Database
from ..oauth import OAuthCallbackServer, OAuthFlowError, run_authorization_flow

logger = logging.getLogger(__name__)
OAUTH_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

router = Router(name="auth")


async def _run_auth_flow(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer,
    *, streamer_user_id: int | None = None,
) -> None:
    async def send_url(url: str) -> None:
        await message.answer(
            "Перейди по ссылке, войди в свой Twitch-аккаунт и разреши доступ — после этого "
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
            await message.answer("Что-то пошло не так при авторизации. Попробуй ещё раз.")
            return

    if streamer_user_id is None:
        await db.save_user_token(
            result.login, result.broadcaster_id, result.access_token,
            result.refresh_token, result.expires_at,
        )
        await message.answer(f"Готово! Twitch-аккаунт «{result.login}» авторизован для подсчёта фолловеров.")
        return
    if not await db.save_verified_streamer_connection(
        streamer_user_id, result, verified_at=time.time(),
    ):
        await message.answer("Этот Twitch-аккаунт или твой Telegram уже связан с другим аккаунтом. Настройки не изменены.")
        return
    await message.answer(f"Готово! Twitch-аккаунт «{result.login}» подключён к твоему кабинету стримера.")


@router.message(Command("auth_twitch"))
async def cmd_auth_twitch(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer
) -> None:
    await _run_auth_flow(message, db, config, oauth_server)


@router.message(Command("streamer_connect"))
async def cmd_streamer_connect(
    message: Message, db: Database, config: Config, oauth_server: OAuthCallbackServer
) -> None:
    if (
        message.chat.type != ChatType.PRIVATE
        or message.from_user is None
        or message.from_user.id != message.chat.id
    ):
        await message.answer("Подключи кабинет стримера в личном чате с ботом.")
        return
    telegram_user_id = message.from_user.id
    await _run_auth_flow(
        message, db, config, oauth_server, streamer_user_id=telegram_user_id,
    )
