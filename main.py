from __future__ import annotations

import asyncio
import io
import logging
import logging.handlers
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import aiohttp
from aiogram import Bot
from bot.telegram_replay import ReplayDispatcher as Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)
from aiogram.utils.token import TokenValidationError

from bot.chat_listener import ChatListener
from bot.follow_listener import FollowEventListener
from bot.config import (
    ConfigError,
    PreviewCaptureConfig,
    PreviewRuntimeConfig,
    contour_bot_username_allowed,
    environment_label,
    is_railway_environment,
    load_config,
)
from bot.database import Database, DatabaseConfigurationError
from bot.db_backup import run_backup_loop
from bot.deep_links import TELEGRAM_BOT_USERNAME
from bot.handlers import register_all_handlers
from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy
from bot.config import first_release_payment_policy
from bot.plan_catalog import PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION, PLUS_TERMS_VERSION
from bot.stars_provider import TelegramStarsProvider
from bot.admin_auth import AdminAccess
from bot.streamer_auth import StreamerAccess
from bot.admin_metrics import AdminSnapshot
from bot.admin_directory import AdminDirectory
from bot.logging_utils import mask_chat_id
from bot.live_preview_provider import LivePreviewArtifactProvider
from bot.live_post import LivePostUpdater
from bot.middlewares import setup_middlewares
from bot.oauth import (
    REDIRECT_PATH,
    OAuthCallbackServer,
    evaluate_runtime_health,
)
from bot.poller import StreamPoller, TelegramChannelUsernameCache
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationWorker
from bot.broadcast_worker import BroadcastWorker
from bot.owner_alerts import OwnerAlerter
from bot.dialogue_retention import DEFAULT_RETENTION_DAYS, run_retention_loop
from bot.handlers.broadcasts import OPTOUT_CALLBACK, OPTOUT_TEXT
from bot.telegram_send_budget import TelegramSendBudget
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


def _private_bot_commands(
    tracking_commands: list[BotCommand], *, owner: bool = False,
    growth_enabled: bool = False,
) -> list[BotCommand]:
    commands = [
        BotCommand(command="start", description="🏠 Главное меню бота"),
        *tracking_commands,
        BotCommand(command="import_follows", description="📥 Импорт подписок с Twitch"),
        BotCommand(command="auth_twitch", description="🔐 Подключить Twitch-аккаунт"),
        BotCommand(command="streamer_connect", description="🎮 Подключить кабинет стримера"),
        BotCommand(command="myid", description="🆔 Узнать chat_id этого чата"),
        BotCommand(command="paysupport", description="Поддержка по подписке и оплате"),
        BotCommand(command="terms", description="Условия подписки и документы"),
    ]
    if growth_enabled:
        commands.append(BotCommand(command="invite", description="🔗 Пригласить в тестовый бот"))
    if owner:
        commands.append(BotCommand(command="admin", description="🛡️ Админ-панель"))
    return commands


def _menu_button_for_config(config) -> MenuButtonCommands | MenuButtonWebApp:
    base_url = str(getattr(config, "oauth_public_base_url", "") or "").rstrip("/")
    if (getattr(config, 'production_admitted', False)
            and getattr(config, 'production_contract', None) is not None
            and getattr(config, 'mini_app_enabled', False)):
        return MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=f"{base_url}/app"))
    if (
        getattr(config, "mini_app_enabled", False)
        and getattr(config, "pinned_staging", False)
        and str(getattr(config, "admin_telegram_bot_username", "") or "").casefold()
        == "signalstreamsbot"
        and base_url.startswith("https://")
    ):
        return MenuButtonWebApp(text="Приложение", web_app=WebAppInfo(url=f"{base_url}/app"))
    return MenuButtonCommands()


