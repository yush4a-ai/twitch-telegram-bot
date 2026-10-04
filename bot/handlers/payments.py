"""Payment updates from the existing authenticated aiogram polling pipeline."""

import asyncio
import time

from aiogram import F, Router
from aiogram.filters import Command
from html import escape

from ..billing_provider import PaymentVerificationError
from ..plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE
from ..stars_provider import TelegramStarsProvider
from ..legal_documents import get_support_state


async def on_payment_support(message, config):
    state = get_support_state(config)
    lines = ["<b>Поддержка по подписке и оплате</b>"]
    if state.telegram_url:
        lines.append(escape(state.telegram_url))
    if state.email:
        lines.append(escape(state.email))
    if not state.available:
        lines.append("Контакт поддержки пока не указан.")
    await message.answer("\n\n".join(lines), disable_web_page_preview=True)


async def _context(service, payload):
    provider = service._provider
    if not isinstance(provider, TelegramStarsProvider) or not service._local_runtime():
        raise PaymentVerificationError("Stars updates disabled")
    order_id, attempt_id = provider.parse_payload(payload)
    order = await service._db.get_billing_order(order_id)
    attempt = await service._store.get_attempt(attempt_id)
    if (order is None or attempt is None or attempt.order_id != order_id
            or attempt.provider != "telegram_stars" or attempt.method != "stars"):
        raise PaymentVerificationError("unknown Stars order/attempt")
    return provider, order, attempt


async def on_precheckout(query, billing_service, bot, *, now=None):
    at = time.time() if now is None else now
    ok, error = False, PAYMENT_UNAVAILABLE_MESSAGE
    async def check():
        provider, order, attempt = await _context(billing_service, query.invoice_payload)
        decision = provider.validate_precheckout(query, order, now=at)
        if not decision.ok or attempt.state not in {"creating", "pending", "creation_unknown"}:
            return False, decision.error_message or "Заказ уже закрыт. Откройте тариф ещё раз."
        if await billing_service._db.has_viewer_plus(order.telegram_user_id, now=at):
            return False, "Подписка уже действует. Откройте раздел «Тариф»."
        if order.plan == "streamer_plus":
            identity = await billing_service._db.get_streamer_identity(order.telegram_user_id)
            if identity is None or identity[0] != order.broadcaster_id:
                return False, "Подключение Twitch изменилось. Откройте тариф ещё раз."
        return True, None
    try:
        ok, error = await asyncio.wait_for(check(), timeout=3)
    except Exception:
        pass
    await asyncio.wait_for(bot.answer_pre_checkout_query(pre_checkout_query_id=query.id, ok=ok,
        **({"error_message": error} if not ok else {})), timeout=5)


async def _apply_message(message, service, *, refund, now):
    payment = message.refunded_payment if refund else message.successful_payment
    if payment is None:
        return None
    try:
        provider, order, attempt = await _context(service, payment.invoice_payload)
        evidence = (provider.refunded_payment_to_evidence(message, order, expected_charge=attempt.provider_reference, now=now)
                    if refund else provider.successful_payment_to_evidence(message, order, now=now))
        return await service.apply_payment_evidence(evidence, now=now)
    except (PaymentVerificationError, PermissionError, ValueError):
        return None


async def on_successful_payment(message, billing_service, *, now=None):
    return await _apply_message(message, billing_service, refund=False, now=time.time() if now is None else now)


async def on_refunded_payment(message, billing_service, *, now=None):
    return await _apply_message(message, billing_service, refund=True, now=time.time() if now is None else now)


def build_payment_router():
    router = Router(name="payments")
    router.pre_checkout_query.register(on_precheckout)
    router.message.register(on_successful_payment, F.successful_payment)
    router.message.register(on_refunded_payment, F.refunded_payment)
    router.message.register(on_payment_support, Command("paysupport"))
    return router
