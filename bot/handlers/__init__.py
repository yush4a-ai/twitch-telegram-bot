from aiogram import Dispatcher

from .auth import router as auth_router
from .streams import router as streams_router
from .payments import build_payment_router
from .navigation import build_navigation_router


def register_all_handlers(dp: Dispatcher) -> None:
    dp.include_router(build_payment_router())
    dp.include_router(build_navigation_router())
    dp.include_router(streams_router)
    dp.include_router(auth_router)
