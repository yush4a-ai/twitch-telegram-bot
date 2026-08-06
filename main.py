from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import os
import sys
import time

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)

from bot.chat_listener import ChatListener
from bot.config import load_config
from bot.database import Database
from bot.handlers import register_all_handlers
from bot.oauth import REDIRECT_PATH, OAuthCallbackServer
from bot.poller import StreamPoller
from bot.token_store import TokenStore
from bot.twitch import TwitchClient

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# консоль Windows по умолчанию использует не-UTF-8 кодировку (обычно cp1251),
# из-за чего кириллица в логах превращается в кракозябры — принудительно
# переключаем stdout на UTF-8
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

# ротация: новый файл после 5 МБ, храним 5 старых архивов
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
logger = logging.getLogger(__name__)

# при старте сразу после включения компьютера сеть (VPN-туннель) иногда ещё не готова —
# даём ей время подняться вместо мгновенного фатального падения
STARTUP_NETWORK_RETRIES = 10
STARTUP_RETRY_DELAY_SECONDS = 5


async def _with_startup_retry(coro_factory, description: str) -> None:
    for attempt in range(1, STARTUP_NETWORK_RETRIES + 1):
        try:
            await coro_factory()
            return
        except TelegramNetworkError as e:
            if attempt == STARTUP_NETWORK_RETRIES:
                raise
            logger.warning(
                "%s: сеть недоступна (попытка %s/%s), жду %sс — %s",
                description, attempt, STARTUP_NETWORK_RETRIES, STARTUP_RETRY_DELAY_SECONDS, e,
            )
            await asyncio.sleep(STARTUP_RETRY_DELAY_SECONDS)


async def main() -> None:
    config = load_config()

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(
        token=config.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    register_all_handlers(dp)

    tracking_commands = [
        BotCommand(command="track", description="➕ Начать следить за Twitch-каналом"),
        BotCommand(command="untrack", description="❌ Перестать следить за каналом"),
        BotCommand(command="list", description="📡 Показать отслеживаемые каналы"),
        BotCommand(command="live", description="🔴 Кто сейчас в эфире"),
        BotCommand(command="report", description="📊 Отчёт по последнему стриму"),
        BotCommand(command="help", description="ℹ️ Что умеет бот"),
    ]

    await _with_startup_retry(
        lambda: bot.set_my_commands(
            [
                BotCommand(command="start", description="🏠 Главное меню бота"),
                *tracking_commands,
                BotCommand(command="import_follows", description="📥 Импорт подписок с Twitch"),
                BotCommand(command="auth_twitch", description="🔐 Подключить Twitch-аккаунт"),
                BotCommand(command="myid", description="🆔 Узнать chat_id этого чата"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        ),
        "Регистрация команд (личка)",
    )
    await _with_startup_retry(
        lambda: bot.set_my_commands(tracking_commands, scope=BotCommandScopeAllGroupChats()),
        "Регистрация команд (группы)",
    )
    await _with_startup_retry(
        lambda: bot.set_chat_menu_button(menu_button=MenuButtonCommands()),
        "Установка кнопки меню",
    )

    async with aiohttp.ClientSession() as session:
        twitch = TwitchClient(config.twitch_client_id, config.twitch_client_secret, session)
        token_store = TokenStore(db, config.twitch_client_id, config.twitch_client_secret, session)
        chat_listener = ChatListener(session)

        oauth_server = OAuthCallbackServer(
            redirect_uri=f"{config.oauth_public_base_url}{REDIRECT_PATH}",
            host=config.oauth_host,
            port=config.oauth_port,
        )
        await oauth_server.start()

        dp["db"] = db
        dp["twitch"] = twitch
        dp["config"] = config
        dp["oauth_server"] = oauth_server

        poller = StreamPoller(
            bot,
            db,
            twitch,
            config.poll_interval_seconds,
            token_store=token_store,
            chat_listener=chat_listener,
            owner_chat_id=config.owner_chat_id,
        )
        poller_task = asyncio.create_task(poller.run())

        try:
            await _with_startup_retry(
                lambda: bot.delete_webhook(drop_pending_updates=True), "Удаление webhook"
            )
            await dp.start_polling(bot)
        finally:
            poller.stop()
            await poller_task
            await oauth_server.stop()
            await db.close()
            await bot.session.close()


# если процесс упадёт по неожиданной причине (не Ctrl+C), не завершаемся молча —
# логируем и пробуем поднять бота заново, а не оставлять его мёртвым до ручного перезапуска
RESTART_DELAY_SECONDS = 30


def run_forever() -> None:
    while True:
        try:
            asyncio.run(main())
            return  # main() завершился штатно (не должно происходить в норме)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Остановлено.")
            return
        except Exception:
            logger.exception(
                "Бот упал с необработанной ошибкой, перезапуск через %sс", RESTART_DELAY_SECONDS
            )
            time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    run_forever()
