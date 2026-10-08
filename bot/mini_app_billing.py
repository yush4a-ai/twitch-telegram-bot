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
from .mini_app_limits import RequestBudget
from .mini_app_auth import verified_identity_payload, verified_payload
from .viewer_trial import TrialAlreadyUsed, ViewerTrialService
from .plan_catalog import catalog_payload, BillingRuntimePolicy, PAYMENT_UNAVAILABLE_MESSAGE
from .subscription_state import SubscriptionService

# Подготовка покупки и проверка статуса — дорогие пути: подпись, обращения к
# базе, у звёзд ещё и внешний вызов. Ограничение на пользователя не даёт одному
# человеку занять сервис, общий предохранитель защищает остальных.
_PURCHASE_BUDGET = RequestBudget(per_user=120, window_seconds=60.0, global_limit=1200)
_STATUS_BUDGET = RequestBudget(per_user=240, window_seconds=60.0, global_limit=2400)


def install_mini_app_billing_routes(
    app: web.Application, db: Database, bot_token: str, *,
    test_enabled: bool = False, test_user_ids: frozenset[int] = frozenset(),
    live_service: BillingService | None = None,
    external_service: BillingService | None = None,
) -> None:
    # This secret never leaves the server. No real payment provider is wired here.
    provider = MockPaymentProvider(hashlib.sha256(
        b"mini-app-mock-billing:" + bot_token.encode("utf-8")
    ).digest()) if test_enabled else None
    service = BillingService(db, provider) if provider is not None else None
    # Публичная покупка идёт через живой сервис (Telegram Stars). Он передаётся
    # снаружи только когда денежная политика включена владельцем; иначе покупка
    # честно отвечает «недоступно», а QA-маршруты продолжают работать на mock.
    # Банковский канал обслуживает отдельный сервис: у него свой провайдер.
    public_service = live_service
    bank_service = external_service
    trial = ViewerTrialService(db)
    subscriptions = SubscriptionService(db,test_user_ids=test_user_ids if test_enabled else frozenset())

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

    async def prepare(request: web.Request):
        identity, values, status = await verified_identity_payload(request, bot_token)
        if status != 200 or identity is None:
            return web.json_response({"error": "unauthorized"}, status=status or 401)
        user_id = identity.id
        if not _PURCHASE_BUDGET.admit(user_id):
            return web.json_response({"error": "rate_limited"}, status=429)
        if (set(values) != {"init_data", "product", "method", "request_key"}
            or not isinstance(values.get("product"), str)
            or values["product"] not in {"viewer_plus", "streamer_plus"}
            or not isinstance(values.get("method"), str)
            or values["method"] not in {"stars", "sbp", "bank_card"}
            or not isinstance(values.get("request_key"), str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", values["request_key"]) is None):
            return web.json_response({"error": "invalid_purchase_request"}, status=400)
        # Звёзды и банковский канал — разные провайдеры и разные сервисы.
        chosen = public_service if values["method"] == "stars" else (bank_service or public_service)
        if not chosen:
            # Денежная политика выключена: покупка недоступна, заказ не создаётся.
            return web.json_response(
                BillingService.public_purchase(values["product"], values["method"]), status=503,
            )
        # Приложение разрешает только выбранный провайдер: чужой способ ответит отказом.
        if values["method"] != "stars" and chosen is not bank_service:
            return web.json_response(
                BillingService.public_purchase(values["product"], values["method"]), status=503,
            )
        try:
            result = await chosen.prepare_payment(
                user_id, values["product"], values["method"], values["request_key"],
                now=time.time(),
                # Мини-апп открывает оплату окном Telegram, поэтому ему нужна
                # ссылка на счёт, а не сообщение в чате с ботом.
                prefer_link=True,
                # Имя покупателя приходит из подписанных данных Telegram: внешняя
                # касса требует его в metadata, выдумывать нельзя.
                buyer_name=identity.display_name or identity.username,
            )
        except PermissionError:
            return web.json_response({"error": "twitch_required"}, status=403)
        except ValueError:
            return web.json_response({"error": "invalid_purchase_request"}, status=400)
        if result.state == "pending":
            return web.json_response({
                "state": "pending", "order_id": result.order_id,
                "payment_url": result.hosted_url,
            })
        if result.state == "unavailable":
            payload = BillingService.public_purchase(values["product"], values["method"])
            payload["reason_code"] = result.reason_code
            return web.json_response(payload, status=503)
        return web.json_response({
            "state": result.state, "order_id": result.order_id,
            "payment_url": None, "reason_code": result.reason_code,
        }, status=503)

    async def purchase_state(request: web.Request):
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if not _STATUS_BUDGET.admit(user_id):
            return web.json_response({"error": "rate_limited"}, status=429)
        if (set(values) != {"init_data", "order_id"}
            or not isinstance(values.get("order_id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", values["order_id"]) is None):
            return web.json_response({"error": "invalid_order"}, status=400)
        order = await db.get_billing_order(values["order_id"])
        if order is None or order.telegram_user_id != user_id:
            return web.json_response({"error": "order_denied"}, status=403)
        return web.json_response(await subscriptions.order_summary(order, user_id, time.time()))

    async def state(request: web.Request) -> web.Response:
        user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data"}:
            return web.json_response({"error": "invalid_subscription_request"}, status=400)
        return web.json_response(await subscriptions.state(user_id))

    async def catalog(request: web.Request) -> web.Response:
        _user_id, values, error = await read(request)
        if error is not None:
            return error
        if set(values) != {"init_data"}:
            return web.json_response({"error": "invalid_catalog_request"}, status=400)
        # Готовность способов считается по действующей политике: без этого каталог
        # всегда показывал бы «оплата недоступна», даже когда она включена.
        active = public_service or bank_service
        policy = (
            active.runtime_policy if active is not None else BillingRuntimePolicy()
        )
        return web.json_response(catalog_payload(policy))

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
