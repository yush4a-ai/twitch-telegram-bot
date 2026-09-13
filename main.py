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
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)
from aiogram.utils.token import TokenValidationError

from bot.chat_listener import ChatListener
from bot.follow_listener import FollowEventListener
from bot.config import (
    ConfigError,
    PreviewCaptureConfig,
    PreviewRuntimeConfig,
    is_railway_environment,
    load_config,
)
from bot.database import Database, DatabaseConfigurationError
from bot.handlers import register_all_handlers
from bot.logging_utils import mask_chat_id
from bot.live_preview_provider import LivePreviewArtifactProvider
from bot.live_post import LivePostUpdater
from bot.middlewares import setup_middlewares
from bot.oauth import (
    REDIRECT_PATH,
    OAuthCallbackServer,
    evaluate_runtime_health,
)
from bot.poller import StreamPoller
from bot.preview_analysis import HighlightAnalyzer
from bot.preview_capture import CaptureService, CaptureSettings
from bot.preview_render import PreviewRenderer
from bot.preview_runtime import (
    DisabledPreviewObserver,
    NoopPreviewArtifactProvider,
    PreviewManager,
)
from bot.preview_source import (
    TwitchCaptureSource,
    TwitchPlaybackResolver,
    probe_streamlink,
)
from bot.token_store import TokenStore
from bot.twitch import TwitchClient

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# консоль Windows по умолчанию использует не-UTF-8 кодировку (обычно cp1251),
# из-за чего кириллица в логах превращается в кракозябры — принудительно
# переключаем stdout на UTF-8
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8")

_log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def _build_logging_handlers(log_dir: str) -> list[logging.Handler]:
    """Всегда оставляет консольный лог, даже если filesystem Railway read-only."""
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_log_formatter)
    handlers: list[logging.Handler] = [console_handler]
    try:
        os.makedirs(log_dir, exist_ok=True)
        # ротация: новый файл после 5 МБ, храним 5 старых архивов
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "bot.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    except OSError as e:
        print(f"Файловый лог недоступен, продолжаю только с консолью: {e}", file=sys.stderr)
    else:
        file_handler.setFormatter(_log_formatter)
        handlers.append(file_handler)
    return handlers


logging.basicConfig(level=logging.INFO, handlers=_build_logging_handlers(LOG_DIR))
logger = logging.getLogger(__name__)

# при старте сразу после включения компьютера сеть (VPN-туннель) иногда ещё не готова —
# даём ей время подняться вместо мгновенного фатального падения
STARTUP_NETWORK_RETRIES = 10
STARTUP_RETRY_DELAY_SECONDS = 5
SHUTDOWN_STEP_TIMEOUT_SECONDS = 3


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


