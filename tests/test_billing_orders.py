import os
import tempfile
import unittest

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider
from bot.database import Database


SECRET = b"local-billing-order-test-secret-32-bytes"


class FailOnceProvider(MockPaymentProvider):
    def __init__(self):
        super().__init__(SECRET)
        self.failed = False

    async def create_checkout(self, order_id, units, currency):
        if not self.failed:
            self.failed = True
            raise RuntimeError("mock outage")
        return await super().create_checkout(order_id, units, currency)


class FailCancelProvider(MockPaymentProvider):
    async def cancel_checkout(self, reference):
        raise RuntimeError("mock cancel outage")


class BillingOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "billing.db")
        self.db = Database(self.path)
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=1)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=1)
        self.service = BillingService(self.db, MockPaymentProvider(SECRET))

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_checkout_requires_link_and_replay_keeps_one_pending_order_without_plus(self):
        with self.assertRaises(PermissionError):
            await self.service.create_checkout(303, "request-1", duration_seconds=3600, now=100)
        first = await self.service.create_checkout(101, "request-1", duration_seconds=3600, now=100)
        self.assertTrue(first.reference.startswith("mock-checkout:"))
        second = await self.service.create_checkout(101, "request-1", duration_seconds=3600, now=101)
        self.assertEqual(first, second)
        order = await self.db.get_billing_order(first.order_id)
        self.assertEqual((order.telegram_user_id, order.broadcaster_id, order.status), (101, "11", "pending"))
        self.assertEqual((order.units, order.currency, order.duration_seconds), (1, "TEST", 3600))
        self.assertEqual(order.checkout_expires_at, 1000)
        self.assertFalse(await self.db.has_streamer_plus(101, now=101))
        with self.assertRaises(ValueError):
            await self.service.create_checkout(101, "request-1", duration_seconds=7200, now=101)
        with self.assertRaises(ValueError):
            await self.service.create_checkout(202, "request-1", duration_seconds=3600, now=101)

    async def test_provider_failure_retries_same_order_after_database_reopen(self):
        provider = FailOnceProvider()
        service = BillingService(self.db, provider)
        with self.assertRaises(RuntimeError):
            await service.create_checkout(101, "retry-1", duration_seconds=60, now=100)
        pending = await self.db.get_billing_order_by_request_key("retry-1")
        self.assertEqual(pending.status, "pending")
        self.assertIsNone(pending.checkout_reference)
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        service = BillingService(self.db, provider)
        checkout = await service.create_checkout(101, "retry-1", duration_seconds=60, now=101)
        self.assertEqual(checkout.order_id, pending.order_id)
        self.assertEqual(checkout.reference, "mock-checkout:" + pending.order_id)
        self.assertEqual((await self.db.get_billing_order(pending.order_id)).checkout_reference,
                         checkout.reference)

    async def test_cancel_pending_is_owner_scoped_and_expiry_is_server_time(self):
        checkout = await self.service.create_checkout(101, "cancel-1", duration_seconds=60, now=100)
        with self.assertRaises(PermissionError):
            await self.service.cancel_order(202, checkout.order_id, now=101)
        self.assertTrue(await self.service.cancel_order(101, checkout.order_id, now=101))
        self.assertEqual((await self.db.get_billing_order(checkout.order_id)).status, "cancelled")
        self.assertFalse(await self.service.cancel_order(101, checkout.order_id, now=102))
        expiring = await self.service.create_checkout(101, "expire-1", duration_seconds=60, now=100)
        self.assertEqual(await self.service.expire_pending(now=999), 0)
        self.assertEqual(await self.service.expire_pending(now=1000), 1)
        self.assertEqual((await self.db.get_billing_order(expiring.order_id)).status, "expired")
        self.assertFalse(await self.db.has_streamer_plus(101, now=1000))

    async def test_provider_cancel_failure_and_audit_failure_leave_consistent_state(self):
        service = BillingService(self.db, FailCancelProvider(SECRET))
        checkout = await service.create_checkout(101, "cancel-fail", duration_seconds=60, now=100)
        with self.assertRaises(RuntimeError):
            await service.cancel_order(101, checkout.order_id, now=101)
        self.assertEqual((await self.db.get_billing_order(checkout.order_id)).status, "pending")
        await self.db.conn.execute(
            "CREATE TEMP TRIGGER fail_billing_audit BEFORE INSERT ON billing_audit "
            "BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
        )
        with self.assertRaisesRegex(Exception, "audit failure"):
            await self.service.create_checkout(101, "audit-fail", duration_seconds=60, now=102)
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM billing_orders WHERE request_key='audit-fail'"
        )
        self.assertEqual((await cursor.fetchone())[0], 0)


if __name__ == "__main__":
    unittest.main()
