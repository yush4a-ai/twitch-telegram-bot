from __future__ import annotations

import json
import re
import time

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..database import Database
from ..report import build_report_html, format_duration_seconds
from ..twitch import TwitchClient

# сколько времени после завершения стрима ещё доступен полный отчёт через /report
REPORT_RETENTION_SECONDS = 24 * 60 * 60

router = Router(name="streams")

LOGIN_RE = re.compile(r"^[a-zA-Z0-9_]{4,25}$")

# защита от злоупотребления: сколько каналов может отслеживать один чат
MAX_CHANNELS_PER_CHAT = 50


class AddChannel(StatesGroup):
    waiting_for_login = State()


class QuietHoursSetup(StatesGroup):
    waiting_for_offset = State()
    waiting_for_custom_time = State()


def _extract_login_text(text: str) -> str | None:
    login = text.strip().lower()
    login = login.removeprefix("https://twitch.tv/").removeprefix("twitch.tv/").strip("/ ")
    if not LOGIN_RE.match(login):
        return None
    return login


def _extract_login(command: CommandObject) -> str | None:
    if not command.args:
        return None
    return _extract_login_text(command.args)


def _main_menu_keyboard(chat_type: str) -> InlineKeyboardMarkup:
    # сгруппировано по смыслу: каналы -> отчёты -> настройки чата -> справка,
    # вместо плоского списка из разнородных пунктов
    rows = [
        [
            InlineKeyboardButton(text="📡 Мои каналы", callback_data="menu:list"),
            InlineKeyboardButton(text="➕ Добавить", callback_data="menu:add"),
        ],
        [InlineKeyboardButton(text="📊 Отчёт по стриму", callback_data="menu:report")],
    ]
    if chat_type != ChatType.PRIVATE:
        # в личке привязывать некуда — привязка личного чата имеет смысл только для групп/каналов
        rows.append(
            [InlineKeyboardButton(text="🔗 Привязать отчёты к личке", callback_data="menu:link_stats")]
        )
    else:
        # а в личке, наоборот, можно управлять настройками своих групп удалённо
        rows.append(
            [InlineKeyboardButton(text="💬 Мои сообщества", callback_data="menu:manage_group")]
        )
        rows.append(
            [InlineKeyboardButton(text="🌙 Тихие часы", callback_data="menu:quiet_hours")]
        )
    rows.append([InlineKeyboardButton(text="ℹ️ Что умею", callback_data="menu:about")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channels_keyboard(
    target_chat_id: int,
    channels: list[tuple[str, bool, int | None, str, bool, bool]],
    chat_default_recipient: int | None,
    *,
    back_callback: str = "menu:home",
    allow_add: bool = True,
    show_recipient_toggle: bool = True,
    is_telegram_channel: bool = False,
) -> InlineKeyboardMarkup:
    """Компакт-список: одна строка на канал (имя + иконки-статусы), тап открывает
    карточку канала с полной настройкой — вместо частокола из 5+ кнопок на канал.
    is_telegram_channel=True показывает только 🔔/🔕 — остальные иконки относятся
    к итоговому отчёту, которого в Telegram-канале не бывает."""
    rows = []
    for login, notify_enabled, post_recipient, report_format, raid_detection_enabled, _quiet_hours_exempt in channels:
        bell = "🔔" if notify_enabled else "🔕"
        status_icons = bell
        if not is_telegram_channel:
            if show_recipient_toggle:
                effective = (
                    post_recipient if post_recipient is not None else (chat_default_recipient or target_chat_id)
                )
                status_icons += " 👥" if effective == target_chat_id else " 📩"
            status_icons += " 📑" if report_format != "brief" else " 📄"
            if raid_detection_enabled:
                status_icons += " ⚡"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{login}  {status_icons}",
                    callback_data=f"channelcard:{target_chat_id}:{login}",
                )
            ]
        )
    if allow_add:
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить канал", callback_data=f"menu:add:{target_chat_id}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channel_card_keyboard(
    target_chat_id: int,
    login: str,
    notify_enabled: bool,
    post_recipient: int | None,
    chat_default_recipient: int | None,
    report_format: str,
    raid_detection_enabled: bool,
    quiet_hours_exempt: bool,
    *,
    back_callback: str,
    show_recipient_toggle: bool,
    is_telegram_channel: bool = False,
    show_quiet_hours_toggle: bool = False,
) -> InlineKeyboardMarkup:
    """Полная настройка одного канала — каждый переключатель на своей строке
    с явной подписью, вместо мелких кнопок вперемешку в общем списке.
    is_telegram_channel=True скрывает настройки итогового отчёта — в Telegram-канале
    он не отправляется (нет получателя и чат-статистики), только живой пост."""
    rows = [
        [
            InlineKeyboardButton(
                text=f"Уведомление о начале: {'🔔 вкл' if notify_enabled else '🔕 выкл'}",
                callback_data=f"togglenotify:{target_chat_id}:{login}",
            )
        ]
    ]
    if is_telegram_channel:
        rows.append(
            [InlineKeyboardButton(text="❌ Удалить канал", callback_data=f"untrack:{target_chat_id}:{login}")]
        )
        rows.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_callback)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if show_recipient_toggle:
        effective = (
            post_recipient if post_recipient is not None else (chat_default_recipient or target_chat_id)
        )
        recipient_label = "👥 Группа" if effective == target_chat_id else "📩 Личка"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Итоговый отчёт → {recipient_label}",
                    callback_data=f"togglerecipient:{target_chat_id}:{login}",
                )
            ]
        )
    format_label = "📄 Кратко" if report_format == "brief" else "📑 Развёрнуто"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Формат отчёта: {format_label}",
                callback_data=f"toggleformat:{target_chat_id}:{login}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Детектор рейдов: {'⚡ вкл' if raid_detection_enabled else 'выкл'}",
                callback_data=f"toggleraid:{target_chat_id}:{login}",
            )
        ]
    )
    if show_quiet_hours_toggle:
        exempt_label = "🌙 Тихие часы: не действуют" if quiet_hours_exempt else "🌙 Тихие часы: действуют"
        rows.append(
            [
                InlineKeyboardButton(
                    text=exempt_label,
                    callback_data=f"togglequiethoursexempt:{target_chat_id}:{login}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="❌ Удалить канал", callback_data=f"untrack:{target_chat_id}:{login}")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")]]
    )


