"""Signed purchase UX is read-only while money is OFF; one disposable ledger."""

import os
import tempfile
import time
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from bot.plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class MiniAppPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(os.path.join(directory.name, "purchase.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=1)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.base = await self.server()

    async def server(self):
        app = web.Application()
        install_mini_app_routes(app, self.db, BOT_TOKEN, billing_test_enabled=True,
                                billing_test_user_ids=frozenset({101}))
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    async def call(self, path, *, actor=101, base=None, **fields):
        async with self.session.post((base or self.base) + "/app/api/" + path,
                                     json={"init_data": signed_webapp(actor), **fields}) as response:
            return response.status, await response.json()

    async def counts(self):
        return tuple([(await (await self.db.conn.execute("SELECT count(*) FROM " + table)).fetchone())[0]
                      for table in ("billing_orders", "billing_payments", "entitlement_grants", "billing_payment_attempts")])

    async def test_three_methods_same_products_server_prices_no_side_effects_after_restart(self):
        status, catalog = await self.call("subscription/catalog")
        self.assertEqual(status, 200)
        self.assertEqual([p["rub"]["amount_minor"] for p in catalog["products"]], [15000, 30000])
        self.assertEqual({m["id"] for m in catalog["methods"]}, {"stars", "sbp", "bank_card"})
        before = await self.counts()
        with patch.dict(os.environ, {"PLATEGA_MERCHANT_ID": "fake", "PLATEGA_SECRET": "fake",
                                     "BILLING_ENABLED": "true"}), patch("bot.billing.BillingService.prepare_payment", new_callable=AsyncMock) as money:
            for base in (self.base, await self.server()):
                for product in ("viewer_plus", "streamer_plus"):
                    for method in ("stars", "sbp", "bank_card"):
                        with self.subTest(product=product, method=method, restart=base != self.base):
                            status, result = await self.call("purchase/prepare", base=base,
                                                            product=product, method=method, request_key=uuid.uuid4().hex)
                            self.assertEqual(status, 503)
                            self.assertEqual(result, {"state": "unavailable", "message": PAYMENT_UNAVAILABLE_MESSAGE,
                                                      "payment_request_created": False})
            money.assert_not_awaited()
        self.assertEqual(await self.counts(), before)

    async def test_auth_and_exact_fields_are_checked_before_unavailable(self):
        for path in ("purchase/prepare", "purchase/state"):
            async with self.session.post(self.base + "/app/api/" + path, json={"paid": True}) as response:
                self.assertEqual(response.status, 401)
            async with self.session.post(self.base + "/app/api/" + path, json={"init_data": "fake"}) as response:
                self.assertEqual(response.status, 403)
        good = {"product": "viewer_plus", "method": "sbp", "request_key": "valid-request"}
        for fields in ({**good, "amount": 1}, {**good, "user_id": 202}, {**good, "paid": True},
                       {**good, "mode": "streamer"}, {**good, "product": "stars_plus"},
                       {**good, "product": []}, {**good, "method": {}},
                       {**good, "request_key": "x" * 129}, {**good, "request_key": True},
                       {**good, "request_key": "<script>"}, {"product": "viewer_plus"}):
            with self.subTest(fields=fields):
                self.assertEqual((await self.call("purchase/prepare", **fields))[0], 400)
        self.assertEqual(await self.counts(), (0, 0, 0, 0))

    async def seed_order(self, *, status="pending", expires=None):
        now = time.time() - 10
        order = await self.db.create_billing_order(uuid.uuid4().hex, uuid.uuid4().hex, 101,
                                                  600, now=now, plan="viewer_plus")
        await self.db.conn.execute("UPDATE billing_orders SET provider='platega',method='sbp',currency='RUB',"
                                   "units=15000,financial_status=?,access_starts_at=?,access_expires_at=?,"
                                   "checkout_expires_at=?,checkout_url='https://pay.platega.io/fixture' WHERE order_id=?",
                                   (status, now - 10 if status == "confirmed" else None,
                                    expires, now + 300, order.order_id))
        await self.db.conn.commit()
        return order.order_id

    async def test_pending_checkout_expiry_preserves_financial_fact(self):
        order_id = await self.seed_order()
        await self.db.conn.execute('UPDATE billing_orders SET checkout_expires_at=? WHERE order_id=?', (time.time() - 1, order_id))
        await self.db.conn.commit()
        status, result = await self.call('purchase/state', order_id=order_id)
        self.assertEqual(status, 200)
        self.assertEqual((result['financial_status'], result['status']), ('pending', 'expired'))
        self.assertEqual((await self.db.get_billing_order(order_id)).financial_status, 'pending')

    async def test_state_own_financial_and_access_are_independent_no_sensitive_fields(self):
        for financial, expires in (("pending", None), ("confirmed", time.time() + 500),
                                    ("confirmed", time.time() - 5), ("refunded", time.time() + 500), ("canceled", None)):
            with self.subTest(financial=financial, expires=expires):
                order_id = await self.seed_order(status=financial, expires=expires)
                status, result = await self.call("purchase/state", order_id=order_id)
                self.assertEqual(status, 200)
                self.assertEqual(result["financial_status"], financial)
                self.assertEqual(result["product"], "viewer_plus")
                self.assertEqual(result["method"], "sbp")
                self.assertEqual(result["access_expires_at"], expires)
                self.assertFalse(result["effective_access"]["viewer"])
                self.assertEqual(set(result), {"order_id", "product", "method", "financial_status", "status",
                                               "checkout_expires_at", "access_starts_at", "access_expires_at",
                                               "effective_access", "monetary", "created_at"})
                self.assertEqual((await self.call("purchase/state", actor=202, order_id=order_id))[0], 403)
                self.assertEqual((await self.call("subscription/test-confirm", order_id=order_id))[0], 403)
        self.assertEqual((await self.call("purchase/state", order_id=uuid.uuid4().hex))[0], 403)
        self.assertEqual((await self.call("purchase/state", order_id="bad"))[0], 400)
        self.assertEqual((await self.call("purchase/state", order_id=order_id, paid=True))[0], 400)

    async def test_frozen_streamer_subscription_survives_unlink_without_granting_new_owner(self):
        now = time.time()
        await self.db.issue_test_streamer_plus("11", "frozen-subscription", starts_at=now - 10,
                                              expires_at=now + 600, issued_by=999, now=now,
                                              beneficiary_telegram_user_id=101)
        await self.db.conn.execute("DELETE FROM streamer_identities WHERE telegram_user_id=101")
        await self.db.conn.commit()
        await self.db.link_streamer_identity(202, "11", "alpha", verified_at=now)
        _, own = await self.call("subscription/state")
        self.assertTrue(own["streamer"]["active"])
        self.assertFalse(own["streamer"]["linked"])
        self.assertFalse(own["streamer"]["publishing_access"])
        self.assertTrue(own["viewer"]["active"])
        _, other = await self.call("subscription/state", actor=202)
        self.assertFalse(other["streamer"]["active"])
        self.assertFalse(other["viewer"]["active"])


class PurchaseMenuTests(unittest.TestCase):
    def test_plus_moves_to_more_and_home_keeps_four_actions(self):
        from bot.handlers.streams import _main_menu_keyboard
        from bot.telegram_ui import more_keyboard
        private = _main_menu_keyboard("private", viewer_url="https://staging.example.test/app")
        buttons = [b for row in private.inline_keyboard for b in row]
        self.assertEqual(len(buttons),4)
        self.assertEqual(buttons[0].web_app.url,"https://staging.example.test/app")
        self.assertFalse(any(b.text=="Возможности Plus" for b in buttons))
        plus = next(b for row in more_keyboard().inline_keyboard for b in row if b.text=="⭐ Plus и подписка")
        self.assertEqual(plus.callback_data,"menu:plus")
        for kind in ("group","supergroup","channel"):
            menu = _main_menu_keyboard(kind, viewer_url="https://staging.example.test/app")
            self.assertFalse(any(b.web_app for row in menu.inline_keyboard for b in row))