async def _verify_staging_bot_identity(bot: Bot, config) -> None:
    if not (getattr(config, "mini_app_enabled", False)
            and getattr(config, "pinned_staging", False)):
        return
    identity = await bot.get_me()
    if not contour_bot_username_allowed(getattr(identity, "username", "")):
        raise ConfigError("Mini App staging token должен принадлежать тестовому боту")


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
    send_budget=None,
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
            simple_initial_delay_seconds=preview_config.simple_initial_delay_seconds,
            interval_seconds=preview_config.interval_seconds,
            max_concurrent_jobs=preview_config.max_concurrent_jobs,
            max_active_sessions=preview_config.max_active_sessions,
            send_budget=send_budget,
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


async def _reconcile_telegram_channels(
    bot: Bot,
    db: Database,
    channel_username_cache: TelegramChannelUsernameCache | None = None,
) -> None:
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
                if channel_username_cache is not None:
                    channel_username_cache.update(chat_id, getattr(chat, "username", None))
                continue
            logger.warning(
                "Чат %s больше не является доступным Telegram-каналом; "
                "удаляю stale-регистрацию",
                mask_chat_id(chat_id),
            )

        removed = await db.remove_all_channels(chat_id)
        if channel_username_cache is not None:
            channel_username_cache.discard(chat_id)
        logger.info(
            "Stale Telegram-канал %s очищен; снято Twitch-подписок: %s",
            mask_chat_id(chat_id),
            removed,
        )


# Подпись к фото в Telegram ограничена 1024 символами, а текст сообщения — 4096.
TELEGRAM_CAPTION_LIMIT = 1024


