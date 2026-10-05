"""Кнопка «Больше не присылать» в рассылках владельца.

Отписка касается только рассылок: уведомления о стримах, на которые человек
подписался, продолжают приходить, а вернуть человека может владелец вручную.
"""
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery

router = Router(name="broadcasts")

OPTOUT_CALLBACK = "broadcast_optout"
OPTOUT_TEXT = "Больше не присылать"


async def cb_broadcast_optout(callback: CallbackQuery, db=None) -> None:
    if db is None:
        await callback.answer("Не получилось. Попробуйте позже.", show_alert=True)
        return
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    await db.opt_out_broadcast(int(user.id), now=time.time(), reason="button")
    await callback.answer("Готово: рассылки больше не придут", show_alert=False)


def build_broadcast_router() -> Router:
    router.callback_query(F.data == OPTOUT_CALLBACK)(cb_broadcast_optout)
    return router
