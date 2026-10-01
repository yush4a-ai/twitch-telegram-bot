import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from bot.billing import BillingService
from bot.billing_models import BillingSubject
from bot.billing_provider import MockPaymentProvider, PaymentVerificationError, VerifiedPaymentEvent
from bot.database import Database


SECRET = b"local-viewer-billing-test-secret-32"


class ViewerBillingProductTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.provider = MockPaymentProvider(SECRET)
        self.service = BillingService(self.db, self.provider)

    async def asyncTearDown(self):
        await self.db.close()

    def signed(self, order_id, event_id, kind, payment_id, *, now):
        event = VerifiedPaymentEvent(
            provider="mock", event_id=event_id, order_id=order_id,
            payment_id=payment_id, event_type=kind, units=1, currency="TEST",
        )
        return self.provider.sign_test_event(event, now=now)

    async def test_viewer_without_twitch_gets_only_viewer_grant(self):
        checkout = await self.service.create_checkout(
            303, "viewer-303", 3600, plan="viewer_plus", now=100,
        )
        order = await self.db.get_billing_order(checkout.order_id)
        self.assertEqual(order.subject, BillingSubject("viewer", "303"))
        self.assertIsNone(order.broadcaster_id)
        self.assertEqual(order.plan, "viewer_plus")
        self.assertFalse(await self.db.has_viewer_plus(303, now=101))
        body, headers = self.signed(checkout.order_id, "v-capture", "captured", "v-payment", now=110)
        self.assertEqual(await self.service.handle_webhook(body, headers, now=110), "paid")
        self.assertTrue(await self.db.has_viewer_plus(303, now=110))
        self.assertFalse(await self.db.has_streamer_plus(303, now=110))
        self.assertEqual(await self.service.handle_webhook(body, headers, now=111), "paid")
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE subject_kind='viewer' AND source='mock'"
        )
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_refund_only_revokes_its_own_product(self):
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=1)
        viewer = await self.service.create_checkout(
            101, "viewer-order", 3600, plan="viewer_plus", now=100,
        )
        streamer = await self.service.create_checkout(101, "streamer-order", 3600, now=100)
        for checkout, key, payment in (
            (viewer, "viewer-capture", "viewer-pay"),
            (streamer, "streamer-capture", "streamer-pay"),
        ):
            body, headers = self.signed(checkout.order_id, key, "captured", payment, now=110)
            self.assertEqual(await self.service.handle_webhook(body, headers, now=110), "paid")
        self.assertTrue(await self.db.has_viewer_plus(101, now=120))
        self.assertTrue(await self.db.has_streamer_plus(101, now=120))
        self.assertEqual(
            await self.service.request_refund(101, viewer.order_id, "viewer-refund", now=120),
            "mock-refund:viewer-pay:viewer-refund",
        )
        body, headers = self.signed(viewer.order_id, "viewer-refunded", "refunded", "viewer-pay", now=121)
        self.assertEqual(await self.service.handle_webhook(body, headers, now=121), "refunded")
        self.assertFalse(await self.db.has_viewer_plus(101, now=122))
        self.assertTrue(await self.db.has_streamer_plus(101, now=122))
        self.assertEqual((await self.db.get_billing_order(streamer.order_id)).status, "paid")

    async def test_unsigned_callback_and_client_plan_cannot_grant(self):
        checkout = await self.service.create_checkout(
            303, "viewer-bad-callback", 60, plan="viewer_plus", now=100,
        )
        body, _headers = self.signed(checkout.order_id, "fake-paid", "captured", "payment-1", now=110)
        with self.assertRaises(PaymentVerificationError):
            await self.service.handle_webhook(body, {}, now=110)
        self.assertFalse(await self.db.has_viewer_plus(303, now=111))
        with self.assertRaises(ValueError):
            await self.service.create_checkout(303, "both-products", 60, plan="both", now=100)
        with self.assertRaises(PermissionError):
            await self.db.create_billing_order(
                "a" * 32, "forged-subject", 303, 60, now=100,
                plan="viewer_plus", subject=BillingSubject("viewer", "404"),
            )


class LegacyBillingMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_streamer_orders_payments_and_audit_survive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy.db")
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(
                    "CREATE TABLE billing_orders ("
                    "order_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, "
                    "telegram_user_id INTEGER NOT NULL, broadcaster_id TEXT NOT NULL, "
                    "plan TEXT NOT NULL, provider TEXT NOT NULL, status TEXT NOT NULL, "
                    "units INTEGER NOT NULL, currency TEXT NOT NULL, duration_seconds INTEGER NOT NULL, "
                    "created_at REAL NOT NULL, checkout_expires_at REAL NOT NULL, "
                    "checkout_reference TEXT, paid_at REAL, closed_at REAL, grant_id TEXT UNIQUE);"
                    "CREATE TABLE billing_payments (provider TEXT NOT NULL, payment_id TEXT NOT NULL, "
                    "order_id TEXT NOT NULL, status TEXT NOT NULL, units INTEGER NOT NULL, "
                    "currency TEXT NOT NULL, captured_at REAL NOT NULL, refunded_at REAL, "
                    "PRIMARY KEY(provider,payment_id)) WITHOUT ROWID;"
                    "CREATE TABLE billing_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "order_id TEXT NOT NULL, action TEXT NOT NULL, happened_at REAL NOT NULL);"
                    "INSERT INTO billing_orders VALUES ('old-order','old-request',101,'11',"
                    "'streamer_plus','mock','paid',1,'TEST',3600,100,1000,"
                    "'mock-checkout:old-order',110,NULL,'old-grant');"
                    "INSERT INTO billing_payments VALUES ('mock','old-payment','old-order',"
                    "'captured',1,'TEST',110,NULL);"
                    "INSERT INTO billing_audit(order_id,action,happened_at) "
                    "VALUES ('old-order','created',100),('old-order','captured',110);"
                )
            db = Database(path)
            await db.connect()
            try:
                order = await db.get_billing_order("old-order")
                self.assertEqual(order.subject, BillingSubject("streamer", "11"))
                self.assertEqual((order.broadcaster_id, order.status, order.grant_id),
                                 ("11", "paid", "old-grant"))
                self.assertEqual((await db.get_billing_payment("old-order")).payment_id, "old-payment")
                cursor = await db.conn.execute(
                    "SELECT action FROM billing_audit WHERE order_id='old-order' ORDER BY id"
                )
                self.assertEqual([row[0] for row in await cursor.fetchall()], ["created", "captured"])
                self.assertIn("r10_001_billing_subjects", await db.schema_versions())
                self.assertEqual((await (await db.conn.execute("PRAGMA integrity_check")).fetchone())[0], "ok")
            finally:
                await db.close()