def _report_channels_keyboard(logins: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📊 {login}", callback_data=f"report:{login}")]
        for login in logins
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _start_link_keyboard(bot: Bot, chat_id: int) -> InlineKeyboardMarkup:
    bot_user = await bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=link_{chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✉️ Получать посты в личку", url=link)]]
    )


async def _added_channel_summary(message: Message, db: Database, login: str, is_first_channel: bool) -> str:
    """Явно проговаривает, что произойдёт с только что добавленным каналом при
    настройках по умолчанию — чтобы не приходилось гадать, придёт ли вообще что-то.
    При самом первом канале в чате также объясняет разницу между живым постом
    (всегда в этот чат) и итоговым отчётом (можно перенаправить в личку)."""
    if is_first_channel:
        return (
            f"Готово, слежу за каналом «{login}».\n\n"
            "🔴 Как только стрим начнётся — здесь появится живой пост со счётчиком "
            "зрителей. Он всегда остаётся в этом чате.\n"
            "📊 После окончания стрима сюда же придёт развёрнутый итоговый отчёт "
            "(текст + HTML с графиком). Получателя отчёта и его формат можно "
            "поменять в настройках канала."
        )
    return (
        f"Готово, слежу за каналом «{login}».\n\n"
        "🔴 Живой пост — в этот чат. 📊 Итоговый отчёт — тоже сюда, развёрнутый "
        "(текст + HTML). Поменять можно в настройках канала."
    )


async def _ensure_stats_recipient(message: Message, db: Database) -> str | None:
    """Пытается по умолчанию привязать финальный отчёт о завершённом стриме
    (не живой пост о старте — он всегда идёт в этот чат) к личке того, кто добавил
    канал (только для групп/каналов — в личке чат и так совпадает с получателем).
    Не перебивает уже существующую привязку. Возвращает предупреждение, если личка
    автора недоступна (человек ни разу не писал боту) — тогда его нужно показать
    вместе с кнопкой Start."""
    if message.chat.type == ChatType.PRIVATE or message.from_user is None:
        return None

    user_id = message.from_user.id
    if await db.is_known_private_user(user_id):
        await db.set_default_stats_recipient(message.chat.id, user_id)
        return None

    if await db.get_stats_recipient(message.chat.id) is not None:
        return None  # получатель уже настроен явно — не докучаем

    return (
        "Чтобы получать итоговые отчёты о завершённых стримах себе в личку "
        "(живые посты о начале стрима всегда остаются в этом чате), нажми кнопку ниже."
    )


MENU_TEXT = (
    "Привет! Я слежу за стримами на Twitch: оповещаю о начале и присылаю подробный "
    "отчёт по завершении. Подробнее — кнопка «ℹ️ Что умею».\n\n"
    "Выбери действие:"
)

ABOUT_TEXT = (
    "ℹ️ <b>Что я умею</b>\n\n"
    "🔴 <b>Живой пост</b> — публикую сообщение в чат, как только канал выходит в эфир, "
    "и обновляю счётчик зрителей, пока идёт стрим.\n\n"
    "📊 <b>Итоговый отчёт</b> — после конца стрима присылаю сводку: длительность, "
    "пик и среднее число зрителей, новых фолловеров, сравнение с прошлыми стримами.\n"
    "Отчёт можно сделать кратким (только текст) или развёрнутым — тогда вдобавок придёт "
    "HTML-файл с графиком зрителей и чата, лучшими клипами, списком уникальных чатеров "
    "и ссылкой на запись (VOD) с таймкодами ключевых моментов.\n\n"
    "💬 <b>Топ чатеров</b> — самые активные зрители чата за стрим.\n\n"
    "⚡ <b>Детектор рейдов</b> — как только канал начинают рейдить, сразу присылаю "
    "уведомление с именем рейдера и числом приведённых зрителей (используется "
    "официальное подтверждение от Twitch, не только догадка по всплеску зрителей в чате).\n\n"
    "🤝 <b>Тег коллаба</b> — если в названии стрима упоминается другой отслеживаемый "
    "канал, помечаю итоговый отчёт как совместный стрим.\n\n"
    "🚨 <b>Алерты</b> — сообщаю, если отслеживаемый канал забанен/удалён на Twitch "
    "или сменил отображаемое имя.\n\n"
    "📍 <b>Гибкая доставка</b> — итоговый отчёт можно получать в группу или себе в личку "
    "(живой пост всегда остаётся в группе, где отслеживается канал).\n\n"
    "🌙 <b>Тихие часы</b> — задай период (например, 23:00–08:00 в своём часовом поясе), "
    "и итоговые отчёты в личку в это время не будут приходить сразу — они соберутся "
    "в одну сводку и придут, как только период закончится. Для отдельных каналов можно "
    "сделать исключение в настройках канала — их отчёт всегда будет приходить сразу.\n\n"
    "⚙️ <b>Удалённое управление</b> — если ты админ группы, можно менять её настройки "
    "прямо из личного чата с ботом, без переключения между чатами.\n\n"
    "🔐 <b>Подключение Twitch-аккаунта</b> — команда /auth_twitch даёт боту доступ "
    "к числу фолловеров канала (для итогового отчёта). Без подключения этот пункт "
    "просто не показывается в отчёте, а карточка канала явно пометит, что фолловеры "
    "недоступны."
)


@router.message(CommandStart(deep_link=True))
async def cmd_start_link(message: Message, command: CommandObject, state: FSMContext, db: Database) -> None:
    await db.mark_known_private_user(message.chat.id)

    payload = command.args or ""
    if payload.startswith("link_") and message.chat.type == ChatType.PRIVATE:
        try:
            source_chat_id = int(payload.removeprefix("link_"))
        except ValueError:
            await cmd_start(message, state, db)
            return
        await db.set_stats_recipient(source_chat_id, message.chat.id)
        await message.answer(
            "Готово! Теперь итоговые отчёты о завершённых стримах для этого чата "
            "будут приходить сюда, в личку (живые посты о начале стрима остаются в чате)."
        )
        return
    await cmd_start(message, state, db)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    if message.chat.type == ChatType.PRIVATE:
        await db.mark_known_private_user(message.chat.id)
    await message.answer(MENU_TEXT, reply_markup=_main_menu_keyboard(message.chat.type))


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"chat_id этого чата: {message.chat.id}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(ABOUT_TEXT)


@router.my_chat_member()
async def on_bot_membership_changed(event: ChatMemberUpdated, db: Database) -> None:
    """В Telegram-канале нет способа узнать о боте иначе: читатели канала не могут
    писать боту сообщения, поэтому /start там никогда не сработает. Единственный
    сигнал о том, что бота добавили (или сняли) как админа — это my_chat_member."""
    if event.chat.type != ChatType.CHANNEL:
        return
    new_status = event.new_chat_member.status
    if new_status == "administrator":
        await db.register_telegram_channel(event.chat.id, event.chat.title or str(event.chat.id))
    elif new_status in ("left", "kicked", "member"):
        # "member" — бота понизили из админов, без прав постить он бесполезен для канала
        await db.unregister_telegram_channel(event.chat.id)


@router.callback_query(lambda c: c.data == "menu:home")
async def cb_menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(MENU_TEXT, reply_markup=_main_menu_keyboard(callback.message.chat.type))
    await callback.answer()


@router.callback_query(lambda c: c.data == "menu:about")
async def cb_menu_about(callback: CallbackQuery) -> None:
    await callback.message.edit_text(ABOUT_TEXT, reply_markup=_back_keyboard())
    await callback.answer()


@router.callback_query(lambda c: c.data == "menu:link_stats")
async def cb_menu_link_stats(callback: CallbackQuery, db: Database) -> None:
    chat_id = callback.message.chat.id

    existing = await db.get_stats_recipient(chat_id)
    status = (
        "\n\nСейчас отчёты уже привязаны к чьей-то личке. Нажатие кнопки привяжет их заново к тебе."
        if existing is not None
        else ""
    )
    await callback.message.edit_text(
        "Чтобы получать итоговые отчёты о завершённых стримах себе в личку "
        "(живые посты о начале стрима всегда остаются в этом чате), нажми кнопку ниже "
        f"и в открывшемся диалоге с ботом нажми «Start».{status}",
        reply_markup=await _start_link_keyboard(callback.bot, chat_id),
    )
    await callback.answer()


async def _admin_groups_for_user(callback: CallbackQuery, db: Database) -> list[tuple[int, str]]:
    """(chat_id, title) чатов, доступных для удалённого управления: группы с уже
    отслеживаемым каналом + Telegram-каналы, где бот админ (их можно открыть и без
    единого добавленного Twitch-канала — чтобы было куда его добавить впервые)."""
    if callback.from_user is None:
        return []
    group_ids = set(await db.all_distinct_group_chat_ids())
    channel_titles = dict(await db.all_telegram_channels())
    candidate_ids = group_ids | channel_titles.keys()

    result = []
    for chat_id in candidate_ids:
        try:
            member = await callback.bot.get_chat_member(chat_id, callback.from_user.id)
        except Exception:
            continue  # бот мог быть удалён из чата или потерять доступ
        if member.status not in ("administrator", "creator"):
            continue
        if chat_id in channel_titles:
            title = channel_titles[chat_id]
        else:
            try:
                chat = await callback.bot.get_chat(chat_id)
                title = chat.title or str(chat_id)
            except Exception:
                title = str(chat_id)
        result.append((chat_id, title))
    return result


async def _add_to_group_button(bot: Bot) -> InlineKeyboardButton:
    bot_user = await bot.get_me()
    # startgroup — спецпараметр deep-link'а, открывает системный диалог Telegram
    # «выберите группу», чтобы добавить туда бота
    link = f"https://t.me/{bot_user.username}?startgroup=add"
    return InlineKeyboardButton(text="➕ Добавить в группу", url=link)


ADD_TO_CHANNEL_HINT = (
    "Telegram не даёт добавлять ботов в каналы напрямую по ссылке, как в группы — "
    "только вручную:\n\n"
    "1. Открой свой канал → «Управление каналом» → «Администраторы»\n"
    "2. «Добавить администратора» → найди бота по имени и добавь его\n"
    "3. Дай ему право «Публикация сообщений»\n\n"
    "После этого канал появится в списке ниже."
)


@router.callback_query(lambda c: c.data == "menu:manage_group")
async def cb_menu_manage_group(callback: CallbackQuery, db: Database) -> None:
    await callback.answer("Проверяю группы и каналы…")
    chats = await _admin_groups_for_user(callback, db)
    add_group_button = await _add_to_group_button(callback.bot)
    add_channel_button = InlineKeyboardButton(text="➕ Добавить в канал", callback_data="menu:add_channel_hint")

    if not chats:
        await callback.message.edit_text(
            "Не нашёл групп или каналов, где ты админ и где бот уже подключён.\n\n"
            "Управлять можно группами (где уже добавлен хотя бы один Twitch-канал) "
            "и каналами (где бот назначен администратором) — если ты в них админ или владелец.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [add_group_button],
                    [add_channel_button],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")],
                ]
            ),
        )
        return

    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"managegroup:{gid}")]
        for gid, title in chats
    ]
    rows.append([add_group_button])
    rows.append([add_channel_button])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")])
    await callback.message.edit_text(
        "Выбери группу или канал для управления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(lambda c: c.data == "menu:add_channel_hint")
async def cb_add_channel_hint(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        ADD_TO_CHANNEL_HINT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:manage_group")]]
        ),
    )
    await callback.answer()


