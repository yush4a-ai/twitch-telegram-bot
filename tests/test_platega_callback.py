"""Durable callback ACK and closed live routes, using a local test application."""

import json
import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.payment_web import install_payment_routes
from tests.test_payment_lifecycle_v3 import PaymentFixture, TX
from tests.test_platega_provider import FakeTransport, provider, MERCHANT, SECRET
from bot.billing import BillingService
from tests.test_payment_lifecycle_v3 import POLICY


class PlategaCallbackTests(PaymentFixture):
    async def test_real_adapter_auth_schema_and_chunked_body_at_local_route(self):
        adapter = provider(FakeTransport())
        service = BillingService(self.db, adapter, runtime_policy=POLICY)
        app = web.Application()
        self.assertTrue(install_payment_routes(app, service, local_contract_enabled=True))
        client = TestClient(TestServer(app)); await client.start_server(); self.addAsyncCleanup(client.close)
        body = json.dumps({"id": TX, "status": "CONFIRMED", "amount": 150, "currency": "RUB", "paymentMethod": 2}).encode()
        for headers in ({"Content-Type": "application/json"}, {"Content-Type": "application/json", "X-MerchantId": MERCHANT, "X-Secret": "wrong"}):
            response = await client.post("/payments/platega/callback", data=body, headers=headers)
            self.assertEqual(response.status, 403)
            self.assertEqual(await self.count("billing_provider_inbox"), 0)
        headers = {"Content-Type": "application/json", "X-MerchantId": MERCHANT, "X-Secret": SECRET}
        async def chunks():
            yield body[:10]
            yield body[10:]
        response = await client.post("/payments/platega/callback", data=chunks(), headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.count("billing_provider_inbox"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 0)
        response = await client.post("/payments/platega/callback", data=b'x'*8193, headers=headers)
        self.assertEqual(response.status, 413)

    async def test_default_route_closed_and_local_ack_only_after_commit(self):
        app = web.Application()
        self.assertFalse(install_payment_routes(app, self.service))
        client = TestClient(TestServer(app)); await client.start_server(); self.addAsyncCleanup(client.close)
        response = await client.post("/payments/platega/callback", json={"id": TX, "status": "CONFIRMED"})
        self.assertEqual(response.status, 404)
        app2 = web.Application()
        self.assertTrue(install_payment_routes(app2, self.service, local_contract_enabled=True))
        local = TestClient(TestServer(app2)); await local.start_server(); self.addAsyncCleanup(local.close)
        response = await local.post("/payments/platega/callback", json={"id": TX, "status": "CONFIRMED"})
        self.assertEqual(response.status, 200)
        self.assertEqual(await self.count("billing_provider_inbox"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 0)
        await self.db.conn.execute("CREATE TEMP TRIGGER fail_inbox BEFORE INSERT ON billing_provider_inbox BEGIN SELECT RAISE(ABORT,'inbox failure'); END")
        response = await local.post("/payments/platega/callback", json={"id": TX, "status": "CANCELED"})
        self.assertEqual(response.status, 503)
        self.assertEqual(await self.count("billing_provider_inbox"), 1)

    async def test_full_inbox_keeps_old_facts_and_acknowledges_duplicate(self):
        body = json.dumps({"id": TX, "status": "CONFIRMED"}).encode()
        receipt = await self.service.accept_provider_notice(body, {}, now=100)
        await self.db.conn.executemany("INSERT INTO billing_provider_inbox(provider,event_key,transaction_id,raw_status,payload_digest,received_at,state) VALUES ('platega',?,?, 'CONFIRMED','fixture',100,'pending')",
            [("key"+str(index), "tx"+str(index)) for index in range(4095)])
        await self.db.conn.commit()
        duplicate = await self.service.accept_provider_notice(body, {}, now=101)
        self.assertTrue(duplicate.durable and duplicate.duplicate)
        with self.assertRaises(OverflowError):
            await self.service.accept_provider_notice(json.dumps({"id": TX, "status": "CANCELED"}).encode(), {}, now=102)
        self.assertEqual(await self.count("billing_provider_inbox"), 4096)
        with self.assertRaises(ValueError):
            await self.service.accept_provider_notice(body+b' ', {}, now=103)
        self.assertEqual(await self.count("billing_provider_inbox"), 4096)
