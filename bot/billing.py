"""Test-only billing orchestration over a provider contract and SQLite ledger."""

from __future__ import annotations

import math
import hashlib
import time
import uuid
from typing import Mapping

from .billing_provider import CheckoutSession, PaymentProvider
from .database import Database


class BillingService:
    def __init__(self, db: Database, provider: PaymentProvider) -> None:
        if provider.provider_id != "mock":
            raise ValueError("R5 permits only the mock provider")
        self._db = db
        self._provider = provider

    async def create_checkout(
        self, telegram_user_id: int, request_key: str, duration_seconds: int,
        *, now: float | None = None,
    ) -> CheckoutSession:
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid checkout time")
        order = await self._db.create_billing_order(
            uuid.uuid4().hex, request_key, telegram_user_id, duration_seconds, now=at,
        )
        if order.status in {"cancelled", "expired", "refunded"} or at >= order.checkout_expires_at:
            raise ValueError("checkout is closed")
        if order.checkout_reference is not None:
            return CheckoutSession(order.order_id, order.checkout_reference)
        checkout = await self._provider.create_checkout(order.order_id, order.units, order.currency)
        if checkout.order_id != order.order_id:
            raise ValueError("provider returned wrong order ID")
        saved = await self._db.save_billing_checkout_reference(
            order.order_id, checkout.reference, now=at,
        )
        return CheckoutSession(saved.order_id, saved.checkout_reference)

    async def cancel_order(
        self, telegram_user_id: int, order_id: str, *, now: float | None = None,
    ) -> bool:
        at = time.time() if now is None else now
        order = await self._db.get_billing_order(order_id)
        if order is None or order.telegram_user_id != telegram_user_id:
            raise PermissionError("billing order is not owned by Telegram user")
        if order.status in {"cancelled", "expired"}:
            return False
        if order.status != "pending":
            raise ValueError("only pending billing orders can be cancelled")
        if order.checkout_reference is not None and at < order.checkout_expires_at:
            await self._provider.cancel_checkout(order.checkout_reference)
        return await self._db.cancel_billing_order(telegram_user_id, order_id, now=at)

    async def expire_pending(self, *, now: float | None = None) -> int:
        at = time.time() if now is None else now
        return await self._db.expire_pending_billing_orders(now=at)

    async def handle_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: float | None = None,
    ) -> str:
        at = time.time() if now is None else now
        event = self._provider.verify_webhook(body, headers, now=at)
        return await self._db.apply_verified_billing_event(
            event, hashlib.sha256(body).hexdigest(), now=at,
        )

    async def request_refund(
        self, telegram_user_id: int, order_id: str, request_key: str,
        *, now: float | None = None,
    ) -> str:
        at = time.time() if now is None else now
        order = await self._db.get_billing_order(order_id)
        if order is None or order.telegram_user_id != telegram_user_id:
            raise PermissionError("billing order is not owned by Telegram user")
        existing = await self._db.get_billing_refund_request(request_key)
        if existing is not None:
            if existing[0] != order_id:
                raise ValueError("refund request key conflict")
            return existing[1]
        if order.status != "paid":
            raise ValueError("only paid orders can request a refund")
        payment = await self._db.get_billing_payment(order_id)
        if payment is None or payment.status != "captured":
            raise ValueError("captured payment is missing")
        reference = await self._provider.request_refund(payment.payment_id, request_key)
        return await self._db.save_billing_refund_request(
            order_id, request_key, reference, now=at,
        )