# готовые интервалы тихих часов в локальном времени пользователя: (label, start_hour, end_hour)
QUIET_HOURS_PRESETS = [
    ("22:00 – 07:00", 22, 7),
    ("23:00 – 08:00", 23, 8),
    ("00:00 – 09:00", 0, 9),
]

_TIME_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})$")


def _local_minute_to_utc(local_minute: int, utc_offset_minutes: int) -> int:
    return (local_minute - utc_offset_minutes) % 1440


def _utc_minute_to_local(utc_minute: int, utc_offset_minutes: int) -> int:
    return (utc_minute + utc_offset_minutes) % 1440


def _format_minute(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


async def _quiet_hours_screen_text_and_keyboard(
    chat_id: int, db: Database
) -> tuple[str, InlineKeyboardMarkup]:
    quiet_hours = await db.get_quiet_hours(chat_id)
    rows = []
    for label, start_hour, end_hour in QUIET_HOURS_PRESETS:
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"qhpreset:{start_hour}:{end_hour}")]
        )
    rows.append([InlineKeyboardButton(text="✏️ Свой интервал", callback_data="qh:custom")])

    if quiet_hours is not None:
        start_minute, end_minute, utc_offset, notify_after = quiet_hours
        local_start = _format_minute(_utc_minute_to_local(start_minute, utc_offset))
        local_end = _format_minute(_utc_minute_to_local(end_minute, utc_offset))
        notify_label = "🔔 Сводка после: вкл" if notify_after else "🔕 Сводка после: выкл"
        rows.append([InlineKeyboardButton(text=notify_label, callback_data="qh:togglenotifyafter")])
        rows.append([InlineKeyboardButton(text="❌ Выключить тихие часы", callback_data="qh:disable")])
        text = (
            "🌙 <b>Тихие часы</b>\n\n"
            f"Сейчас включены: {local_start} – {local_end} (твоё локальное время)\n\n"
            "В это время итоговые отчёты не приходят сразу — они копятся и присылаются "
            "одной сводкой, как только тихие часы закончатся. Живые посты о начале "
            "стрима это не затрагивает — они всегда идут в группу.\n\n"
            "«Сводка после» — присылать ли вопрос «кто стримил, пока тебя не было» "
            "по окончании тихих часов."
        )
    else:
        text = (
            "🌙 <b>Тихие часы</b>\n\n"
            "Сейчас выключены. В выбранный период итоговые отчёты не будут приходить "
            "сразу — они соберутся в одну сводку и придут, как только период закончится. "
            "Живые посты о начале стрима это не затрагивает.\n\n"
            "Выбери интервал (в твоём локальном времени):"
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(lambda c: c.data == "menu:quiet_hours")
async def cb_menu_quiet_hours(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    chat_id = callback.message.chat.id
    if await db.get_utc_offset(chat_id) is None:
        await state.set_state(QuietHoursSetup.waiting_for_offset)
        await callback.message.edit_text(
            "Прежде чем настроить тихие часы, укажи свой часовой пояс относительно UTC "
            "(например, для МСК напиши <code>+3</code>, для Калининграда <code>+2</code>).",
            reply_markup=_back_keyboard(),
        )
        await callback.answer()
        return

    text, keyboard = await _quiet_hours_screen_text_and_keyboard(chat_id, db)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.message(StateFilter(QuietHoursSetup.waiting_for_offset))
async def process_utc_offset_input(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    try:
        offset_hours = int(raw)
    except ValueError:
        await message.answer(
            "Не похоже на смещение. Напиши целое число часов относительно UTC, "
            "например <code>+3</code> или <code>-5</code>.",
            reply_markup=_back_keyboard(),
        )
        return
    if abs(offset_hours) > 14:
        await message.answer(
            "Смещение вне разумного диапазона (-14..+14). Попробуй ещё раз.",
            reply_markup=_back_keyboard(),
        )
        return

    await db.set_utc_offset(message.chat.id, offset_hours * 60)
    await state.clear()
    text, keyboard = await _quiet_hours_screen_text_and_keyboard(message.chat.id, db)
    await message.answer(f"Часовой пояс сохранён: UTC{offset_hours:+d}.\n\n" + text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data and c.data.startswith("qhpreset:"))
async def cb_quiet_hours_preset(callback: CallbackQuery, db: Database) -> None:
    _, start_hour_s, end_hour_s = callback.data.split(":", 2)
    chat_id = callback.message.chat.id
    utc_offset = await db.get_utc_offset(chat_id) or 0

    start_local_minute = int(start_hour_s) * 60
    end_local_minute = int(end_hour_s) * 60
    start_utc = _local_minute_to_utc(start_local_minute, utc_offset)
    end_utc = _local_minute_to_utc(end_local_minute, utc_offset)

    await db.set_quiet_hours(chat_id, start_utc, end_utc, utc_offset)
    text, keyboard = await _quiet_hours_screen_text_and_keyboard(chat_id, db)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Тихие часы включены")


@router.callback_query(lambda c: c.data == "qh:custom")
async def cb_quiet_hours_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuietHoursSetup.waiting_for_custom_time)
    await callback.message.edit_text(
        "Напиши интервал в своём локальном времени в формате <code>23:00-08:00</code>.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:quiet_hours")]]
        ),
    )
    await callback.answer()


