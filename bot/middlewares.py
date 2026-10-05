from __future__ import annotations

import logging
import io
import pathlib
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InaccessibleMessage, Message, TelegramObject

from .media_store import MAX_IMAGE_BYTES, MediaError, save_image

logger = logging.getLogger(__name__)

# минимальный интервал между любыми двумя действиями одного пользователя
DEFAULT_INTERVAL_SECONDS = 0.5

# отдельные лимиты для операций, которые стоят дорого: перебор чатов через
# Telegram API, сборка HTML-отчёта, полная пагинация подписок на Twitch.
# Без них один человек, удерживающий кнопку, выбирает общий лимит Telegram
# (30 запросов в секунду на бота) и подвешивает бота для всех остальных
HEAVY_ACTION_INTERVAL_SECONDS = 8.0
HEAVY_ACTIONS = (
    "menu:manage_group",
    "menu:import_follows",
    "importfollows:add",
    "menu:report",
    "report:",
    "menu:live",
)
HEAVY_COMMANDS = ("/report", "/import_follows", "/live", "/auth_twitch")

# как часто выбрасывать записи о давно неактивных пользователях, чтобы словарь
# не рос бесконечно на большом числе пользователей
_CLEANUP_EVERY_SECONDS = 300
_ENTRY_TTL_SECONDS = 600


class ThrottleMiddleware(BaseMiddleware):
    """Ограничивает частоту действий на пользователя. Лишние апдейты отбрасываются,
    а не ставятся в очередь — иначе накопленный хвост всё равно упрётся в лимиты
    Telegram, просто с задержкой."""

    def __init__(self) -> None:
        self._last_action: dict[int, float] = {}
        self._last_heavy: dict[int, float] = {}
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float) -> None:
        if now - self._last_cleanup < _CLEANUP_EVERY_SECONDS:
            return
        self._last_cleanup = now
        cutoff = now - _ENTRY_TTL_SECONDS
        for storage in (self._last_action, self._last_heavy):
            for user_id in [uid for uid, ts in storage.items() if ts < cutoff]:
                del storage[user_id]

    @staticmethod
    def _is_heavy(event: TelegramObject) -> bool:
        if isinstance(event, CallbackQuery):
            data = event.data or ""
            return any(data.startswith(prefix) for prefix in HEAVY_ACTIONS)
        text = getattr(event, "text", None) or ""
        return any(text.startswith(cmd) for cmd in HEAVY_COMMANDS)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event,Message) and event.text == 'Меню':
            # Returning home must cancel a draft even immediately after a tap.
            return await handler(event,data)
        if isinstance(event, Message) and (event.successful_payment is not None or event.refunded_payment is not None):
            # Financial updates must reach the durable, idempotent ledger.
            return await handler(event, data)
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup(now)

        heavy = self._is_heavy(event)
        interval = HEAVY_ACTION_INTERVAL_SECONDS if heavy else DEFAULT_INTERVAL_SECONDS
        storage = self._last_heavy if heavy else self._last_action

        last = storage.get(user.id)
        if last is not None and now - last < interval:
            if isinstance(event, CallbackQuery):
                # без ответа на callback у пользователя вечно крутится «часики»
                try:
                    await event.answer("Слишком часто. Подождите пару секунд.")
                except Exception:
                    pass
            logger.debug("Действие пользователя отброшено троттлингом (heavy=%s)", heavy)
            return None

        storage[user.id] = now
        return await handler(event, data)


class CallbackGuardMiddleware(BaseMiddleware):
    """Отсекает callback-запросы, у которых недоступно сообщение с кнопкой.

    Telegram не отдаёт тело сообщений старше 48 часов: в aiogram это приходит как
    InaccessibleMessage (или None). Обработчики читают из него chat_id и редактируют
    текст, поэтому без такой проверки старая кнопка роняла бы их с AttributeError.

    Исключение — отказ от рассылки: ему тело сообщения не нужно, а человек должен
    мочь отписаться в любой момент, даже через неделю после рассылки."""

    # Значения совпадают с bot/handlers/broadcasts.py: этим колбэкам сообщение не нужно.
    MESSAGE_FREE_CALLBACKS = ("broadcast_",)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            callback_data = event.data or ""
            if callback_data.startswith(self.MESSAGE_FREE_CALLBACKS):
                return await handler(event, data)
            message = event.message
            if message is None or isinstance(message, InaccessibleMessage):
                try:
                    await event.answer(
                        "Сообщение недоступно. Нажмите «Меню» в личном чате с ботом.",
                        show_alert=True,
                    )
                except Exception:
                    pass
                return None
        return await handler(event, data)


