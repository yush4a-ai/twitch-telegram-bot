import unittest
import os
import tempfile

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider, PaymentVerificationError, VerifiedPaymentEvent
from bot.database import Database


SECRET = b"local-billing-lifecycle-secret-32-bytes"


class FailRefundProvider(MockPaymentProvider):
    async def request_refund(self, payment_id, request_key):
        raise RuntimeError("mock refund outage")


class BillingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=1)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=1)
        self.provider = MockPaymentProvider(SECRET)
        self.service = BillingService(self.db, self.provider)

    async def asyncTearDown(self):
        await self.db.close()

    async def checkout(self, key="order-1", duration=60):
        return await self.service.create_checkout(101, key, duration_seconds=duration, now=100)

    def signed_event(self, order_id, event_id, kind, payment_id="payment-1", now=110):
        event = VerifiedPaymentEvent(
            provider="mock", event_id=event_id, order_id=order_id,
            payment_id=payment_id, event_type=kind, units=1, currency="TEST",
        )
        return self.provider.sign_test_event(event, now=now)

    async def test_verified_capture_activates_one_grant_and_replay_does_not_duplicate(self):
        checkout = await self.checkout()
        self.assertFalse(await self.db.has_streamer_plus(101, now=109))
        body, headers = self.signed_event(checkout.order_id, "event-capture", "captured")
        self.assertEqual(await self.service.handle_webhook(body, headers, now=110), "paid")
        self.assertTrue(await self.db.has_streamer_plus(101, now=110))
        self.assertFalse(await self.db.has_streamer_plus(101, now=170))
        order = await self.db.get_billing_order(checkout.order_id)
        self.assertEqual((order.status, order.paid_at), ("paid", 110))
        self.assertIsNotNone(order.grant_id)
        payment = await self.db.get_billing_payment(checkout.order_id)
        self.assertEqual((payment.payment_id, payment.status), ("payment-1", "captured"))
        self.assertEqual(await self.service.handle_webhook(body, headers, now=111), "paid")
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants WHERE source='mock'")
        self.assertEqual((await cursor.fetchone())[0], 1)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM billing_payments")
        self.assertEqual((await cursor.fetchone())[0], 1)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM billing_webhook_events")
        self.assertEqual((await cursor.fetchone())[0], 1)
        with self.assertRaises(ValueError):
            await self.service.cancel_order(101, checkout.order_id, now=112)

    async def test_event_id_conflict_and_duplicate_capture_cannot_change_payment(self):
        checkout = await self.checkout()
        body, headers = self.signed_event(checkout.order_id, "event-1", "captured")
        await self.service.handle_webhook(body, headers, now=110)
        changed, changed_headers = self.signed_event(
            checkout.order_id, "event-1", "captured", payment_id="payment-2",
        )
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(changed, changed_headers, now=111)
        duplicate, duplicate_headers = self.signed_event(checkout.order_id, "event-2", "captured")
        self.assertEqual(await self.service.handle_webhook(duplicate, duplicate_headers, now=111), "paid")
        second_payment, second_headers = self.signed_event(
            checkout.order_id, "event-3", "captured", payment_id="payment-2",
        )
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(second_payment, second_headers, now=111)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants WHERE source='mock'")
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_refund_request_waits_for_verified_event_and_revokes_only_mock_grant(self):
        checkout = await self.checkout()
        body, headers = self.signed_event(checkout.order_id, "event-capture", "captured")
        await self.service.handle_webhook(body, headers, now=110)
        await self.db.issue_test_streamer_plus(
            "11", "independent-test", starts_at=100, expires_at=300,
            issued_by=425785231, now=100,
        )
        with self.assertRaises(PermissionError):
            await self.service.request_refund(202, checkout.order_id, "refund-1", now=120)
        reference = await self.service.request_refund(101, checkout.order_id, "refund-1", now=120)
        self.assertEqual(reference, "mock-refund:payment-1:refund-1")
        self.assertEqual(await self.service.request_refund(101, checkout.order_id, "refund-1", now=121),
                         reference)
        self.assertEqual((await self.db.get_billing_order(checkout.order_id)).status, "paid")
        refunded, refund_headers = self.signed_event(
            checkout.order_id, "event-refund", "refunded", now=122,
        )
        self.assertEqual(await self.service.handle_webhook(refunded, refund_headers, now=122), "refunded")
        self.assertEqual(await self.service.handle_webhook(refunded, refund_headers, now=123), "refunded")
        self.assertEqual(await self.service.handle_webhook(body, headers, now=124), "paid")
        self.assertTrue(await self.db.has_streamer_plus(101, now=124))
        cursor = await self.db.conn.execute(
            "SELECT source,revoked_at FROM entitlement_grants ORDER BY source"
        )
        self.assertEqual(await cursor.fetchall(), [("mock", 122), ("test", None)])
        self.assertEqual((await self.db.get_billing_payment(checkout.order_id)).status, "refunded")

    async def test_late_capture_refund_before_capture_and_bad_signature_write_nothing(self):
        cancelled = await self.checkout("cancelled")
        await self.service.cancel_order(101, cancelled.order_id, now=101)
        late, late_headers = self.signed_event(cancelled.order_id, "late", "captured")
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(late, late_headers, now=110)
        pending = await self.checkout("pending")
        premature, premature_headers = self.signed_event(pending.order_id, "early-refund", "refunded")
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(premature, premature_headers, now=110)
        capture, capture_headers = self.signed_event(pending.order_id, "bad-sig", "captured")
        with self.assertRaises(PaymentVerificationError):
            await self.service.handle_webhook(capture + b" ", capture_headers, now=110)
        await self.service.expire_pending(now=1000)
        expired, expired_headers = self.signed_event(pending.order_id, "expired-capture", "captured", now=1000)
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(expired, expired_headers, now=1000)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM billing_payments")
        self.assertEqual((await cursor.fetchone())[0], 0)
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_failure_after_payment_insert_rolls_back_order_event_payment_and_grant(self):
        checkout = await self.checkout()
        await self.db.conn.execute(
            "CREATE TEMP TRIGGER fail_grant_audit BEFORE INSERT ON entitlement_events "
            "BEGIN SELECT RAISE(ABORT, 'grant audit failure'); END"
        )
        body, headers = self.signed_event(checkout.order_id, "event-1", "captured")
        with self.assertRaisesRegex(Exception, "grant audit failure"):
            await self.service.handle_webhook(body, headers, now=110)
        self.assertEqual((await self.db.get_billing_order(checkout.order_id)).status, "pending")
        for table in ("billing_payments", "billing_webhook_events", "entitlement_grants"):
            cursor = await self.db.conn.execute(f"SELECT COUNT(*) FROM {table}")
            self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_event_time_and_payment_id_cannot_cross_order_boundaries(self):
        first = await self.checkout("first")
        early, early_headers = self.signed_event(first.order_id, "early", "captured", now=99)
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(early, early_headers, now=99)
        captured, captured_headers = self.signed_event(first.order_id, "captured", "captured")
        await self.service.handle_webhook(captured, captured_headers, now=110)
        old_duplicate, old_headers = self.signed_event(first.order_id, "old-duplicate", "captured", now=109)
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(old_duplicate, old_headers, now=109)
        second = await self.checkout("second")
        duplicate_payment, duplicate_headers = self.signed_event(
            second.order_id, "other-order", "captured", payment_id="payment-1",
        )
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(duplicate_payment, duplicate_headers, now=110)
        early_refund, refund_headers = self.signed_event(
            first.order_id, "early-refund", "refunded", now=109,
        )
        with self.assertRaises(ValueError):
            await self.service.handle_webhook(early_refund, refund_headers, now=109)
        self.assertEqual((await self.db.get_billing_order(first.order_id)).status, "paid")
        self.assertEqual((await self.db.get_billing_order(second.order_id)).status, "pending")

    async def test_refund_provider_failure_leaves_paid_order_and_no_refund_request(self):
        checkout = await self.checkout()
        body, headers = self.signed_event(checkout.order_id, "capture", "captured")
        await self.service.handle_webhook(body, headers, now=110)
        failing = BillingService(self.db, FailRefundProvider(SECRET))
        with self.assertRaises(RuntimeError):
            await failing.request_refund(101, checkout.order_id, "refund-fail", now=120)
        self.assertEqual((await self.db.get_billing_order(checkout.order_id)).status, "paid")
        self.assertIsNone(await self.db.get_billing_refund_request("refund-fail"))

    async def test_verified_event_replay_is_idempotent_after_database_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "billing.db")
            db = Database(path)
            await db.connect()
            try:
                await db.link_streamer_identity(303, "33", "gamma", verified_at=1)
                service = BillingService(db, self.provider)
                checkout = await service.create_checkout(303, "persistent", duration_seconds=60, now=100)
                body, headers = self.signed_event(checkout.order_id, "persisted-event", "captured")
                self.assertEqual(await service.handle_webhook(body, headers, now=110), "paid")
            finally:
                await db.close()
            reopened = Database(path)
            await reopened.connect()
            try:
                service = BillingService(reopened, self.provider)
                self.assertEqual(await service.handle_webhook(body, headers, now=111), "paid")
                cursor = await reopened.conn.execute(
                    "SELECT COUNT(*) FROM entitlement_grants WHERE source='mock'"
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
            finally:
                await reopened.close()


if __name__ == "__main__":
    unittest.main()