@router.message(StateFilter(QuietHoursSetup.waiting_for_custom_time))
async def process_custom_quiet_hours(message: Message, state: FSMContext, db: Database) -> None:
    match = _TIME_RANGE_RE.match((message.text or "").strip())
    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:quiet_hours")]]
    )
    if match is None:
        await message.answer(
            "Не похоже на интервал. Формат: <code>23:00-08:00</code>.", reply_markup=back_keyboard
        )
        return

    start_hour, start_min, end_hour, end_min = (int(g) for g in match.groups())
    if not (0 <= start_hour <= 23 and 0 <= start_min <= 59 and 0 <= end_hour <= 23 and 0 <= end_min <= 59):
        await message.answer("Часы или минуты вне диапазона. Попробуй ещё раз.", reply_markup=back_keyboard)
        return

    chat_id = message.chat.id
    utc_offset = await db.get_utc_offset(chat_id) or 0
    start_local = start_hour * 60 + start_min
    end_local = end_hour * 60 + end_min
    if start_local == end_local:
        await message.answer(
            "Начало и конец совпадают — тихие часы не будут действовать. Попробуй другой интервал.",
            reply_markup=back_keyboard,
        )
        return

    start_utc = _local_minute_to_utc(start_local, utc_offset)
    end_utc = _local_minute_to_utc(end_local, utc_offset)
    await db.set_quiet_hours(chat_id, start_utc, end_utc, utc_offset)
    await state.clear()

    text, keyboard = await _quiet_hours_screen_text_and_keyboard(chat_id, db)
    await message.answer("Готово.\n\n" + text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data == "qh:disable")
