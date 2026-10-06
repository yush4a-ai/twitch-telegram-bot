"""C10: повтор одной и той же подписи на маршруте покупки не открывает второй заказ.

Проверка идёт по живому маршруту ``/app/api/purchase/prepare`` с настоящим
``BillingService`` и локальным отправителем счёта: ни одного сетевого вызова.
"""

import os
import tempfile
import unittest

import aiohttp
from aiohttp import web

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy
from bot.database import Database
from bot.mini_app_billing import install_mini_app_billing_routes
from bot.plan_catalog import (
    PLUS_PERIOD_RULE,
    PLUS_PERIOD_VERSION,
    PLUS_TERMS_VERSION,
    BillingRuntimePolicy,
)
from bot.stars_provider import TelegramStarsProvider
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


BUYER = 101
# Полный набор флагов: касса считается открытой только при нём.
STARS_POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_invoice=True,
    period_approved=True, refund_policy_approved=True, allow_public_stars=True,
)
ACCESS_PERIOD = AccessPeriodPolicy(PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION)


class LocalInvoiceSender:
    """Локальный отправитель счёта: помнит вызовы и не ходит в сеть."""

    network_free = True

    def __init__(self):
        self.calls = []

    async def create_invoice_link(self, **fields):
        self.calls.append(fields)
        return "https://t.me/invoice/p3-" + str(len(self.calls))


class PurchaseRouteSignatureReplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "replay.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = LocalInvoiceSender()
        self.provider = TelegramStarsProvider(self.sender, STARS_POLICY)
        self.service = BillingService(
            self.db, self.provider, runtime_policy=STARS_POLICY,
            access_policy=ACCESS_PERIOD, terms_version=PLUS_TERMS_VERSION,
            merchant_actor_ids=frozenset({999}),
        )
        self.addAsyncCleanup(self.service.close)
        app = web.Application()
        install_mini_app_billing_routes(app, self.db, BOT_TOKEN, live_service=self.service)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.base = f"http://127.0.0.1:{runner.addresses[0][1]}"

    async def prepare(self, init_data, request_key):
        async with self.session.post(self.base + "/app/api/purchase/prepare", json={
            "init_data": init_data, "product": "viewer_plus", "method": "stars",
            "request_key": request_key,
        }) as response:
            return response.status, await response.json()

    async def count(self, table):
        cursor = await self.db.conn.execute("SELECT count(*) FROM " + table)
        return (await cursor.fetchone())[0]

    async def test_replayed_signature_reuses_one_order_and_grants_nothing(self):
        # Одна и та же строка подписи в двух запросах: как при перехвате initData.
        init_data = signed_webapp(BUYER)
        first_status, first = await self.prepare(init_data, "replay-key-1")
        second_status, second = await self.prepare(init_data, "replay-key-1")
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(first["order_id"], second["order_id"])
        self.assertEqual(first["payment_url"], second["payment_url"])
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(await self.count("billing_orders"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 0)

    async def test_replayed_signature_cannot_open_a_second_parallel_order(self):
        init_data = signed_webapp(BUYER)
        self.assertEqual((await self.prepare(init_data, "replay-key-a"))[0], 200)
        status, body = await self.prepare(init_data, "replay-key-b")
        self.assertEqual(status, 503)
        self.assertEqual(body["reason_code"], "payment_in_progress")
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(await self.count("billing_orders"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 0)


if __name__ == "__main__":
    unittest.main()
