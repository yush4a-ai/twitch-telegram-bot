"""Публичный callback Platega: маршрут открыт только допущенному провайдеру.

Callback приходит из внешней сети, поэтому здесь фиксируется: без корректных
заголовков провайдера запись не создаётся, тело ограничено, а сам маршрут не
монтируется, пока владелец не разрешил банковский канал.
"""

import json
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider
from bot.payment_web import install_payment_routes
from bot.platega_provider import PlategaProvider
from bot.platega_transport import PlategaHttpTransport
from bot.plan_catalog import BillingRuntimePolicy
from tests.test_payment_lifecycle_v3 import PaymentFixture, TX
from tests.test_platega_provider import MERCHANT, SECRET
from tests.test_platega_transport import EXTERNAL_POLICY, FakeSession


def live_provider():
    return PlategaProvider(
        PlategaHttpTransport(session=FakeSession()),
        MERCHANT, SECRET,
        hosted_hosts=frozenset({"pay.platega.io"}),
        runtime_policy=EXTERNAL_POLICY,
        return_url="https://worker-production-cee5.up.railway.app/app",
        failed_url="https://worker-production-cee5.up.railway.app/app",
    )


class PlategaPublicCallbackTests(PaymentFixture):
    async def test_public_route_accepts_only_signed_notices_and_acks_after_write(self):
        service = BillingService(self.db, live_provider(), runtime_policy=EXTERNAL_POLICY)
        app = web.Application()
        self.assertTrue(install_payment_routes(app, service, public_callback=True))
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)

        body = json.dumps({"id": TX, "status": "CONFIRMED", "amount": 150,
                           "currency": "RUB", "paymentMethod": 2}).encode()
        unsigned = {"Content-Type": "application/json"}
        wrong = {"Content-Type": "application/json", "X-MerchantId": MERCHANT, "X-Secret": "wrong"}
        for headers in (unsigned, wrong):
            response = await client.post("/payments/platega/callback", data=body, headers=headers)
            self.assertEqual(response.status, 403)
        self.assertEqual(await self.count("billing_provider_inbox"), 0)

        signed = {"Content-Type": "application/json", "X-MerchantId": MERCHANT, "X-Secret": SECRET}
        response = await client.post("/payments/platega/callback", data=body, headers=signed)
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.count("billing_provider_inbox"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 0)

        response = await client.post("/payments/platega/callback", data=b"x" * 8193, headers=signed)
        self.assertEqual(response.status, 413)
        response = await client.post("/payments/platega/callback", data=body, headers=unsigned)
        self.assertEqual(response.status, 403)
        await service.close()

    async def test_public_route_stays_closed_without_an_admitted_provider(self):
        offline = BillingRuntimePolicy()
        closed_provider = PlategaProvider(
            PlategaHttpTransport(session=FakeSession()),
            MERCHANT, SECRET,
            hosted_hosts=frozenset({"pay.platega.io"}),
            runtime_policy=offline,
            return_url="https://worker-production-cee5.up.railway.app/app",
            failed_url="https://worker-production-cee5.up.railway.app/app",
        )
        for service in (BillingService(self.db, closed_provider, runtime_policy=offline),
                        BillingService(self.db, MockPaymentProvider(b"x" * 32))):
            with self.subTest(provider=service._provider.provider_id):
                app = web.Application()
                self.assertFalse(install_payment_routes(app, service, public_callback=True))
                client = TestClient(TestServer(app))
                await client.start_server()
                self.addAsyncCleanup(client.close)
                response = await client.post(
                    "/payments/platega/callback",
                    json={"id": TX, "status": "CONFIRMED"},
                    headers={"X-MerchantId": MERCHANT, "X-Secret": SECRET},
                )
                self.assertEqual(response.status, 404)
            await service.close()
        self.assertEqual(await self.count("billing_provider_inbox"), 0)

    async def test_local_contract_route_is_untouched_by_the_public_flag(self):
        app = web.Application()
        self.assertFalse(install_payment_routes(app, self.service, public_callback=False))
        self.assertTrue(install_payment_routes(app, self.service, local_contract_enabled=True))


if __name__ == "__main__":
    unittest.main()