async def cb_quiet_hours_disable(callback: CallbackQuery, db: Database) -> None:
    chat_id = callback.message.chat.id
    await db.clear_quiet_hours(chat_id)
    await db.get_and_clear_deferred_reports(chat_id)
    await db.clear_quiet_hours_digest_sent(chat_id)
    text, keyboard = await _quiet_hours_screen_text_and_keyboard(chat_id, db)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Тихие часы выключены")


@router.callback_query(lambda c: c.data == "qh:togglenotifyafter")
async def cb_quiet_hours_toggle_notify_after(callback: CallbackQuery, db: Database) -> None:
    chat_id = callback.message.chat.id
    quiet_hours = await db.get_quiet_hours(chat_id)
    if quiet_hours is None:
        await callback.answer("Тихие часы сейчас выключены.", show_alert=True)
        return
    _start, _end, _offset, notify_after = quiet_hours
    await db.set_quiet_hours_notify_after(chat_id, not notify_after)
    text, keyboard = await _quiet_hours_screen_text_and_keyboard(chat_id, db)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(
        "Сводка после тихих часов выключена" if notify_after else "Сводка после тихих часов включена"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("quietdigest:"))
async def cb_quiet_digest_response(callback: CallbackQuery, db: Database) -> None:
    _, action, chat_id_s = callback.data.split(":", 2)
    chat_id = int(chat_id_s)

    entries = await db.get_and_clear_deferred_reports(chat_id)
    await db.clear_quiet_hours_digest_sent(chat_id)

    if not entries:
        await callback.answer()
        return

    if action == "skip":
        await callback.message.edit_text("Хорошо, пропускаю подробности.")
        await callback.answer()
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Готовлю отчёты…")
    for source_chat_id, login, _stream_id, _ended_at in entries:
        await _send_report(callback.message, source_chat_id, login, db)


