"""Signed subscription state and pinned, allowlisted non-monetary QA orders."""

from __future__ import annotations

import hashlib
import secrets
import time

from aiohttp import web

from .billing import BillingService
from .billing_provider import MockPaymentProvider, VerifiedPaymentEvent
from .database import Database
from .mini_app_auth import verified_payload


def install_mini_app_billing_routes(
    app: web.Application, db: Database, bot_token: str, *,
    test_enabled: bool = False, test_user_ids: frozenset[int] = frozenset(),
) -> None:
    # This secret never leaves the server. No real payment provider is wired here.
    provider = MockPaymentProvider(hashlib.sha256(
        b"mini-app-mock-billing:" + bot_token.encode("utf-8")
    ).digest()) if test_enabled else None
    service = BillingService(db, provider) if provider is not None else None

    async def read(request: web.Request):
        user_id, values, status = await verified_payload(request, bot_token)
        if status != 200:
            return None, None, web.json_response({"error": "unauthorized"}, status=status)
        return user_id, values, None

    def allowed(user_id: int) -> bool:
        return service is not None and user_id in test_user_ids

    async def own_order(user_id: int, values: dict):
        order_id = values.get("order_id")
        if not isinstance(order_id, str) or len(order_id) != 32:
            return None, web.json_response({"error": "invalid_order"}, status=400)
        order = await db.get_billing_order(order_id)
        if order is None or order.telegram_user_id != user_id or order.provider != "mock":
            return None, web.json_response({"error": "order_denied"}, status=403)
        return order, None

    async def state(request: web.Request) -> web.Response:
        user_id, _values, error = await read(request)
        if error is not None:
            return error
        now = time.time()
        identity = await db.get_streamer_identity(user_id)
        viewer = await db.get_current_plus_grant(user_id, "viewer_plus", now=now)
        streamer = await db.get_current_plus_grant(user_id, "streamer_plus", now=now)
        orders = await db.list_billing_orders_for_user(user_id)

        def display_status(order):
            if order.status == "pending" and now >= order.checkout_expires_at:
                return "expired"
            if order.status == "paid" and order.paid_at is not None and now >= order.paid_at + order.duration_seconds:
                return "expired"
            return order.status

        return web.json_response({
            "viewer": {"active": viewer is not None,
                       "expires_at": viewer[1] if viewer else None,
                       "source": viewer[0] if viewer else None},
            "streamer": {"linked": identity is not None,
                         "twitch_login": identity[1] if identity else None,
                         "active": streamer is not None,
                         "expires_at": streamer[1] if streamer else None,
                         "source": streamer[0] if streamer else None},
            "history": [
                {"order_id": order.order_id, "product": order.plan,
                 "status": display_status(order), "created_at": order.created_at}
                for order in orders
            ],
            "test_checkout_available": allowed(user_id),
            "money_charged": False,
        })

    async def test_checkout(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not allowed(user_id):
            return web.json_response({"error": "test_access_denied"}, status=403)
        if set(values) != {"init_data", "product"} or values.get("product") not in {"viewer_plus", "streamer_plus"}:
            return web.json_response({"error": "invalid_product"}, status=400)
        try:
            checkout = await service.create_checkout(
                user_id, "miniqa:" + secrets.token_hex(16), 600,
                plan=values["product"],
            )
        except PermissionError:
            return web.json_response({"error": "not_linked"}, status=403)
        return web.json_response({"order_id": checkout.order_id, "status": "pending"})

    async def test_confirm(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not allowed(user_id):
            return web.json_response({"error": "test_access_denied"}, status=403)
        if set(values) != {"init_data", "order_id"}:
            return web.json_response({"error": "invalid_request"}, status=400)
        order, error = await own_order(user_id, values)
        if error is not None:
            return error
        if order.status == "paid":
            return web.json_response({"status": "paid"})
        now = time.time()
        if order.status != "pending" or now >= order.checkout_expires_at:
            return web.json_response({"error": "order_closed"}, status=409)
        event = VerifiedPaymentEvent(
            provider="mock", event_id="miniqa:" + secrets.token_hex(12),
            order_id=order.order_id, payment_id="miniqa:" + secrets.token_hex(12),
            event_type="captured", units=1, currency="TEST",
        )
        body, headers = provider.sign_test_event(event, now=now)
        try:
            outcome = await service.handle_webhook(body, headers, now=now)
        except ValueError:
            return web.json_response({"error": "order_closed"}, status=409)
        return web.json_response({"status": outcome})

    async def test_cancel(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not allowed(user_id):
            return web.json_response({"error": "test_access_denied"}, status=403)
        if set(values) != {"init_data", "order_id"}:
            return web.json_response({"error": "invalid_request"}, status=400)
        order, error = await own_order(user_id, values)
        if error is not None:
            return error
        try:
            cancelled = await service.cancel_order(user_id, order.order_id)
        except ValueError:
            return web.json_response({"error": "order_closed"}, status=409)
        return web.json_response({"status": "cancelled" if cancelled else "expired"})

    async def test_refund(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not allowed(user_id):
            return web.json_response({"error": "test_access_denied"}, status=403)
        if set(values) != {"init_data", "order_id"}:
            return web.json_response({"error": "invalid_request"}, status=400)
        order, error = await own_order(user_id, values)
        if error is not None:
            return error
        payment = await db.get_billing_payment(order.order_id)
        if order.status != "paid" or payment is None or payment.status != "captured":
            return web.json_response({"error": "order_closed"}, status=409)
        now = time.time()
        event = VerifiedPaymentEvent(
            provider="mock", event_id="miniqa:" + secrets.token_hex(12),
            order_id=order.order_id, payment_id=payment.payment_id,
            event_type="refunded", units=1, currency="TEST",
        )
        body, headers = provider.sign_test_event(event, now=now)
        try:
            outcome = await service.handle_webhook(body, headers, now=now)
        except ValueError:
            return web.json_response({"error": "order_closed"}, status=409)
        return web.json_response({"status": outcome})

    app.router.add_post("/app/api/subscription/state", state)
    app.router.add_post("/app/api/subscription/test-checkout", test_checkout)
    app.router.add_post("/app/api/subscription/test-confirm", test_confirm)
    app.router.add_post("/app/api/subscription/test-cancel", test_cancel)
    app.router.add_post("/app/api/subscription/test-refund", test_refund)
