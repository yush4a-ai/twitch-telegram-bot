"""Покупка через банковский канал в мини-аппе: свой сервис и подтверждённое имя.

Банковский канал не должен подменять звёзды и наоборот, а имя покупателя для
платёжки берётся только из подписанных данных Telegram.
"""

import json
import os
import tempfile
import unittest
import uuid

import aiohttp
from aiohttp import web

from bot.billing import BillingService
from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from bot.platega_provider import PlategaProvider
from bot.platega_transport import PlategaHttpTransport
from bot.plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp
from tests.test_payment_lifecycle_v3 import PERIOD, product
from tests.test_platega_provider import MERCHANT, SECRET, TRANSACTION
from tests.test_platega_transport import EXTERNAL_POLICY, FakeResponse, FakeSession

TERMS = "fixture-terms-v1"


def created_body():
    return {
        "transactionId": TRANSACTION,
        "redirect": "https://pay.platega.io/checkout?id=" + TRANSACTION,
        "status": "PENDING",
        "expiresIn": "00:15:00",
    }


class MiniAppBankPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(os.path.join(directory.name, "bank.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.transport_session = FakeSession(
            FakeResponse(200, json.dumps(created_body()).encode()))
        provider = PlategaProvider(
            PlategaHttpTransport(session=self.transport_session), MERCHANT, SECRET,
            hosted_hosts=frozenset({"pay.platega.io"}),
            runtime_policy=EXTERNAL_POLICY,
            return_url="https://worker-production-cee5.up.railway.app/app",
            failed_url="https://worker-production-cee5.up.railway.app/app",
        )
        self.bank = BillingService(
            self.db, provider, runtime_policy=EXTERNAL_POLICY,
            access_policy=PERIOD, catalog=product, terms_version=TERMS,
        )
        self.addAsyncCleanup(self.bank.close)
        self.base = await self.server(self.bank)

    async def server(self, bank_service):
        app = web.Application()
        install_mini_app_routes(app, self.db, BOT_TOKEN,
                                external_billing_service=bank_service)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    async def call(self, path, *, actor=101, base=None, **fields):
        async with self.session.post(
            (base or self.base) + "/app/api/" + path,
            json={"init_data": signed_webapp(actor), **fields},
        ) as response:
            return response.status, await response.json()

    async def orders(self):
        return (await (await self.db.conn.execute(
            "SELECT count(*) FROM billing_orders")).fetchone())[0]

    async def test_bank_purchase_uses_its_own_service_and_verified_name(self):
        status, result = await self.call(
            "purchase/prepare", product="viewer_plus", method="sbp",
            request_key=uuid.uuid4().hex,
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            (result["state"], result["payment_url"]),
            ("pending", "https://pay.platega.io/checkout?id=" + TRANSACTION),
        )
        payload = self.transport_session.calls[0]["json"]
        self.assertEqual(payload["paymentMethod"], 2)
        self.assertEqual(payload["metadata"], {"userId": "101", "userName": "Owner"})
        self.assertEqual(await self.orders(), 1)

    async def test_bank_purchase_is_unavailable_without_the_bank_service(self):
        base = await self.server(None)
        status, result = await self.call(
            "purchase/prepare", base=base, product="viewer_plus",
            method="bank_card", request_key=uuid.uuid4().hex,
        )

        self.assertEqual(status, 503)
        self.assertEqual(result, {"state": "unavailable",
                                  "message": PAYMENT_UNAVAILABLE_MESSAGE,
                                  "payment_request_created": False})
        self.assertEqual(await self.orders(), 0)
        self.assertEqual(self.transport_session.calls, [])

    async def test_stars_method_never_uses_the_bank_service(self):
        status, _result = await self.call(
            "purchase/prepare", product="viewer_plus", method="stars",
            request_key=uuid.uuid4().hex,
        )

        self.assertEqual(status, 503)
        self.assertEqual(self.transport_session.calls, [])
        self.assertEqual(await self.orders(), 0)


if __name__ == "__main__":
    unittest.main()