@router.callback_query(lambda c: c.data and c.data.startswith("managegroup:"))
async def cb_manage_group(callback: CallbackQuery, db: Database) -> None:
    target_chat_id = int(callback.data.split(":", 1)[1])

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Ты больше не админ этой группы/канала.", show_alert=True)
        return

    text, keyboard = await _render_channels_list(
        target_chat_id, db,
        back_callback="menu:manage_group", allow_add=True,
        title_prefix="Управление удалённо:\n",
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


_CHANNELS_HINT = (
    "📡 <b>Отслеживаемые каналы</b>\n"
    "Нажми на канал, чтобы открыть его настройки."
)

_CHANNELS_HINT_PRIVATE = _CHANNELS_HINT

_CHANNELS_HINT_TG_CHANNEL = (
    "📡 <b>Отслеживаемые каналы</b>\n"
    "Нажми на канал, чтобы открыть его настройки.\n\n"
    "В Telegram-канале доступен только живой пост о начале стрима — "
    "итоговый отчёт здесь не отправляется."
)


async def _render_channels_list(
    target_chat_id: int,
    db: Database,
    *,
    back_callback: str = "menu:home",
    allow_add: bool = True,
    title_prefix: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    channels = await db.list_channels_with_routing(target_chat_id)
    # id личных чатов в Telegram положительные, групп/каналов — отрицательные;
    # в личке переключать получателя отчёта некуда — он и так эта же личка
    is_private = target_chat_id > 0
    is_telegram_channel = await db.is_telegram_channel(target_chat_id)
    if is_telegram_channel:
        hint = _CHANNELS_HINT_TG_CHANNEL
    else:
        hint = _CHANNELS_HINT_PRIVATE if is_private else _CHANNELS_HINT
    text = title_prefix + (hint if channels else "Список пуст. Добавь канал кнопкой ниже.")
    chat_default_recipient = await db.get_stats_recipient(target_chat_id)
    keyboard = _channels_keyboard(
        target_chat_id, channels, chat_default_recipient,
        back_callback=back_callback, allow_add=allow_add,
        show_recipient_toggle=not is_private,
        is_telegram_channel=is_telegram_channel,
    )
    return text, keyboard


async def _check_manage_permission(callback: CallbackQuery, target_chat_id: int) -> bool:
    """True, если пользователь может управлять каналами target_chat_id из текущего
    диалога. Если диалог открыт прямо в target_chat_id — разрешено всем участникам
    (как и раньше). Если управление идёт удалённо (например, из лички) — только
    админам/владельцу target_chat_id, проверяется через Telegram API."""
    if callback.message.chat.id == target_chat_id:
        return True
    if callback.from_user is None:
        return False
    member = await callback.bot.get_chat_member(target_chat_id, callback.from_user.id)
    return member.status in ("administrator", "creator")


@router.callback_query(lambda c: c.data == "menu:list")
async def cb_menu_list(callback: CallbackQuery, db: Database) -> None:
    text, keyboard = await _render_channels_list(callback.message.chat.id, db)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def _channels_list_back_callback(current_chat_id: int, target_chat_id: int) -> str:
    return "menu:home" if current_chat_id == target_chat_id else f"managegroup:{target_chat_id}"


async def _render_channel_card(
    target_chat_id: int, login: str, db: Database, *, list_back_callback: str
) -> tuple[str, InlineKeyboardMarkup] | None:
    """None, если канал уже не отслеживается (например, удалили из другого места)."""
    channels = await db.list_channels_with_routing(target_chat_id)
    match = next((c for c in channels if c[0] == login), None)
    if match is None:
        return None
    _, notify_enabled, post_recipient, report_format, raid_detection_enabled, quiet_hours_exempt = match

    is_private = target_chat_id > 0
    is_telegram_channel = await db.is_telegram_channel(target_chat_id)
    chat_default_recipient = await db.get_stats_recipient(target_chat_id)

    # тихие часы — настройка получателя итогового отчёта, а не исходного чата отслеживания
    # (отчёт мог быть переадресован в чью-то личку) — переключатель исключения показываем,
    # только если у фактического получателя тихие часы вообще включены
    recipient_chat_id = await db.resolve_post_recipient(target_chat_id, login)
    show_quiet_hours_toggle = (
        not is_telegram_channel and await db.get_quiet_hours(recipient_chat_id) is not None
    )

    if is_telegram_channel:
        text = (
            f"📡 <b>{login}</b>\n\n"
            "В Telegram-канале доступен только живой пост о начале стрима — "
            "итоговый отчёт здесь не отправляется.\n\n"
            "🔔/🔕 — присылать ли живой пост, когда канал выходит в эфир"
        )
    else:
        lines = [
            f"📡 <b>{login}</b>\n",
            "🔔/🔕 — присылать ли живой пост, когда канал выходит в эфир",
        ]
        if not is_private:
            lines.append("👥/📩 — куда слать итоговый отчёт после стрима: в группу или тебе в личку")
        lines.append("📑/📄 — формат итогового отчёта: развёрнутый (текст + HTML с графиком) или краткий (только текст)")
        lines.append("⚡ — детектор рейдов: слать ли уведомление, когда канал начинают рейдить")
        if show_quiet_hours_toggle:
            lines.append("🌙 — действуют ли тихие часы получателя на этот канал (можно сделать исключение)")
        if await db.get_user_token(login) is None:
            lines.append(
                "\n⚠️ Число фолловеров недоступно — стример не подключил свой Twitch-аккаунт "
                "к боту (/auth_twitch)."
            )
        text = "\n".join(lines)

    keyboard = _channel_card_keyboard(
        target_chat_id, login, notify_enabled, post_recipient, chat_default_recipient,
        report_format, raid_detection_enabled, quiet_hours_exempt,
        back_callback=f"channellist:{target_chat_id}:{list_back_callback}",
        show_recipient_toggle=not is_private,
        is_telegram_channel=is_telegram_channel,
        show_quiet_hours_toggle=show_quiet_hours_toggle,
    )
    return text, keyboard


@router.callback_query(lambda c: c.data and c.data.startswith("channelcard:"))
async def cb_channel_card(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)
    list_back = _channels_list_back_callback(callback.message.chat.id, target_chat_id)

    result = await _render_channel_card(target_chat_id, login, db, list_back_callback=list_back)
    if result is None:
        await callback.answer("Канал больше не отслеживается.", show_alert=True)
        return
    text, keyboard = result
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("channellist:"))
async def cb_channel_list_back(callback: CallbackQuery, db: Database) -> None:
    # формат: channellist:<target_chat_id>:<list_back_callback>
    _, target_chat_id_s, list_back_callback = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    text, keyboard = await _render_channels_list(
        target_chat_id, db, back_callback=list_back_callback,
        allow_add=callback.message.chat.id == target_chat_id,
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


async def _refresh_channel_card(callback: CallbackQuery, db: Database, target_chat_id: str, login: str) -> None:
    list_back = _channels_list_back_callback(callback.message.chat.id, int(target_chat_id))
    result = await _render_channel_card(int(target_chat_id), login, db, list_back_callback=list_back)
    if result is None:
        return
    text, keyboard = result
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data and c.data.startswith("togglenotify:"))
async def cb_toggle_notify(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    currently_enabled = await db.get_notify_enabled(target_chat_id, login)
    await db.set_notify_enabled(target_chat_id, login, not currently_enabled)

    await _refresh_channel_card(callback, db, target_chat_id_s, login)
    await callback.answer(
        "Уведомления выключены" if currently_enabled else "Уведомления включены"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("togglerecipient:"))
async def cb_toggle_recipient(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if target_chat_id > 0:
        # личка — переключать некуда, отчёт и так приходит туда же, куда живой пост
        await callback.answer("В личке переключать некуда.")
        return

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    if callback.from_user is None or not await db.is_known_private_user(callback.from_user.id):
        await callback.answer(
            "Сначала напиши боту в личке хотя бы раз (например, /start), "
            "чтобы он мог присылать туда посты.",
            show_alert=True,
        )
        return

    # смотрим, куда пост реально уходит СЕЙЧАС (с учётом дефолтов чата), а не только
    # на наличие явной привязки канала — иначе переключатель не сработает предсказуемо,
    # если весь чат уже привязан к личке через общую настройку
    current_effective = await db.resolve_post_recipient(target_chat_id, login)
    if current_effective == target_chat_id:
        # сейчас уходит в группу — явно переключаем на личку того, кто нажал
        await db.set_post_recipient(target_chat_id, login, callback.from_user.id)
        answer_text = "Отчёт по этому каналу теперь идёт тебе в личку"
    else:
        # сейчас уходит в чью-то личку (по умолчанию или явно) — явно закрепляем за группой
        await db.set_post_recipient(target_chat_id, login, target_chat_id)
        answer_text = "Отчёт по этому каналу теперь идёт в группу"

    await _refresh_channel_card(callback, db, target_chat_id_s, login)
    await callback.answer(answer_text)


@router.callback_query(lambda c: c.data and c.data.startswith("toggleformat:"))
async def cb_toggle_format(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    current_format = await db.get_report_format(target_chat_id, login)
    new_format = "full" if current_format == "brief" else "brief"
    await db.set_report_format(target_chat_id, login, new_format)

    await _refresh_channel_card(callback, db, target_chat_id_s, login)
    await callback.answer(
        "Отчёт теперь краткий (только текст)" if new_format == "brief" else "Отчёт теперь развёрнутый (текст + HTML)"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("toggleraid:"))
async def cb_toggle_raid(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    currently_enabled = await db.get_raid_detection_enabled(target_chat_id, login)
    await db.set_raid_detection_enabled(target_chat_id, login, not currently_enabled)

    await _refresh_channel_card(callback, db, target_chat_id_s, login)
    await callback.answer(
        "Детектор рейдов выключен" if currently_enabled else "Детектор рейдов включён"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("togglequiethoursexempt:"))
async def cb_toggle_quiet_hours_exempt(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    currently_exempt = await db.get_quiet_hours_exempt(target_chat_id, login)
    await db.set_quiet_hours_exempt(target_chat_id, login, not currently_exempt)

    await _refresh_channel_card(callback, db, target_chat_id_s, login)
    await callback.answer(
        "Тихие часы снова действуют на этот канал" if currently_exempt
        else "Этот канал теперь исключён из тихих часов — отчёт придёт сразу"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("menu:add"))
async def cb_menu_add(callback: CallbackQuery, state: FSMContext) -> None:
    # "menu:add" — добавление в текущий чат; "menu:add:<target_chat_id>" — удалённое
    # добавление (например, из карточки Telegram-канала, открытой из лички)
    parts = callback.data.split(":", 2)
    target_chat_id = int(parts[2]) if len(parts) > 2 else callback.message.chat.id

    if target_chat_id != callback.message.chat.id and not await _check_manage_permission(
        callback, target_chat_id
    ):
        await callback.answer("Только админы этой группы/канала могут добавлять каналы.", show_alert=True)
        return

    await state.set_state(AddChannel.waiting_for_login)
    await state.update_data(target_chat_id=target_chat_id)
    back_callback = _channels_list_back_callback(callback.message.chat.id, target_chat_id)
    await callback.message.edit_text(
        "Напиши логин Twitch-канала (например: dobriy_yura).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)]]
        ),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("untrack:"))
async def cb_untrack(callback: CallbackQuery, db: Database) -> None:
    _, target_chat_id_s, login = callback.data.split(":", 2)
    target_chat_id = int(target_chat_id_s)

    if not await _check_manage_permission(callback, target_chat_id):
        await callback.answer("Только админы этой группы могут менять настройки.", show_alert=True)
        return

    await db.remove_channel(target_chat_id, login)
    back_callback = _channels_list_back_callback(callback.message.chat.id, target_chat_id)
    text, keyboard = await _render_channels_list(
        target_chat_id, db, back_callback=back_callback, allow_add=callback.message.chat.id == target_chat_id
    )
    text = "Канал удалён.\n\n" + text
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.message(StateFilter(AddChannel.waiting_for_login))
async def process_login_input(
    message: Message, state: FSMContext, db: Database, twitch: TwitchClient
) -> None:
    data = await state.get_data()
    target_chat_id = data.get("target_chat_id", message.chat.id)
    back_keyboard = _back_keyboard() if target_chat_id == message.chat.id else InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=_channels_list_back_callback(message.chat.id, target_chat_id),
        )]]
    )

    login = _extract_login_text(message.text or "")
    if login is None:
        await message.answer(
            "Не похоже на логин Twitch. Попробуй ещё раз (например: dobriy_yura).",
            reply_markup=back_keyboard,
        )
        return

    if not await twitch.channel_exists(login):
        await message.answer(
            f"Канал «{login}» не найден на Twitch. Попробуй другой логин.",
            reply_markup=back_keyboard,
        )
        return

    if await db.count_channels(target_chat_id) >= MAX_CHANNELS_PER_CHAT:
        await state.clear()
        await message.answer(
            f"В этом чате уже отслеживается максимум каналов ({MAX_CHANNELS_PER_CHAT}). "
            "Удали ненужный через «Мои каналы», прежде чем добавлять новый.",
            reply_markup=back_keyboard,
        )
        return

    had_channels_before = await db.count_channels(target_chat_id) > 0
    added = await db.add_channel(target_chat_id, login)
    await state.clear()

    if added:
        text = await _added_channel_summary(message, db, login, is_first_channel=not had_channels_before)
    else:
        text = f"Канал «{login}» уже отслеживается в этом чате."

    if target_chat_id == message.chat.id:
        stats_warning = await _ensure_stats_recipient(message, db)
        if stats_warning:
            await message.answer(
                text + "\n\n" + stats_warning,
                reply_markup=await _start_link_keyboard(message.bot, message.chat.id),
            )
            return

    back_callback = _channels_list_back_callback(message.chat.id, target_chat_id)
    _, keyboard = await _render_channels_list(
        target_chat_id, db, back_callback=back_callback, allow_add=target_chat_id == message.chat.id
    )
    await message.answer(text, reply_markup=keyboard)


# Текстовые команды остаются как альтернативный способ управления
@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject, db: Database, twitch: TwitchClient) -> None:
    login = _extract_login(command)
    if login is None:
        await message.answer("Использование: /track [twitch_логин]\nНапример: /track dobriy_yura")
        return

    if not await twitch.channel_exists(login):
        await message.answer(f"Канал «{login}» не найден на Twitch.")
        return

    if await db.count_channels(message.chat.id) >= MAX_CHANNELS_PER_CHAT:
        await message.answer(
            f"В этом чате уже отслеживается максимум каналов ({MAX_CHANNELS_PER_CHAT}). "
            "Удали ненужный через /untrack или кнопку «Мои каналы», прежде чем добавлять новый."
        )
        return

    had_channels_before = await db.count_channels(message.chat.id) > 0
    added = await db.add_channel(message.chat.id, login)
    if added:
        text = await _added_channel_summary(message, db, login, is_first_channel=not had_channels_before)
    else:
        text = f"Канал «{login}» уже отслеживается в этом чате."

    stats_warning = await _ensure_stats_recipient(message, db)
    if stats_warning:
        await message.answer(text + "\n\n" + stats_warning, reply_markup=await _start_link_keyboard(message.bot, message.chat.id))
        return

    await message.answer(text)


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject, db: Database) -> None:
    login = _extract_login(command)
    if login is None:
        await message.answer("Использование: /untrack [twitch_логин]")
        return

    removed = await db.remove_channel(message.chat.id, login)
    if removed:
        await message.answer(f"Канал «{login}» больше не отслеживается.")
    else:
        await message.answer(f"Канал «{login}» не найден в списке отслеживаемых.")


