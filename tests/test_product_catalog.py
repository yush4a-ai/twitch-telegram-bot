"""Server-owned offers remain readable while every monetary method is unavailable."""

import unittest
from dataclasses import FrozenInstanceError

from aiohttp.test_utils import TestClient, TestServer

from bot import plan_catalog
from scripts.mini_app_browser_fixture import build_fixture
from tests.test_admin_telegram_auth import signed_webapp


class ProductCatalogTests(unittest.TestCase):
    def test_catalog_has_two_products_exact_prices_inheritance_and_unapproved_period(self):
        get_product = getattr(plan_catalog, "get_product", None)
        self.assertTrue(callable(get_product), "server products are required")
        viewer, streamer = get_product("viewer_plus"), get_product("streamer_plus")
        self.assertEqual(viewer.rub.amount_minor, 15000)
        self.assertEqual(streamer.rub.amount_minor, 30000)
        self.assertEqual(streamer.includes, ("viewer_plus",))
        self.assertEqual({p.product_id for p in plan_catalog.list_products()}, {"viewer_plus", "streamer_plus"})
        for product in (viewer, streamer):
            self.assertEqual(product.period_code, "one_month")
            self.assertEqual(product.period_rule, "unapproved")
            self.assertIsNone(product.period_rule_version)
            self.assertFalse(product.auto_renew)
            self.assertIsNone(product.xtr)
            with self.assertRaises(FrozenInstanceError):
                product.auto_renew = True
        for invalid in ("stars_plus", "free", "both", None):
            with self.subTest(product=invalid), self.assertRaises(ValueError):
                get_product(invalid)

    def test_offline_readiness_never_exposes_enabled_money_even_with_credentials_flags(self):
        policy_type = getattr(plan_catalog, "BillingRuntimePolicy", None)
        self.assertTrue(callable(policy_type))
        for product in ("viewer_plus", "streamer_plus"):
            for method in ("stars", "sbp", "bank_card"):
                result = plan_catalog.checkout_readiness(product, method, policy_type())
                self.assertFalse(result.enabled)
                self.assertEqual(result.reason_code, "payments_unavailable")
                sandbox = policy_type(mode="sandbox", target_verified=True, allow_external_create=True,
                                      allow_invoice=True, period_approved=True, refund_policy_approved=True)
                self.assertFalse(plan_catalog.checkout_readiness(product, method, sandbox).enabled,
                                 "unapproved catalog period/XTR cannot be overridden by a generic runtime flag")
        with self.assertRaises(ValueError):
            plan_catalog.checkout_readiness("viewer_plus", "crypto", policy_type())

    def test_catalog_features_preserve_free_and_exclude_unverified_paid_events(self):
        catalog = getattr(plan_catalog, "catalog_payload", None)
        self.assertTrue(callable(catalog))
        payload = catalog()
        self.assertEqual(payload["limits"], {"free_streamers": 50, "plus_streamers": 200, "video_slots": 5})
        ids = set(payload["features"])
        self.assertTrue({"viewer_filters", "viewer_categories", "viewer_reminders", "viewer_folders", "viewer_history"} <= ids)
        self.assertFalse({"raid", "channel_name_change", "viewer_spikes", "clicks", "collabs"} & ids)
        self.assertIn("quiet_hours", payload["free_features"])
        for feature_id in ids:
            self.assertTrue(plan_catalog.get_feature(feature_id)["title"])
        with self.assertRaises(ValueError):
            plan_catalog.get_feature("paid_raid")


class ProductCatalogApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app, self.db = await build_fixture("payment-off")
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_authenticated_catalog_has_prices_all_methods_and_no_order_or_grant(self):
        response = await self.client.post("/app/api/subscription/catalog", json={"init_data": signed_webapp(501)})
        self.assertEqual(response.status, 200)
        data = await response.json()
        products = {p["product_id"]: p for p in data["products"]}
        self.assertEqual(products["viewer_plus"]["rub"]["amount_minor"], 15000)
        self.assertEqual(products["streamer_plus"]["rub"]["amount_minor"], 30000)
        self.assertEqual(products["streamer_plus"]["includes"], ["viewer_plus"])
        self.assertEqual(products["streamer_plus"]["price_label"], "300 ₽")
        self.assertEqual({m["id"] for m in data["methods"]}, {"stars", "sbp", "bank_card"})
        self.assertTrue(all(not m["readiness"]["enabled"] for m in data["methods"]))
        self.assertEqual((await (await self.db.conn.execute("SELECT count(*) FROM billing_orders")).fetchone())[0], 0)
        self.assertEqual((await (await self.db.conn.execute("SELECT count(*) FROM entitlement_grants")).fetchone())[0], 0)

    async def test_catalog_rejects_client_prices_products_and_identity_fields(self):
        for extra in ({"amount": 1}, {"product": "stars_plus"}, {"method": "crypto"}, {"user_id": 999}, {"paid": True}):
            with self.subTest(extra=extra):
                response = await self.client.post("/app/api/subscription/catalog", json={"init_data": signed_webapp(501), **extra})
                self.assertEqual(response.status, 400)
        response = await self.client.post("/app/api/subscription/catalog", json={})
        self.assertEqual(response.status, 401)
        response = await self.client.post("/app/api/subscription/catalog", json={"init_data": signed_webapp(501).replace("Owner", "Other")})
        self.assertEqual(response.status, 403)