class ErrorGuardMiddleware(BaseMiddleware):
    """Последний рубеж: не даёт исключению из обработчика всплыть в поллинг-цикл.

    aiogram и сам не роняет процесс на ошибке обработчика, но пользователь при этом
    остаётся с зависшим интерфейсом и без единого намёка на то, что пошло не так."""

    async def _recover(self,event,data, *, wait=True):
        message=event.message if isinstance(event,CallbackQuery) else event
        actor=event.from_user.id if getattr(event,'from_user',None) else None
        # Отказ от рассылки не нуждается в клавиатуре «Меню»: лишнее сообщение
        # поверх подтверждения отписки только путает человека.
        if isinstance(event, CallbackQuery) and (event.data or "").startswith(
                CallbackGuardMiddleware.MESSAGE_FREE_CALLBACKS):
            return
        from .telegram_ui import own_private
        if isinstance(message,(Message,InaccessibleMessage)) and own_private(message,actor):
            try:
                from .telegram_home import recover_menu_keyboard
                await recover_menu_keyboard(message,data.get('state'),wait=wait)
            except Exception as error:
                logger.warning('Menu keyboard recovery unavailable (%s)',type(error).__name__)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # A cold OAuth/import handler can wait minutes. Install the return path
        # before it waits, then repair replacements/unknown sends on completion.
        await self._recover(event,data,wait=False)
        try:
            return await handler(event, data)
        except Exception:
            outcome = data.get('ingress_outcome')
            if outcome is not None:
                outcome['failed'] = True
            if isinstance(event, Message) and (event.successful_payment or event.refunded_payment):
                raise
            logger.exception("Необработанная ошибка в обработчике %s", type(event).__name__)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
                except Exception:
                    pass
            return None
        finally:
            await self._recover(event,data)


class ProfileMiddleware(BaseMiddleware):
    """Запоминает профиль человека и последнюю активность для панели владельца.

    Запись троттлится (профиль реже, активность чаще), чтобы горячий путь не
    упирался в базу на каждом апдейте. Сбой базы логируется и не мешает
    обработчику: панель не должна ломать работу бота."""

    def __init__(self, db, *, activity_seconds: float = 300.0,
                 profile_seconds: float = 86400.0, retry_seconds: float = 60.0) -> None:
        self._db = db
        self._activity_seconds = activity_seconds
        self._profile_seconds = profile_seconds
        self._retry_seconds = retry_seconds
        self._written: dict[int, tuple[float, float]] = {}
        self._retry_after: dict[int, float] = {}

    def _forget_stale(self, now: float) -> None:
        if len(self._written) <= 4096:
            return
        cutoff = now - max(self._profile_seconds, self._activity_seconds, 3600.0)
        for user_id in [key for key, marks in self._written.items() if max(marks) < cutoff]:
            del self._written[user_id]
        # Если активных записей всё ещё слишком много, вытесняем самые старые:
        # словарь не должен расти бесконечно на большом числе людей.
        while len(self._written) > 8192:
            oldest = min(self._written, key=lambda key: max(self._written[key]))
            del self._written[oldest]

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user") if isinstance(data, dict) else None
        if user is None:
            user = getattr(event, "from_user", None)
        user_id = getattr(user, "id", None) if user is not None else None
        if isinstance(user_id, int) and user_id > 0:
            await self._remember(user, user_id)
        return await handler(event, data)

    async def _remember(self, user, user_id: int) -> None:
        now = time.monotonic()
        if now < self._retry_after.get(user_id, 0.0):
            return
        # -inf, а не 0: при малом времени работы процесса monotonic() может быть
        # меньше суток, и запись профиля иначе откладывалась бы на сутки.
        profile_at, activity_at = self._written.get(user_id, (float("-inf"), float("-inf")))
        write_profile = now - profile_at >= self._profile_seconds
        write_activity = now - activity_at >= self._activity_seconds
        if not (write_profile or write_activity):
            return
        self._forget_stale(now)

        first = getattr(user, "first_name", None)
        last = getattr(user, "last_name", None)
        display = " ".join(
            part.strip() for part in (first, last)
            if isinstance(part, str) and part.strip()
        ) or None
        timestamp = time.time()
        failed = False
        if write_profile:
            try:
                await self._db.remember_profile(
                    user_id,
                    username=getattr(user, "username", None),
                    display_name=display,
                    language_code=getattr(user, "language_code", None),
                    now=timestamp,
                )
            except Exception:
                logger.exception("Не удалось сохранить профиль пользователя")
                failed = True
        if write_activity:
            try:
                await self._db.touch_activity(user_id, now=timestamp)
            except Exception:
                logger.exception("Не удалось отметить активность пользователя")
                failed = True
        if failed:
            # Окно считается пройденным только после успешной записи, иначе один
            # сбой откладывал бы профиль на сутки. Короткая пауза защищает базу.
            self._retry_after[user_id] = now + self._retry_seconds
            return
        self._written[user_id] = (
            now if write_profile else profile_at,
            now if write_activity else activity_at,
        )
        self._retry_after.pop(user_id, None)