async def _safe_cleanup(description: str, awaitable) -> None:
    """Не даёт сбою одного cleanup-шагa пропустить все последующие ресурсы."""
    try:
        await asyncio.wait_for(awaitable, timeout=SHUTDOWN_STEP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error(
            "Cleanup превысил timeout %sс: %s",
            SHUTDOWN_STEP_TIMEOUT_SECONDS,
            description,
        )
    except asyncio.CancelledError:
        logger.warning("Cleanup отменён: %s", description)
    except Exception:
        logger.exception("Ошибка cleanup: %s", description)


async def _cancel_task(task: asyncio.Task | None, description: str) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    done, _pending = await asyncio.wait(
        {task}, timeout=SHUTDOWN_STEP_TIMEOUT_SECONDS
    )
    if not done:
        logger.error(
            "Задача %s не завершилась за %sс cleanup-timeout",
            description,
            SHUTDOWN_STEP_TIMEOUT_SECONDS,
        )
        return
    if task.cancelled():
        return
    try:
        task.result()
    except BaseException as error:
        logger.error("Задача %s завершилась ошибкой при cleanup: %s", description, error)


async def _safe_preview_cleanup(description: str, awaitable) -> bool:
    """Закрывает preview-ресурс без утечки деталей ошибки в production log."""
    try:
        await asyncio.wait_for(awaitable, timeout=SHUTDOWN_STEP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error(
            "Preview cleanup превысил timeout %sс: %s",
            SHUTDOWN_STEP_TIMEOUT_SECONDS,
            description,
        )
    except asyncio.CancelledError:
        logger.warning("Preview cleanup отменён: %s", description)
    except Exception as error:
        logger.error(
            "Ошибка preview cleanup: %s (%s)",
            description,
            type(error).__name__,
        )
    else:
        return True
    return False


async def _close_preview_capture_service(capture_service, description: str) -> bool:
    return await _safe_preview_cleanup(description, capture_service.close())


async def _build_preview_runtime(
    db,
    live_post_updater,
    *,
    preview_config: PreviewRuntimeConfig,
    capture_config: PreviewCaptureConfig,
    poll_interval_seconds: float,
    build_content,
    capture_owner=None,
):
    provider = NoopPreviewArtifactProvider()
    capture_service = None
    enabled = preview_config.enabled
    disabled_reason = preview_config.disabled_reason

    async def close_partial_capture() -> bool:
        nonlocal capture_service
        if capture_service is None:
            return True
        service = capture_service
        closed = await _close_preview_capture_service(
            service, "Preview CaptureService startup rollback"
        )
        if closed:
            capture_service = None
            if capture_owner is not None:
                capture_owner(None)
        return closed

    if enabled and not capture_config.enabled:
        enabled = False
        disabled_reason = capture_config.disabled_reason or "config_error"

    if enabled:
        try:
            streamlink_capability = await probe_streamlink()
            if not streamlink_capability.available:
                enabled = False
                disabled_reason = "streamlink_unavailable"
            else:
                capture_service = await CaptureService.create(
                    settings=CaptureSettings(
                        buffer_seconds=capture_config.buffer_seconds,
                        buffer_max_bytes=capture_config.buffer_max_bytes,
                    )
                )
                if capture_owner is not None:
                    capture_owner(capture_service)
                if not capture_service.capability.available:
                    enabled = False
                    disabled_reason = "capture_unavailable"
                    await close_partial_capture()
                else:
                    renderer = PreviewRenderer.create()
                    render_capability = await renderer.capability()
                    if not render_capability.available:
                        enabled = False
                        disabled_reason = "render_unavailable"
                        await close_partial_capture()
                    else:
                        source = TwitchCaptureSource(
                            TwitchPlaybackResolver(streamlink_capability),
                            capture_service,
                        )
                        provider = LivePreviewArtifactProvider(
                            source=source,
                            analyzer=HighlightAnalyzer(),
                            renderer=renderer,
                        )
        except asyncio.CancelledError:
            await close_partial_capture()
            raise
        except Exception as error:
            logger.error(
                "Preview runtime не собран; core продолжает работу: %s",
                type(error).__name__,
            )
            enabled = False
            disabled_reason = "startup_error"
            await close_partial_capture()

    try:
        manager = PreviewManager(
            db,
            live_post_updater,
            provider,
            enabled=enabled,
            disabled_reason=disabled_reason,
            initial_delay_seconds=preview_config.initial_delay_seconds,
            interval_seconds=preview_config.interval_seconds,
            max_concurrent_jobs=preview_config.max_concurrent_jobs,
            job_timeout_seconds=preview_config.job_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            build_content=build_content,
        )
    except Exception as error:
        logger.error(
            "Preview runtime не собран; core продолжает работу: %s",
            type(error).__name__,
        )
        await close_partial_capture()
        return DisabledPreviewObserver("startup_error"), capture_service
    return manager, capture_service


async def _start_preview_runtime(preview_manager, capture_service):
    manager_enabled = capture_service is not None
    try:
        manager_enabled = bool(preview_manager.health_snapshot().get("enabled"))
    except (AttributeError, TypeError):
        pass

    start_error: Exception | None = None
    try:
        preview_task = preview_manager.start()
    except Exception as error:
        start_error = error
        preview_task = None

    if start_error is None and (not manager_enabled or preview_task is not None):
        if not manager_enabled and capture_service is not None:
            if await _close_preview_capture_service(
                capture_service, "Preview CaptureService startup rollback retry"
            ):
                capture_service = None
        return preview_manager, capture_service

    logger.error(
        "Preview runtime не стартовал; core продолжает работу: %s",
        type(start_error).__name__ if start_error is not None else "startup_error",
    )
    await _safe_preview_cleanup(
        "PreviewManager startup rollback", preview_manager.shutdown()
    )
    if capture_service is not None:
        if await _close_preview_capture_service(
            capture_service, "Preview CaptureService startup rollback"
        ):
            capture_service = None
    return DisabledPreviewObserver("startup_error"), capture_service


async def _shutdown_preview_runtime(preview_manager, capture_service) -> None:
    if preview_manager is not None:
        await _safe_preview_cleanup("PreviewManager", preview_manager.shutdown())
    if capture_service is not None:
        await _close_preview_capture_service(
            capture_service, "Preview CaptureService"
        )


async def _log_known_chats(db: Database) -> None:
    """Печатает в лог чаты без раскрытия реальных chat_id."""
    channels = await db.all_telegram_channels()
    if channels:
        logger.info("Подключённые Telegram-каналы (псевдоним — название):")
        for chat_id, title in channels:
            logger.info("    %s — %s", mask_chat_id(chat_id), title)
    else:
        logger.info("Telegram-каналов, где бот админ, пока нет")

    group_ids = await db.all_distinct_group_chat_ids()
    if group_ids:
        logger.info(
            "Группы с отслеживаемыми каналами: %s",
            ", ".join(mask_chat_id(g) for g in group_ids),
        )


async def _apply_auto_track(db: Database, config) -> None:
    """Заводит подписки, перечисленные в AUTO_TRACK.

    Обходной путь для ситуации, когда бот уже добавлен в канал, а указать Twitch-канал
    через меню возможности нет. Повторный запуск безопасен: add_channel не создаёт
    дубликатов, поэтому переменную можно спокойно оставить в настройках."""
    if not config.auto_track:
        return
    for chat_id, login in config.auto_track:
        try:
            created = await db.add_channel(chat_id, login)
        except Exception:
            logger.exception(
                "AUTO_TRACK: не удалось добавить %s в чат %s",
                login,
                mask_chat_id(chat_id),
            )
            continue
        if created:
            logger.info(
                "AUTO_TRACK: канал %s добавлен в чат %s",
                login,
                mask_chat_id(chat_id),
            )
        else:
            logger.info(
                "AUTO_TRACK: канал %s в чате %s уже отслеживается",
                login,
                mask_chat_id(chat_id),
            )


async def _reconcile_telegram_channels(bot: Bot, db: Database) -> None:
    """Удаляет stale-регистрации каналов, недоступных боту после рестарта."""
    for chat_id, _title in await db.all_telegram_channels():
        try:
            chat = await bot.get_chat(chat_id)
            member = await bot.get_chat_member(chat_id, bot.id)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning(
                "Telegram-канал %s больше недоступен, удаляю stale-регистрацию: %s",
                mask_chat_id(chat_id),
                e,
            )
        except TelegramNetworkError:
            raise
        except Exception:
            logger.exception(
                "Не удалось проверить регистрацию Telegram-канала %s; запись сохранена",
                mask_chat_id(chat_id),
            )
            continue
        else:
            if chat.type == ChatType.CHANNEL and member.status == "administrator":
                continue
            logger.warning(
                "Чат %s больше не является доступным Telegram-каналом; "
                "удаляю stale-регистрацию",
                mask_chat_id(chat_id),
            )

        removed = await db.remove_all_channels(chat_id)
        logger.info(
            "Stale Telegram-канал %s очищен; снято Twitch-подписок: %s",
            mask_chat_id(chat_id),
            removed,
        )


async def main() -> None:
    config = load_config()

    db = Database(config.db_path, token_encryption_key=config.token_encryption_key)
    bot: Bot | None = None
    try:
        await db.connect()
        # Между остановкой старого процесса и запуском нового EventSub не слушается:
        # текущий эфир уже нельзя считать полностью покрытым событиями.
        await db.invalidate_live_follow_counts_after_restart()
        # ChatListener держит активность, чатеров, топ и рейды в RAM. После restart
        # текущая logical session продолжается, но её chat stats уже неполны.
        await db.invalidate_live_chat_stats_after_restart()
        await _log_known_chats(db)
        await _apply_auto_track(db, config)

        bot = Bot(
            token=config.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        live_post_updater = LivePostUpdater(bot, db)
        await _with_startup_retry(
            lambda: _reconcile_telegram_channels(bot, db),
            "Проверка Telegram-каналов",
        )
        dp = Dispatcher()
        setup_middlewares(dp)
        register_all_handlers(dp)

        if config.owner_chat_id is None:
            logger.warning(
                "OWNER_CHAT_ID не задан — команда /stats и алерты о банах каналов работать не будут"
            )

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

        # общий таймаут на все исходящие запросы: без него зависшее соединение
        # держит цикл опроса до дефолтных пяти минут aiohttp
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30, connect=10)
        ) as session:
            twitch = TwitchClient(config.twitch_client_id, config.twitch_client_secret, session)
            token_store = TokenStore(
                db, config.twitch_client_id, config.twitch_client_secret, session
            )
            chat_listener = ChatListener(session)
            follow_listener = FollowEventListener(
                db, token_store, config.twitch_client_id, session
            )
            oauth_server = OAuthCallbackServer(
                redirect_uri=f"{config.oauth_public_base_url}{REDIRECT_PATH}",
                host=config.oauth_host,
                port=config.oauth_port,
            )
            follow_listener_task: asyncio.Task | None = None
            poller: StreamPoller | None = None
            preview_manager = None
            preview_capture_service = None
            poller_task: asyncio.Task | None = None
            polling_task: asyncio.Task | None = None
            try:
                await oauth_server.start()

                dp["db"] = db
                dp["twitch"] = twitch
                dp["config"] = config
                dp["oauth_server"] = oauth_server
                dp["follow_listener"] = follow_listener
                dp["token_store"] = token_store

                follow_listener_task = asyncio.create_task(follow_listener.run())
                await follow_listener.wait_initial_ready()

                preview_config = getattr(config, "preview", PreviewRuntimeConfig())
                capture_config = getattr(
                    config, "preview_capture", PreviewCaptureConfig()
                )

                def own_preview_capture(service) -> None:
                    nonlocal preview_capture_service
                    preview_capture_service = service

                preview_manager, preview_capture_service = (
                    await _build_preview_runtime(
                        db,
                        live_post_updater,
                        preview_config=preview_config,
                        capture_config=capture_config,
                        poll_interval_seconds=config.poll_interval_seconds,
                        build_content=lambda observation, destination: (
                            poller.build_preview_content(observation, destination)
                        ),
                        capture_owner=own_preview_capture,
                    )
                )

                poller = StreamPoller(
                    bot,
                    db,
                    twitch,
                    config.poll_interval_seconds,
                    token_store=token_store,
                    chat_listener=chat_listener,
                    follow_listener=follow_listener,
                    owner_chat_id=config.owner_chat_id,
                    live_post_updater=live_post_updater,
                    preview_observer=preview_manager,
                )
                dp["poller"] = poller
                dp["preview_manager"] = preview_manager
                started_preview_manager, preview_capture_service = (
                    await _start_preview_runtime(
                        preview_manager, preview_capture_service
                    )
                )
                if started_preview_manager is not preview_manager:
                    preview_manager = started_preview_manager
                    poller.set_preview_observer(preview_manager)
                    dp["preview_manager"] = preview_manager
                poller_task = asyncio.create_task(poller.run())

                # Только теперь runtime собран целиком, и /healthz может отвечать
                # ok вместо starting. Provider синхронный и читает готовые
                # in-memory snapshot, поэтому HTTP-запрос не трогает ни SQLite,
                # ни Twitch, ни Telegram.
                def _runtime_health(
                    poller: StreamPoller = poller,
                    follow_listener: FollowEventListener = follow_listener,
                ) -> tuple[bool, str]:
                    now = time.time()
                    return evaluate_runtime_health(
                        poller.health_snapshot(now),
                        follow_listener.health_snapshot(now),
                        now,
                    )

                oauth_server.set_health_provider(_runtime_health)

                await _with_startup_retry(
                    lambda: bot.delete_webhook(drop_pending_updates=True), "Удаление webhook"
                )
                polling_task = asyncio.create_task(
                    dp.start_polling(bot, close_bot_session=False)
                )
                done, _pending = await asyncio.wait(
                    {polling_task, poller_task, follow_listener_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task, name in (
                    (poller_task, "StreamPoller"),
                    (follow_listener_task, "FollowEventListener"),
                ):
                    if task in done:
                        await task
                        raise RuntimeError(f"{name} неожиданно завершился")
                await polling_task
            finally:
                # Draining начался: снимаем provider, чтобы /healthz сразу отдавал
                # 503 и балансировщик перестал считать инстанс здоровым, пока
                # сервер ещё дослуживает текущие запросы.
                oauth_server.set_health_provider(None)
                if poller is not None:
                    poller.stop()
                await _cancel_task(polling_task, "Telegram polling")
                await _cancel_task(poller_task, "StreamPoller")
                await _shutdown_preview_runtime(
                    preview_manager, preview_capture_service
                )
                if poller is not None:
                    await _safe_cleanup("StreamPoller", poller.shutdown())
                await _safe_cleanup("FollowEventListener", follow_listener.stop())
                await _cancel_task(follow_listener_task, "FollowEventListener")
                await _safe_cleanup("OAuth callback server", oauth_server.stop())
    finally:
        if bot is not None:
            await _safe_cleanup("Telegram session", bot.session.close())
        await _safe_cleanup("SQLite", db.close())


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
        except (ConfigError, DatabaseConfigurationError, TokenValidationError):
            logger.exception(
                "Постоянная ошибка конфигурации; автоматический restart не поможет"
            )
            raise SystemExit(2)
        except Exception:
            if is_railway_environment():
                logger.exception(
                    "Бот упал с необработанной ошибкой; restart передан Railway"
                )
                raise
            logger.exception(
                "Бот упал с необработанной ошибкой, перезапуск через %sс", RESTART_DELAY_SECONDS
            )
            time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    run_forever()