@router.message(Command("list"))
async def cmd_list(message: Message, db: Database) -> None:
    channels = await db.list_channels_with_notify(message.chat.id)
    if not channels:
        await message.answer("Список пуст. Добавь канал через /track [twitch_логин].")
        return

    text = "Отслеживаемые каналы:\n" + "\n".join(
        f"• {'🔔' if enabled else '🔕'} {login}" for login, enabled in channels
    )
    await message.answer(text)


async def _send_report(message: Message, chat_id: int, login: str, db: Database) -> None:
    record = await db.get_last_finished_stream(chat_id, login)
    if record is None:
        await message.answer(f"Пока нет ни одного завершённого стрима «{login}» в этом чате.")
        return

    (
        stream_id, ended_at, started_at, title, duration_seconds, peak_viewers,
        avg_viewers, _new_followers, new_followers_text, unique_chatters, join_reliable,
        top_chatters_json, raid_events_json,
    ) = record

    age = time.time() - ended_at
    if age > REPORT_RETENTION_SECONDS:
        await message.answer(
            f"Последний стрим «{login}» завершился более 24 часов назад — "
            "полный отчёт с графиком и списком чатеров больше недоступен."
        )
        return

    samples = await db.get_stream_samples(chat_id, login, stream_id)
    chat_activity = await db.get_chat_activity_samples(chat_id, login, stream_id)
    chatter_nicks = await db.get_chat_unique_nicks(chat_id, login, stream_id)
    vod = await db.get_vod(chat_id, login, stream_id)
    top_chatters = [tuple(item) for item in json.loads(top_chatters_json)] if top_chatters_json else []
    raid_events = [tuple(item) for item in json.loads(raid_events_json)] if raid_events_json else []

    report_html = build_report_html(
        login,
        started_at or "",
        format_duration_seconds(duration_seconds),
        peak_viewers,
        avg_viewers,
        samples,
        new_followers=new_followers_text,
        chat_activity=chat_activity,
        unique_chatters=unique_chatters or len(chatter_nicks),
        unique_chatters_reliable=bool(join_reliable) if join_reliable is not None else True,
        chatter_nicks=chatter_nicks,
        vod_url=vod[0] if vod else None,
        top_chatters=top_chatters,
        raid_events=raid_events,
    )

    file = BufferedInputFile(report_html.encode("utf-8"), filename=f"stream_{login}_{stream_id}.html")
    await message.answer_document(file, caption=f"Отчёт по последнему стриму «{login}».")


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject, db: Database) -> None:
    login = _extract_login(command)
    if login is None:
        await message.answer("Использование: /report [twitch_логин]\nНапример: /report dobriy_yura")
        return
    await _send_report(message, message.chat.id, login, db)


@router.callback_query(lambda c: c.data == "menu:report")
async def cb_menu_report(callback: CallbackQuery, db: Database) -> None:
    logins = await db.list_channels(callback.message.chat.id)
    if not logins:
        await callback.message.edit_text(
            "Список каналов пуст. Сначала добавь канал.", reply_markup=_back_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "По какому каналу нужен отчёт?", reply_markup=_report_channels_keyboard(logins)
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("report:"))
async def cb_report_channel(callback: CallbackQuery, db: Database) -> None:
    login = callback.data.split(":", 1)[1]
    await callback.answer("Готовлю отчёт…")
    await _send_report(callback.message, callback.message.chat.id, login, db)
