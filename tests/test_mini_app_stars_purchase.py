"""Сквозная покупка подписки через Telegram Stars в Mini App.

Тест идёт по живому маршруту ``POST /app/api/purchase/prepare`` с реальным
``BillingService`` и ``TelegramStarsProvider``, но с локальным отправителем без
сети: ни одного запроса к Telegram, ни одного настоящего платежа и ни одной
настоящей звезды. Выключенная денежная политика должна честно отклонять покупку.
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import aiohttp
from aiogram.types import Chat, Message, RefundedPayment, SuccessfulPayment, User
from aiohttp import web

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy
from bot.database import Database
from bot.mini_app_billing import install_mini_app_billing_routes
from bot.plan_catalog import (
    PAYMENT_UNAVAILABLE_MESSAGE,
    PLUS_PERIOD_RULE,
    PLUS_PERIOD_VERSION,
    PLUS_TERMS_VERSION,
    VIEWER_PLUS_XTR,
    BillingRuntimePolicy,
)
from bot.stars_provider import TelegramStarsProvider
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


BUYER_ID = 101
CHARGE_ID = "stars_charge_purchase_1"
# Утверждённая политика: деньги открываются только этим полным набором флагов.
STARS_POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_invoice=True,
    period_approved=True, refund_policy_approved=True, allow_public_stars=True,
)
# Срок доступа должен совпадать с замороженным сроком каталога.
ACCESS_PERIOD = AccessPeriodPolicy(PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION)


class FakeStarsSender:
    """Локальный отправитель счёта: помнит вызовы и не ходит в сеть."""

    network_free = True

    def __init__(self):
        self.invoice_calls = []

    async def create_invoice_link(self, **fields):
        self.invoice_calls.append(fields)
        return "https://t.me/invoice/test-" + str(len(self.invoice_calls))


def stars_message(payload, *, refund=False, charge=CHARGE_ID, buyer=BUYER_ID):
    values = dict(currency="XTR", total_amount=VIEWER_PLUS_XTR, invoice_payload=payload,
                  telegram_payment_charge_id=charge, provider_payment_charge_id="")
    payment = RefundedPayment(**values) if refund else SuccessfulPayment(**values)
    return Message(message_id=90, date=datetime.now(timezone.utc),
                   chat=Chat(id=buyer, type="private"),
                   from_user=User(id=buyer, is_bot=False, first_name="Покупатель"),
                   **{"refunded_payment" if refund else "successful_payment": payment})


class BotLikeStarsSender(FakeStarsSender):
    """Отправитель как настоящий бот: умеет и сообщение, и ссылку.

    Именно такого двойника не хватало: старый умел только ссылку, поэтому
    регрессия «мини-апп получает счёт-сообщение и остаётся без payment_url»
    проходила незамеченной.
    """

    def __init__(self):
        super().__init__()
        self.invoice_messages = []

    async def send_invoice(self, **fields):
        self.invoice_messages.append(fields)
        return SimpleNamespace(message_id=70)


class MiniAppStarsPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "stars-purchase.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = FakeStarsSender()
        self.provider = TelegramStarsProvider(self.sender, STARS_POLICY)
        self.service = self.make_service()
        self.addAsyncCleanup(self.service.close)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)

    def make_service(self, *, policy=STARS_POLICY, provider=None):
        return BillingService(
            self.db, provider or self.provider, runtime_policy=policy,
            access_policy=ACCESS_PERIOD, terms_version=PLUS_TERMS_VERSION,
            merchant_actor_ids=frozenset({999}),
        )

    async def server(self, *, service="live"):
        app = web.Application()
        install_mini_app_billing_routes(
            app, self.db, BOT_TOKEN,
            live_service=self.service if service == "live" else service,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    async def prepare(self, base, *, actor=BUYER_ID, product="viewer_plus",
                      method="stars", request_key="stars-request-1"):
        body = {"init_data": signed_webapp(actor), "product": product,
                "method": method, "request_key": request_key}
        async with self.session.post(base + "/app/api/purchase/prepare", json=body) as response:
            return response.status, await response.json()

    async def count(self, sql, params=()):
        cursor = await self.db.conn.execute(sql, params)
        return (await cursor.fetchone())[0]

    async def grants(self):
        return await self.count(
            "SELECT count(*) FROM entitlement_grants WHERE subject_kind='viewer' AND subject_id=?",
            (str(BUYER_ID),),
        )

    async def paid_order(self, base, *, request_key="stars-grant-1"):
        """Готовит заказ через маршрут и возвращает его и payload счёта."""
        status, body = await self.prepare(base, request_key=request_key)
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "pending")
        order = await self.db.get_billing_order(body["order_id"])
        attempt_id = (await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (order.order_id,),
        )).fetchone())[0]
        return order, self.provider.invoice_payload(order.order_id, attempt_id)

    async def test_mini_app_route_takes_the_link_and_never_sends_a_chat_invoice(self):
        """Мини-апп должен получать ссылку, а не сообщение со счётом в чате."""
        sender = BotLikeStarsSender()
        provider = TelegramStarsProvider(sender, STARS_POLICY)
        service = self.make_service(provider=provider)
        base = await self.server(service=service)

        status, body = await self.prepare(base, request_key="app-link-1")

        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "pending")
        self.assertIsInstance(body["payment_url"], str)
        self.assertTrue(body["payment_url"].startswith("https://"))
        self.assertEqual(len(sender.invoice_calls), 1)
        self.assertEqual(sender.invoice_messages, [])

    async def test_catalog_reports_stars_ready_when_policy_is_on(self):
        """Каталог считает готовность по действующей политике.

        Раньше он вызывал каталог без политики, поэтому всегда показывал
        «оплата недоступна» даже при включённых деньгах.
        """
        base = await self.server()
        async with self.session.post(
            base + "/app/api/subscription/catalog", json={"init_data": signed_webapp(BUYER_ID)}
        ) as response:
            self.assertEqual(response.status, 200)
            catalog = await response.json()
        for product in catalog["products"]:
            readiness = product["method_readiness"]
            self.assertTrue(readiness["stars"]["enabled"], product["product_id"])
            self.assertIsNone(readiness["stars"]["reason_code"])
            # СБП и карта остаются выключенными: их открывает отдельный флаг провайдера.
            self.assertFalse(readiness["sbp"]["enabled"])
            self.assertFalse(readiness["bank_card"]["enabled"])
            self.assertEqual(product["xtr"]["currency"], "XTR")

    async def test_catalog_hides_stars_when_the_owner_has_not_opened_them(self):
        """Без разрешения владельца витрина не обещает оплату звёздами."""
        locked = self.make_service(policy=BillingRuntimePolicy(
            mode="sandbox", target_verified=True, allow_invoice=True,
            period_approved=True, refund_policy_approved=True,
        ))
        base = await self.server(service=locked)
        async with self.session.post(
            base + "/app/api/subscription/catalog", json={"init_data": signed_webapp(BUYER_ID)}
        ) as response:
            catalog = await response.json()
        for product in catalog["products"]:
            readiness = product["method_readiness"]["stars"]
            self.assertFalse(readiness["enabled"], product["product_id"])
            self.assertEqual(readiness["reason_code"], "stars_unavailable")

    async def test_catalog_without_live_service_reports_unavailable(self):
        base = await self.server(service=None)
        async with self.session.post(
            base + "/app/api/subscription/catalog", json={"init_data": signed_webapp(BUYER_ID)}
        ) as response:
            self.assertEqual(response.status, 200)
            catalog = await response.json()
        for product in catalog["products"]:
            for state in product["method_readiness"].values():
                self.assertFalse(state["enabled"])
                self.assertEqual(state["reason_code"], "payments_unavailable")

    async def test_successful_preparation_returns_pending_order_and_invoice_link(self):
        base = await self.server()
        status, body = await self.prepare(base, request_key="stars-buy-1")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "pending")
        self.assertTrue(body["order_id"])
        self.assertIsInstance(body["payment_url"], str)
        self.assertTrue(body["payment_url"].startswith("https://"))
        # Ровно один счёт, одна звезда-цена и никакого провайдерского токена.
        self.assertEqual(len(self.sender.invoice_calls), 1)
        invoice = self.sender.invoice_calls[0]
        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(invoice["provider_token"], "")
        self.assertEqual([price.amount for price in invoice["prices"]], [VIEWER_PLUS_XTR])
        self.assertNotIn("subscription_period", invoice)
        self.assertEqual(await self.count("SELECT count(*) FROM billing_orders"), 1)
        order = await self.db.get_billing_order(body["order_id"])
        self.assertEqual((order.provider, order.method, order.units, order.currency),
                         ("telegram_stars", "stars", VIEWER_PLUS_XTR, "XTR"))
        self.assertEqual(order.checkout_url, body["payment_url"])

    async def test_repeated_request_key_reuses_the_same_order_without_a_second_invoice(self):
        base = await self.server()
        first_status, first = await self.prepare(base, request_key="stars-same-key")
        second_status, second = await self.prepare(base, request_key="stars-same-key")
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(second["order_id"], first["order_id"])
        self.assertEqual(second["state"], "pending")
        self.assertEqual(second["payment_url"], first["payment_url"])
        self.assertEqual(len(self.sender.invoice_calls), 1)
        self.assertEqual(await self.count("SELECT count(*) FROM billing_orders"), 1)

    async def test_confirmed_payment_grants_viewer_plus_exactly_once(self):
        base = await self.server()
        order, payload = await self.paid_order(base)
        now = time.time()
        evidence = self.provider.successful_payment_to_evidence(stars_message(payload), order, now=now)
        applied = await self.service.apply_payment_evidence(evidence, now=now)
        self.assertEqual(applied.state, "applied")
        self.assertTrue(await self.db.has_viewer_plus(BUYER_ID, now=now))
        self.assertFalse(await self.db.has_viewer_plus(202, now=now))
        self.assertEqual(await self.grants(), 1)
        # То же подтверждённое событие не выдаёт второй доступ.
        replay = await self.service.apply_payment_evidence(evidence, now=now + 10)
        self.assertEqual(replay.state, "already_applied")
        self.assertTrue(await self.db.has_viewer_plus(BUYER_ID, now=now + 10))
        self.assertEqual(await self.grants(), 1)

    async def test_refund_revokes_viewer_plus_access(self):
        base = await self.server()
        order, payload = await self.paid_order(base)
        now = time.time()
        evidence = self.provider.successful_payment_to_evidence(stars_message(payload), order, now=now)
        self.assertEqual((await self.service.apply_payment_evidence(evidence, now=now)).state, "applied")
        refreshed = await self.db.get_billing_order(order.order_id)
        refund = self.provider.refunded_payment_to_evidence(
            stars_message(payload, refund=True), refreshed, expected_charge=CHARGE_ID, now=now + 5,
        )
        outcome = await self.service.apply_payment_evidence(refund, now=now + 5)
        self.assertEqual(outcome.state, "applied")
        self.assertFalse(await self.db.has_viewer_plus(BUYER_ID, now=now + 6))
        self.assertEqual(await self.count(
            "SELECT count(*) FROM entitlement_grants WHERE subject_kind='viewer' AND subject_id=? "
            "AND revoked_at IS NULL", (str(BUYER_ID),),
        ), 0)
        # Строка гранта остаётся историей, но доступ снят.
        self.assertEqual(await self.grants(), 1)

    async def test_disabled_policy_refuses_purchase_and_creates_no_order(self):
        disabled_service = self.make_service(policy=BillingRuntimePolicy())
        self.addAsyncCleanup(disabled_service.close)
        servers = {
            "no_live_service": await self.server(service=None),
            "default_policy": await self.server(service=disabled_service),
        }
        for name, base in servers.items():
            with self.subTest(server=name):
                status, body = await self.prepare(base, request_key="stars-disabled")
                self.assertEqual(status, 503)
                self.assertEqual(body["state"], "unavailable")
                self.assertEqual(body["message"], PAYMENT_UNAVAILABLE_MESSAGE)
                self.assertIs(body["payment_request_created"], False)
        self.assertEqual(self.sender.invoice_calls, [])
        self.assertEqual(await self.count("SELECT count(*) FROM billing_orders"), 0)
        self.assertEqual(await self.count("SELECT count(*) FROM billing_payment_attempts"), 0)

    async def test_service_with_default_policy_is_unavailable_despite_provider_and_env(self):
        disabled_service = self.make_service(policy=BillingRuntimePolicy())
        self.addAsyncCleanup(disabled_service.close)
        flags = {"BILLING_MODE": "sandbox", "BILLING_TARGET_VERIFIED": "1",
                 "BILLING_ALLOW_INVOICE": "1", "BILLING_PERIOD_APPROVED": "1",
                 "BILLING_REFUND_APPROVED": "1", "STARS_ENABLED": "1"}
        for label, environment in (("plain", {}), ("env_flags", flags)):
            with self.subTest(environment=label), patch.dict(os.environ, environment):
                result = await disabled_service.prepare_payment(
                    BUYER_ID, "viewer_plus", "stars", "stars-service-off", now=time.time(),
                )
                self.assertEqual(result.state, "unavailable")
                self.assertEqual(result.reason_code, "payments_unavailable")
                self.assertIsNone(result.order_id)
                self.assertIsNone(result.hosted_url)
        self.assertEqual(self.sender.invoice_calls, [])
        self.assertEqual(await self.count("SELECT count(*) FROM billing_orders"), 0)


if __name__ == "__main__":
    unittest.main()
