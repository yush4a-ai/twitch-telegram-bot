"""Signed subscription state and pinned, allowlisted non-monetary QA orders."""

from __future__ import annotations

import hashlib
import secrets
import time
import re

from aiohttp import web

from .billing import BillingService
from .billing_provider import MockPaymentProvider, VerifiedPaymentEvent
from .database import Database
from .mini_app_auth import verified_payload
from .viewer_trial import TrialAlreadyUsed, ViewerTrialService
from .plan_catalog import catalog_payload, PAYMENT_UNAVAILABLE_MESSAGE
from .entitlements import resolve_effective_viewer


def install_mini_app_billing_routes(
    app: web.Application, db: Database, bot_token: str, *,
    test_enabled: bool = False, test_user_ids: frozenset[int] = frozenset(),
) -> None:
    # This secret never leaves the server. No real payment provider is wired here.
    provider = MockPaymentProvider(hashlib.sha256(
        b"mini-app-mock-billing:" + bot_token.encode("utf-8")
    ).digest()) if test_enabled else None
    service = BillingService(db, provider) if provider is not None else None
    trial = ViewerTrialService(db)

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

    async def owned_streamer(user_id: int, now: float):
        # Subscription ownership survives unlink. Legacy unbound grants remain
        # visible only through their existing verified broadcaster binding.
        row = await (await db.conn.execute(
            "SELECT g.source,g.expires_at FROM entitlement_grants g "
            "WHERE g.subject_kind='streamer' AND g.plan='streamer_plus' "
            "AND g.revoked_at IS NULL AND g.starts_at<=? AND g.expires_at>? "
            "AND (g.beneficiary_telegram_user_id=? OR (g.beneficiary_telegram_user_id IS NULL "
            "AND EXISTS (SELECT 1 FROM streamer_identities i WHERE i.telegram_user_id=? "
            "AND i.broadcaster_id=g.subject_id))) ORDER BY g.expires_at DESC LIMIT 1",
            (now, now, user_id, user_id),
        )).fetchone()
        return row

    async def order_summary(order, user_id: int, now: float):
        monetary = order.provider in {"platega", "telegram_stars"}
        status = order.status
        if status == "pending" and now >= order.checkout_expires_at:
            status = "expired"
        elif status == "paid" and order.paid_at is not None and now >= order.paid_at + order.duration_seconds:
            status = "expired"
        viewer = await resolve_effective_viewer(db, user_id, now=now)
        return {
            "order_id": order.order_id, "product": order.plan, "method": order.method,
            "financial_status": order.financial_status if monetary else None,
            "status": status, "created_at": order.created_at,
            "checkout_expires_at": order.checkout_expires_at,
            "access_starts_at": order.access_starts_at,
            "access_expires_at": order.access_expires_at,
            "effective_access": {"viewer": viewer.active,
                                 "streamer": await owned_streamer(user_id, now) is not None},
            "monetary": monetary,
        }

    async def prepare(request: web.Request):
        _user_id, values, error = await read(request)
        if error is not None:
            return error
        if (set(values) != {"init_data", "product", "method", "request_key"}
            or not isinstance(values.get("product"), str)
            or values["product"] not in {"viewer_plus", "streamer_plus"}
            or not isinstance(values.get("method"), str)
            or values["method"] not in {"stars", "sbp", "bank_card"}
            or not isinstance(values.get("request_key"), str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", values["request_key"]) is None):
            return web.json_response({"error": "invalid_purchase_request"}, status=400)
        # First release is unconditionally OFF, independent of env credentials.
        # No checkout/provider/order creation or entitlement mutation happens here.
        return web.json_response({"state": "unavailable", "message": PAYMENT_UNAVAILABLE_MESSAGE,
                                  "payment_request_created": False}, status=503)

    async def purchase_state(request: web.Request):
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if (set(values) != {"init_data", "order_id"}
            or not isinstance(values.get("order_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", values["order_id"]) is None):
            return web.json_response({"error": "invalid_order"}, status=400)
        order = await db.get_billing_order(values["order_id"])
        if order is None or order.telegram_user_id != user_id:
            return web.json_response({"error": "order_denied"}, status=403)
        return web.json_response(await order_summary(order, user_id, time.time()))

    async def state(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data"}:
            return web.json_response({"error": "invalid_subscription_request"}, status=400)
        now = time.time()
        identity = await db.get_streamer_identity(user_id)
        viewer = await db.get_current_plus_grant(user_id, "viewer_plus", now=now)
        effective_viewer = await resolve_effective_viewer(db, user_id, now=now)
        trial_status = await trial.status(user_id, now=now)
        streamer = await owned_streamer(user_id, now)
        publishing = await db.get_current_plus_grant(user_id, "streamer_plus", now=now)
        orders = await db.list_billing_orders_for_user(user_id)

        return web.json_response({
            "viewer": {"active": effective_viewer.active,
                       "expires_at": effective_viewer.expires_at,
                       "source": viewer[0] if viewer else ("streamer_plus" if effective_viewer.active else None),
                       "sources": [{"grant_id": s.grant_id, "product_id": s.product_id,
                                    "starts_at": s.starts_at, "expires_at": s.expires_at}
                                   for s in effective_viewer.sources],
                       "test_trial_available": allowed(user_id) and not trial_status.used and not effective_viewer.active,
                       "test_trial_used": allowed(user_id) and trial_status.used,
                       "test_trial_active": allowed(user_id) and trial_status.active,
                       "test_trial_expires_at": trial_status.expires_at if allowed(user_id) else None},
            "streamer": {"linked": identity is not None,
                         "publishing_access": publishing is not None,
                         "twitch_login": identity[1] if identity else None,
                         "active": streamer is not None,
                         "expires_at": streamer[1] if streamer else None,
                         "source": streamer[0] if streamer else None},
            "history": [await order_summary(order, user_id, now) for order in orders],
            "test_checkout_available": allowed(user_id),
            "money_charged": False,
        })

    async def catalog(request: web.Request) -> web.Response:
        _user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data"}:
            return web.json_response({"error": "invalid_catalog_request"}, status=400)
        return web.json_response(catalog_payload())

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

    async def test_trial(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not allowed(user_id):
            return web.json_response({"error": "test_access_denied"}, status=403)
        if set(values) != {"init_data"}:
            return web.json_response({"error": "invalid_trial_request"}, status=400)
        try:
            started = await trial.start(user_id, now=time.time())
        except TrialAlreadyUsed:
            return web.json_response({"error": "trial_used"}, status=409)
        except PermissionError:
            return web.json_response({"error": "plus_active"}, status=409)
        return web.json_response({
            "started_now": started.started_now, "expires_at": started.expires_at,
            "money_charged": False,
        })

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
    app.router.add_post("/app/api/subscription/catalog", catalog)
    app.router.add_post("/app/api/purchase/prepare", prepare)
    app.router.add_post("/app/api/purchase/state", purchase_state)
    app.router.add_post("/app/api/subscription/test-checkout", test_checkout)
    app.router.add_post("/app/api/subscription/test-trial", test_trial)
    app.router.add_post("/app/api/subscription/test-confirm", test_confirm)
    app.router.add_post("/app/api/subscription/test-cancel", test_cancel)
    app.router.add_post("/app/api/subscription/test-refund", test_refund)