class DialogueMiddleware(BaseMiddleware):
    """Личные сообщения людей становятся перепиской в панели владельца.

    Пишем только личные чаты и только не-команды: журнал команд — это не чат,
    а группы и каналы в переписку вообще не попадают.
    """

    def __init__(self, db, *, media_dir=None, max_length: int = 4096) -> None:
        self._db = db
        self._media_dir = media_dir
        self._max_length = max_length

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            await self._remember(event, data)
        except Exception:
            # Переписка не должна мешать ответу человеку: сбой записи не блокирует бота.
            logger.exception("Не удалось записать сообщение в переписку")
        return await handler(event, data)

    async def _download_photo(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        """Скачивает фото человека, чтобы владелец видел его в панели.

        Размер проверяется до скачивания: чужой файл не должен забивать диск.
        """
        if not self._media_dir:
            return None
        photos = getattr(event, "photo", None)
        if not photos:
            return None
        bot = data.get("bot") if isinstance(data, dict) else None
        if bot is None:
            return None
        largest = photos[-1]
        size = getattr(largest, "file_size", None)
        if isinstance(size, int) and size > MAX_IMAGE_BYTES:
            logger.warning("Фото в переписке больше лимита, оставляю без файла")
            return None
        buffer = io.BytesIO()
        await bot.download(largest, destination=buffer)
        payload = buffer.getvalue()
        try:
            return save_image(pathlib.Path(self._media_dir), payload, prefix="chat")
        except MediaError:
            logger.warning("Фото в переписке не принято: неподдерживаемый формат или размер")
            return None

    # Виды вложений, которые человек может прислать в личку боту.
    ATTACHMENTS = (
        "photo", "sticker", "animation", "video", "video_note", "voice",
        "audio", "document", "contact", "location", "venue", "poll", "dice",
    )

    def _attachment_of(self, event: TelegramObject) -> str | None:
        for kind in self.ATTACHMENTS:
            if getattr(event, kind, None):
                return kind
        return None

    async def _remember(self, event: TelegramObject, data: dict[str, Any]) -> None:
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if user is None or getattr(chat, "type", None) != "private":
            # Видно, какие обновления не попали в переписку и почему.
            logger.info(
                "Переписка: пропуск %s (чат %s, автор %s)",
                type(event).__name__, getattr(chat, "type", None),
                "есть" if user is not None else "нет",
            )
            return
        text = ((getattr(event, "text", None) or getattr(event, "caption", None)) or "").strip()
        attachment = self._attachment_of(event)
        if not text and not attachment:
            logger.info(
                "Переписка: в %s нет ни текста, ни вложения",
                type(event).__name__,
            )
            return
        # Команда без вложения — это не переписка, а нажатие в меню.
        if text.startswith("/") and not attachment:
            return
        image_path = await self._download_photo(event, data) if attachment == "photo" else None
        await self._db.record_dialogue_message(
            int(user.id), "in", text[: self._max_length] or None,
            now=time.time(), telegram_message_id=getattr(event, "message_id", None),
            image_path=image_path, attachment=attachment,
        )


def setup_middlewares(dp, db=None, media_dir=None) -> None:
    """Порядок важен: сначала отбраковка мусорных апдейтов, затем троттлинг,
    и только потом — перехват ошибок вокруг самого обработчика."""
    # Переписка ставится самым первым слоем: она обязана записать сообщение
    # даже если внутренние слои отбросят апдейт (троттлинг, защита от мусора).
    if db is not None:
        # Telegram шлёт сообщения разными обновлениями (обычные, бизнес- и
        # гостевые): без этого часть людей выглядела молчащей.
        # Регистрируется именно внешним слоем: внутренние библиотека вызывает
        # только когда сообщение подошло под обработчик, а обычный текст ни под
        # один не подходит — и переписка молчала.
        for observer_name in ("message", "business_message", "guest_message"):
            observer = getattr(dp, observer_name, None)
            if observer is not None:
                observer.outer_middleware(
                    DialogueMiddleware(db, media_dir=media_dir))

    for observer in (dp.message, dp.callback_query):
        observer.middleware(ErrorGuardMiddleware())

    dp.callback_query.middleware(CallbackGuardMiddleware())

    throttle = ThrottleMiddleware()
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)

    if db is not None:
        # Регистрируется после троттлинга: профиль и активность пишутся только
        # по апдейтам, которые дошли до обработчика.
        profile = ProfileMiddleware(db)
        dp.message.middleware(profile)
        dp.callback_query.middleware(profile)