def _broadcast_keyboard(campaign: dict) -> InlineKeyboardMarkup:
    """Кнопка владельца (если он её включил) и обязательный отказ от рассылок."""
    rows: list[list[InlineKeyboardButton]] = []
    button_text = campaign.get("button_text")
    button_url = campaign.get("button_url")
    if button_text and button_url:
        rows.append([InlineKeyboardButton(text=button_text, url=button_url)])
    rows.append([InlineKeyboardButton(text=OPTOUT_TEXT, callback_data=OPTOUT_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_with_markup(send) -> None:
    """Сначала разметка мастера, а при отказе Telegram — тот же текст простым.

    Разметка не должна ронять рассылку: «<3» или незакрытый тег отвергли бы
    сообщение на каждом получателе, поэтому второй попыткой текст уходит как есть.
    """
    try:
        await send(ParseMode.HTML)
    except TelegramBadRequest:
        await send(None)


def _make_broadcast_sender(bot: Bot):
    async def send(user_id: int, campaign: dict) -> None:
        keyboard = _broadcast_keyboard(campaign)
        body = campaign["body"]
        image_path = campaign.get("image_path")
        if image_path and Path(image_path).is_file():
            if len(body) <= TELEGRAM_CAPTION_LIMIT:
                await _send_with_markup(lambda mode: bot.send_photo(
                    chat_id=user_id, photo=FSInputFile(image_path),
                    caption=body, reply_markup=keyboard, parse_mode=mode,
                ))
                return
            # Подпись к фото ограничена 1024 символами, а текст рассылки — 4096.
            # Длинный текст отправляем отдельным сообщением, иначе Telegram откажет.
            await bot.send_photo(
                chat_id=user_id, photo=FSInputFile(image_path), parse_mode=None)
        await _send_with_markup(lambda mode: bot.send_message(
            chat_id=user_id, text=body, parse_mode=mode,
            reply_markup=keyboard, disable_web_page_preview=True,
        ))
    return send


def _make_chat_sender(bot: Bot):
    """Ответ из панели. Недоступность человека — это False, а не исключение."""
    async def send(user_id: int, text: str, image_path: str | None = None) -> bool:
        try:
            if image_path and Path(image_path).is_file():
                if len(text or "") <= TELEGRAM_CAPTION_LIMIT:
                    await _send_with_markup(lambda mode: bot.send_photo(
                        chat_id=user_id, photo=FSInputFile(image_path),
                        caption=text or None, parse_mode=mode))
                    return True
                # Длинный ответ не влезает в подпись: фото отдельно, текст отдельно.
                await bot.send_photo(
                    chat_id=user_id, photo=FSInputFile(image_path), parse_mode=None)
            await _send_with_markup(lambda mode: bot.send_message(
                chat_id=user_id, text=text, parse_mode=mode,
                disable_web_page_preview=True))
            return True
        except (TelegramForbiddenError, TelegramBadRequest):
            return False
    return send


def _make_owner_alert_sender(bot: Bot, owner_chat_id: int):
    """Короткое сообщение владельцу. Без подробностей и секретов."""
    async def send(text: str) -> None:
        await bot.send_message(
            chat_id=owner_chat_id, text=text, disable_web_page_preview=True)
    return send


async def _run_billing_reconcile(service, *extra_services, interval: float = 300.0) -> None:
    """Сверка оплат и закрытие просроченных счетов.

    Telegram мог не доставить сообщение об оплате, а внешняя касса — уведомление:
    тогда оплату восстанавливают по официальному статусу у провайдера. Заодно
    закрываются счета с истёкшим сроком: оплатить их уже нельзя, а незакрытый
    заказ блокировал бы человеку следующую покупку.
    """
    services = [item for item in (service, *extra_services) if item is not None]
    while True:
        for item in services:
            try:
                now = time.time()
                expired = await item.expire_pending(now=now)
                if expired:
                    logger.info("Закрыто просроченных счетов: %s", expired)
                applied = await item.apply_stars_transactions(now=now)
                if applied:
                    logger.info("Восстановлено оплат звёздами: %s", applied)
                # Пришедшее уведомление кассы само по себе не деньги: оплату
                # подтверждает канонический запрос статуса к провайдеру.
                provider = getattr(item, "_provider", None)
                if callable(getattr(provider, "get_payment_status", None)):
                    summary = await item.reconcile_due(now=time.time(), limit=10)
                    if summary.attempted:
                        logger.info(
                            "Сверка платежей: проверено %s, выдано %s, на проверке %s",
                            summary.attempted, summary.applied, summary.manual,
                        )
                # Оплата могла подтвердиться, а доступ не выдаться (например, разошлись
                # условия заказа): такие заказы повторяем, но не чаще, чем раз в две
                # итерации воркера, чтобы не заваливать журнал.
                recovered = await item.retry_pending_entitlements(
                    now=time.time(), quiet_seconds=interval * 2,
                )
                if recovered:
                    logger.info("Повторно выдано доступов: %s", recovered)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Сверка оплат не удалась")
        await asyncio.sleep(interval)


async def _probe_platega_credentials(provider) -> None:
    """Тихая проверка ключей кассы: балансы читаются, платежи не создаются."""
    try:
        accepted = await provider.probe_credentials()
    except asyncio.CancelledError:
        raise
    except Exception:
        accepted = False
    if accepted:
        logger.info("Platega: ключи приняты, баланс доступен")
    else:
        logger.warning("Platega: ключи не подтверждены проверкой баланса")


async def _run_transient_cleanup(db, *, interval: float = 86400.0) -> None:
    """Убирает служебный мусор: журнал апдейтов и брошенные неоплаченные заказы.

    Финансовые записи не трогаются: заказ с подтверждённым платежом или выданным
    доступом сохраняется независимо от возраста.
    """
    while True:
        try:
            removed = await db.clean_transient_data(now=time.time())
            if removed["updates"] or removed["orders"]:
                logger.info(
                    "Очистка служебных данных: апдейтов %s, заказов %s",
                    removed["updates"], removed["orders"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Очистка служебных данных не удалась")
        await asyncio.sleep(interval)


async def _run_owner_alerts(alerter: OwnerAlerter, collect, *, interval: float = 300.0) -> None:
    """Редкая проверка подсистем: оповещения не срочные, панель не должна спамить."""
    while True:
        try:
            snapshot = await collect()
        except Exception:
            logger.exception("Не удалось собрать снимок для оповещений владельцу")
        else:
            try:
                await alerter.inspect(snapshot)
                await alerter.repeat_if_still_bad(snapshot)
            except Exception:
                logger.exception("Сбой оповещений владельцу")
        await asyncio.sleep(interval)


def _make_notification_worker(
    config, db, poller: StreamPoller, send_budget: TelegramSendBudget | None = None,
) -> NotificationWorker | None:
    if (getattr(config, 'production_contract', None) is not None
            and not getattr(config, 'production_admitted', False)):
        raise ConfigError('Production queue требует успешный getMe admission')
    if not getattr(config, "notification_queue_enabled", False):
        return None
    return NotificationWorker(
        NotificationQueue(db), poller.send_queued_job,
        max_concurrency=4, per_chat_interval=1.0,
        send_budget=send_budget,
    )


async def main() -> None:
    config = load_config()
    if (
        is_railway_environment()
        and getattr(config, "growth_enabled", False)
        and not contour_bot_username_allowed(config.admin_telegram_bot_username)
    ):
        raise ConfigError("R8 staging требует тестового бота")

    contract = getattr(config, 'production_contract', None)
    if contract is not None:
        from bot.production_admission import validate_storage
        validate_storage(contract)
    db = None
    bot: Bot | None = None
    billing_service: BillingService | None = None
    bank_billing_service: BillingService | None = None
    platega_probe_task: asyncio.Task | None = None
    writer_lock = None
    try:
        if contract is not None:
            from bot.production_admission import verify_identity, WriterLock
            bot = Bot(token=config.telegram_bot_token,
                      default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            await _with_startup_retry(lambda: verify_identity(bot, contract), 'Проверка production bot identity')
            writer_lock = WriterLock(contract)
            config = replace(config, production_admitted=True)
        db = Database(config.db_path, token_encryption_key=config.token_encryption_key)
        await db.connect()
        # Между остановкой старого процесса и запуском нового EventSub не слушается:
        # текущий эфир уже нельзя считать полностью покрытым событиями.
        await db.invalidate_live_follow_counts_after_restart()
        # ChatListener держит активность, чатеров, топ и рейды в RAM. После restart
        # текущая logical session продолжается, но её chat stats уже неполны.
        await db.invalidate_live_chat_stats_after_restart()
        await _log_known_chats(db)
        await _apply_auto_track(db, config)

        if bot is None:
            bot = Bot(
                token=config.telegram_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        await _with_startup_retry(
            lambda: _verify_staging_bot_identity(bot, config),
            "Проверка личности тестового бота",
        )
        live_post_updater = LivePostUpdater(bot, db)
        telegram_send_budget = TelegramSendBudget()
        channel_username_cache = TelegramChannelUsernameCache()
        await _with_startup_retry(
            lambda: _reconcile_telegram_channels(bot, db, channel_username_cache),
            "Проверка Telegram-каналов",
        )
        dp = Dispatcher()
        payment_policy = first_release_payment_policy(
            contract_policy=getattr(contract, "payment_policy", None),
        )
        # Провайдер Stars подключается только при включённой денежной политике:
        # пока режим «выключено», покупка отвечает «недоступно» и заказ не создаётся.
        stars_provider = (
            TelegramStarsProvider(bot, payment_policy) if payment_policy.mode != "offline" else None
        )
        billing_service = BillingService(
            db, stars_provider, runtime_policy=payment_policy,
            access_policy=AccessPeriodPolicy(PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION),
            terms_version=PLUS_TERMS_VERSION,
            # Возврат инициирует только владелец: подтверждённый возврат снимает
            # доступ по этому заказу, независимые подписки не затрагиваются.
            merchant_actor_ids=(
                frozenset({config.owner_chat_id}) if config.owner_chat_id else frozenset()
            ),
        )
        # Банковский канал (СБП и карта) — отдельный провайдер и отдельный сервис:
        # ключи сами ничего не включают, нужен явный флаг владельца и объявленный
        # транспорт. До этого сервиса нет, и способ остаётся недоступным.
        from bot.platega_runtime import build_platega_provider
        platega_provider = build_platega_provider(config, payment_policy)
        bank_billing_service = (
            BillingService(
                db, platega_provider, runtime_policy=payment_policy,
                access_policy=AccessPeriodPolicy(PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION),
                terms_version=PLUS_TERMS_VERSION,
                merchant_actor_ids=(
                    frozenset({config.owner_chat_id}) if config.owner_chat_id else frozenset()
                ),
            )
            if platega_provider is not None else None
        )
        if platega_provider is not None:
            logger.info("Банковский канал: провайдер Platega подключён, ожидается подтверждение оплат")
            # Проверка ключей не создаёт платежей и не печатает секретов: владелец
            # видит в журнале, принял ли провайдер ключи из личного кабинета.
            platega_probe_task = asyncio.create_task(
                _probe_platega_credentials(platega_provider), name="platega-credentials",
            )
        dp["billing_service"] = billing_service
        dp["bank_billing_service"] = bank_billing_service
        dp["channel_username_cache"] = channel_username_cache
        setup_middlewares(
            dp, db=db, media_dir=str(Path(config.db_path).parent / "media"))
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

        private_commands = _private_bot_commands(
            tracking_commands, growth_enabled=getattr(config, "growth_enabled", False),
        )
        await _with_startup_retry(
            lambda: bot.set_my_commands(
                private_commands,
                scope=BotCommandScopeAllPrivateChats(),
            ),
            "Регистрация команд (личка)",
        )
        await _with_startup_retry(
            lambda: bot.set_my_commands(tracking_commands, scope=BotCommandScopeAllGroupChats()),
            "Регистрация команд (группы)",
        )
        if config.owner_chat_id is not None and config.admin_panel_access_key:
            await _with_startup_retry(
                lambda: bot.set_my_commands(
                    _private_bot_commands(
                        tracking_commands, owner=True,
                        growth_enabled=getattr(config, "growth_enabled", False),
                    ),
                    scope=BotCommandScopeChat(chat_id=config.owner_chat_id),
                ),
                "Регистрация команд (владелец)",
            )
        await _with_startup_retry(
            lambda: bot.set_chat_menu_button(menu_button=_menu_button_for_config(config)),
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
            billing_test_enabled = (
                getattr(config, "pinned_staging", False)
                and getattr(config, "mini_app_enabled", False)
                and config.admin_telegram_bot_username.casefold() == "signalstreamsbot"
                and config.owner_chat_id is not None
            )
            oauth_server = OAuthCallbackServer(
                redirect_uri=f"{config.oauth_public_base_url}{REDIRECT_PATH}",
                host=config.oauth_host,
                port=config.oauth_port,
                admin_access=(
                    AdminAccess(
                        config.admin_panel_access_key,
                        enabled=True,
                        secure_cookie=config.oauth_public_base_url.startswith("https://"),
                        owner_id=config.owner_chat_id,
                        bot_token=config.telegram_bot_token,
                        bot_username=config.admin_telegram_bot_username,
                        public_base_url=config.oauth_public_base_url,
                    )
                    if getattr(config, "admin_panel_access_key", None) and config.owner_chat_id is not None
                    else None
                ),
                streamer_access=(
                    StreamerAccess(
                        config.telegram_bot_token,
                        bot_username=config.admin_telegram_bot_username,
                        public_base_url=config.oauth_public_base_url,
                        secure_cookie=config.oauth_public_base_url.startswith("https://"),
                    )
                    if getattr(config, "streamer_plus_enabled", False) else None
                ),
                streamer_db=db if getattr(config, "streamer_plus_enabled", False) else None,
                streamer_bot=bot if getattr(config, "streamer_plus_enabled", False) else None,
                viewer_db=db if getattr(config, "viewer_plus_enabled", False) else None,
                viewer_bot_token=(
                    config.telegram_bot_token if getattr(config, "viewer_plus_enabled", False) else None
                ),
                mini_app_db=db if getattr(config, "mini_app_enabled", False) else None,
                mini_app_bot_token=(
                    config.telegram_bot_token if getattr(config, "mini_app_enabled", False) else None
                ),
                mini_app_twitch=twitch if getattr(config, "mini_app_enabled", False) else None,
                mini_app_bot=bot if getattr(config, "mini_app_enabled", False) else None,
                mini_app_bot_username=config.admin_telegram_bot_username or "",
                mini_app_owner_config=config,
                mini_app_oauth_client_id=(config.twitch_client_id if getattr(config, "mini_app_enabled", False) else ""),
                mini_app_oauth_client_secret=(config.twitch_client_secret if getattr(config, "mini_app_enabled", False) else ""),
                mini_app_billing_test_enabled=billing_test_enabled,
                mini_app_billing_test_user_ids=(
                    frozenset({config.owner_chat_id}) if billing_test_enabled else frozenset()
                ),
                mini_app_billing_service=(
                    billing_service if getattr(config, "mini_app_enabled", False) else None
                ),
                # Реальная касса: этот сервис принимает callback и продаёт СБП/карту.
                payment_service=bank_billing_service,
                streamer_environment=environment_label(config.oauth_public_base_url),
            )
            follow_listener_task: asyncio.Task | None = None
            poller: StreamPoller | None = None
            preview_manager = None
            preview_capture_service = None
            poller_task: asyncio.Task | None = None
            polling_task: asyncio.Task | None = None
            backup_task: asyncio.Task | None = None
            owner_alert_task: asyncio.Task | None = None
            retention_task: asyncio.Task | None = None
            transient_task: asyncio.Task | None = None
            notification_worker: NotificationWorker | None = None
            broadcast_worker: BroadcastWorker | None = None
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
                        send_budget=telegram_send_budget,
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
                    telegram_channel_username_cache=channel_username_cache,
                    notification_queue_enabled=getattr(config, "notification_queue_enabled", False),
                    notification_digest_enabled=getattr(config, "notification_digest_enabled", False),
                    notification_digest_window_seconds=getattr(
                        config, "notification_digest_window_seconds", 300),
                    notification_digest_max_lines=getattr(
                        config, "notification_digest_max_lines", 5),
                    telegram_send_budget=telegram_send_budget,
                    viewer_filters_enabled=getattr(config, "viewer_plus_enabled", False),
                    bot_username=(
                        config.admin_telegram_bot_username
                        if getattr(config, "growth_enabled", False)
                        and config.admin_telegram_bot_username
                        else TELEGRAM_BOT_USERNAME
                    ),
                )
                notification_worker = _make_notification_worker(
                    config, db, poller, telegram_send_budget,
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
                oauth_server.set_preview_observer(preview_manager)
                if notification_worker is not None:
                    notification_worker.start()
                # Рассылки владельца идут своим отправителем поверх того же бюджета:
                # очередь уведомлений о стримах от них не зависит.
                broadcast_worker = BroadcastWorker(
                    db, _make_broadcast_sender(bot), send_budget=telegram_send_budget,
                )
                broadcast_worker.start()
                poller_task = asyncio.create_task(poller.run())

                # Свежая копия базы раз в сутки. Без неё откат означал бы потерю
                # всех данных с момента последней ручной копии. Копия делается
                # средствами SQLite и не отправляет ничего пользователям.
                backup_dir = Path(config.db_path).parent / "backups"
                backup_task = asyncio.create_task(
                    run_backup_loop(
                        config.db_path,
                        backup_dir,
                        interval_seconds=getattr(
                            config, "backup_interval_seconds", 24 * 60 * 60
                        ),
                        retention=getattr(config, "backup_retention", 5),
                    )
                )

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

                if getattr(config, "admin_panel_access_key", None):
                    admin_directory = AdminDirectory(
                        db,
                        backup_dir=backup_dir,
                        retention=getattr(config, "backup_retention", 5),
                    )
                    admin_snapshot = AdminSnapshot(
                        db, poller, follow_listener, token_store, preview_manager,
                        db_path=config.db_path,
                        telegram_polling_provider=lambda: (
                            None if polling_task is None else not polling_task.done()
                        ),
                        environment=environment_label(config.oauth_public_base_url),
                        directory=admin_directory,
                    )
                    oauth_server.set_admin_snapshot_provider(admin_snapshot.collect)
                    oauth_server.set_admin_services(
                        people=db, directory=admin_directory, database=db,
                        chat_sender=_make_chat_sender(bot),
                        media_dir=str(Path(config.db_path).parent / "media"),
                        # Возврат оплаты из панели идёт в тот сервис, который принял
                        # платёж: у звёзд и банковского канала свои провайдеры.
                        billing={
                            key: service for key, service in (
                                ("telegram_stars", billing_service),
                                ("platega", bank_billing_service),
                            ) if service is not None
                        },
                    )
                    # Сверка оплат: сообщение об успешной оплате приходит один раз,
                    # и потерянное сообщение нельзя оставлять без последствий.
                    billing_task = asyncio.create_task(
                        _run_billing_reconcile(billing_service, bank_billing_service),
                        name="billing-reconcile",
                    )
                    # Оповещения владельцу: только когда подсистема меняет состояние.
                    owner_chat_id = getattr(config, "owner_chat_id", None)
                    owner_alerter = OwnerAlerter(
                        _make_owner_alert_sender(bot, owner_chat_id) if owner_chat_id else None,
                    )

                    async def _owner_snapshot_collect():
                        """Снимок панели плюс то, что видит только владелец.

                        Оплата без доступа — тихая авария: деньги списаны, человек
                        без подписки. Она обязана попадать в оповещения.
                        """
                        snapshot = await admin_snapshot.collect()
                        try:
                            snapshot["billing"] = {
                                "awaiting_access": await db.count_orders_needing_entitlement_review(),
                            }
                        except Exception:
                            logger.exception("Не удалось посчитать заказы без доступа")
                        return snapshot

                    owner_alert_task = asyncio.create_task(
                        _run_owner_alerts(owner_alerter, _owner_snapshot_collect),
                        name="owner-alerts",
                    )
                    # Срок хранения переписки: старое удаляется вместе с файлами.
                    retention_task = asyncio.create_task(
                        run_retention_loop(
                            db, Path(config.db_path).parent / "media",
                            days=getattr(config, "dialogue_retention_days", None)
                            or DEFAULT_RETENTION_DAYS,
                        ),
                        name="dialogue-retention",
                    )
                    # Служебный мусор: журнал апдейтов и брошенные счета.
                    transient_task = asyncio.create_task(
                        _run_transient_cleanup(db),
                        name="transient-cleanup",
                    )

                await _with_startup_retry(
                    lambda: bot.delete_webhook(drop_pending_updates=False), "Удаление webhook"
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
                if getattr(config, "admin_panel_access_key", None):
                    oauth_server.set_admin_snapshot_provider(None)
                if poller is not None:
                    poller.stop()
                await _cancel_task(polling_task, "Telegram polling")
                await _cancel_task(poller_task, "StreamPoller")
                await _cancel_task(backup_task, "DB backup")
                if notification_worker is not None:
                    await _safe_cleanup("Notification worker", notification_worker.stop())
                if broadcast_worker is not None:
                    await _safe_cleanup("Broadcast worker", broadcast_worker.stop())
                if owner_alert_task is not None:
                    await _cancel_task(owner_alert_task, "Owner alerts")
                await _cancel_task(retention_task, "Dialogue retention")
                await _cancel_task(transient_task, "Transient cleanup")
                await _shutdown_preview_runtime(
                    preview_manager, preview_capture_service
                )
                if poller is not None:
                    await _safe_cleanup("StreamPoller", poller.shutdown())
                await _safe_cleanup("FollowEventListener", follow_listener.stop())
                await _cancel_task(follow_listener_task, "FollowEventListener")
                await _safe_cleanup("OAuth callback server", oauth_server.stop())
    finally:
        if billing_service is not None:
            await _safe_cleanup("Billing worker", billing_service.close())
        if bank_billing_service is not None:
            await _safe_cleanup("Bank billing worker", bank_billing_service.close())
        if platega_probe_task is not None:
            await _cancel_task(platega_probe_task, "Platega credential probe")
        if bot is not None:
            await _safe_cleanup("Telegram session", bot.session.close())
        if db is not None:
            await _safe_cleanup("SQLite", db.close())
        if writer_lock is not None:
            writer_lock.close()


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
