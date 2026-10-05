"""Метрики аудитории: сколько людей приходит, возвращается и где останавливается.

Считаем только то, что действительно есть в базе. Истории активности по дням в
проекте нет, поэтому удержание оценивается грубо: сравнением первого и
последнего появления человека. Где данных нет, возвращаем None, а не ноль.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

MSK_OFFSET_SECONDS = 3 * 3600
DAY_SECONDS = 86400


@dataclass(frozen=True)
class AudienceMetrics:
    known_people: int
    new_7d: int
    new_30d: int
    active_7d: int
    active_today: int
    returned: int
    with_streamers: int
    reached_live: int
    with_delivery: int
    stayed_week: int

    def as_dict(self) -> dict:
        return asdict(self)


def msk_day_start(now: float) -> float:
    """Начало текущих суток по Москве: фиксированное смещение +3, как в боте."""
    shifted = now + MSK_OFFSET_SECONDS
    return (shifted // DAY_SECONDS) * DAY_SECONDS - MSK_OFFSET_SECONDS


async def _scalar(db, sql: str, params: tuple = ()) -> int:
    cursor = await db.conn.execute(sql, params)
    row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def collect_audience(db, *, now: float | None = None) -> AudienceMetrics:
    """Собирает метрики одним проходом, без внешних запросов и без выдуманных чисел."""
    at = time.time() if now is None else now
    week_ago = at - 7 * DAY_SECONDS
    month_ago = at - 30 * DAY_SECONDS
    today_start = msk_day_start(at)
    known = await _scalar(db, "SELECT COUNT(*) FROM telegram_user_profiles")
    new_7d = await _scalar(db, "SELECT COUNT(*) FROM telegram_user_profiles WHERE first_seen_at >= ?", (week_ago,))
    new_30d = await _scalar(db, "SELECT COUNT(*) FROM telegram_user_profiles WHERE first_seen_at >= ?", (month_ago,))
    active_7d = await _scalar(db, "SELECT COUNT(*) FROM telegram_user_profiles WHERE last_active_at >= ?", (week_ago,))
    active_today = await _scalar(db, "SELECT COUNT(*) FROM telegram_user_profiles WHERE last_active_at >= ?", (today_start,))
    # Вернулся: последняя активность позже первого дня присутствия.
    returned = await _scalar(
        db,
        "SELECT COUNT(*) FROM telegram_user_profiles WHERE last_active_at >= first_seen_at + ?",
        (DAY_SECONDS,),
    )
    stayed_week = await _scalar(
        db,
        "SELECT COUNT(*) FROM telegram_user_profiles WHERE last_active_at >= first_seen_at + ?",
        (7 * DAY_SECONDS,),
    )
    with_streamers = await _scalar(
        db,
        "SELECT COUNT(*) FROM telegram_user_profiles p WHERE EXISTS ("
        " SELECT 1 FROM tracked_channels c WHERE c.chat_id = p.user_id)",
    )
    # Дошли до эфира: по каналам человека бот видел хотя бы один эфир. Это
    # измеримый шаг, в отличие от факта доставки уведомления.
    reached_live = await _scalar(
        db,
        "SELECT COUNT(*) FROM telegram_user_profiles p WHERE EXISTS ("
        " SELECT 1 FROM stream_history h WHERE h.chat_id = p.user_id)",
    )
    # Подтверждённая доставка. Ведётся не для всех: история событий зрителя
    # записывается только при действующем Plus, поэтому число всегда ниже
    # реального и показывается отдельно от воронки.
    with_delivery = await _scalar(
        db,
        "SELECT COUNT(*) FROM telegram_user_profiles p WHERE EXISTS ("
        " SELECT 1 FROM viewer_event_history e"
        " WHERE e.telegram_user_id = p.user_id AND e.outcome = 'sent')"
        " OR EXISTS ("
        " SELECT 1 FROM report_deliveries d"
        " WHERE d.recipient_chat_id = p.user_id AND d.text_sent = 1)",
    )
    return AudienceMetrics(
        known_people=known,
        new_7d=new_7d,
        new_30d=new_30d,
        active_7d=active_7d,
        active_today=active_today,
        returned=returned,
        with_streamers=with_streamers,
        reached_live=reached_live,
        with_delivery=with_delivery,
        stayed_week=stayed_week,
    )


def funnel(metrics: AudienceMetrics) -> tuple[dict, ...]:
    """Воронка от знакомства до недели: шаги и доля от предыдущего.

    Шаг «Дождались эфира» стоит вместо «Получили уведомление»: факт доставки
    ведётся только для платных зрителей, поэтому в воронке он занижал бы всех.
    Подтверждённая доставка показывается отдельным числом.
    """
    steps = (
        ("Открыли бота", metrics.known_people),
        ("Добавили стримера", metrics.with_streamers),
        ("Дождались эфира", metrics.reached_live),
        ("Остались на неделю", metrics.stayed_week),
    )
    result = []
    previous = None
    for title, value in steps:
        share = None if previous in (None, 0) else round(value / previous, 4)
        result.append({"title": title, "value": value, "share_of_previous": share})
        previous = value
    return tuple(result)
