"""Истечение и отмена заказа: покупка не должна блокироваться навсегда.

Аудит (A3, A2) показал: истечение и отмена меняли только `status`, оставляя
финансовый статус «ожидает», а блокировка новой покупки смотрит именно на
финансовый статус. В результате человек, чей счёт просто истёк, больше не мог
купить подписку.
"""
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.stars_provider import TelegramStarsProvider

PERIOD = AccessPeriodPolicy("30_days", "expiry-fixture-v1")
POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_external_create=False,
    allow_invoice=True, period_approved=True, refund_policy_approved=True,
    allow_public_stars=True,
)


def fixture_product(product_id):
    return replace(
        get_product(product_id), xtr=Money(100, "XTR"),
        period_rule=PERIOD.rule, period_rule_version=PERIOD.version,
    )


class Sender:
    network_free = True
    bot_id = 4242

    def __init__(self):
        self.invoices = []

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=11)


class OrderExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = Sender()
        self.provider = TelegramStarsProvider(self.sender, POLICY)
        self.service = BillingService(
            self.db, self.provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="expiry-terms-v1",
        )

    async def test_an_expired_unpaid_order_does_not_block_the_next_purchase(self):
        """Истёкший счёт нельзя оплатить, поэтому он не должен мешать покупать."""
        first = await self.service.prepare_payment(101, "viewer_plus", "stars", "expired-key", now=100)
        self.assertEqual(first.state, "pending")

        expired = await self.db.expire_pending_billing_orders(now=100 + 901)
        self.assertEqual(expired, 1)
        order = await self.db.get_billing_order(first.order_id)
        self.assertEqual(order.status, "expired")

        second = await self.service.prepare_payment(101, "viewer_plus", "stars", "after-expiry", now=100 + 902)

        self.assertEqual(second.state, "pending")
        self.assertNotEqual(second.order_id, first.order_id)

    async def test_an_expired_order_no_longer_counts_as_the_active_one(self):
        """Даже без очистки истёкший заказ не должен считаться активным."""
        first = await self.service.prepare_payment(101, "viewer_plus", "stars", "stale-key", now=100)
        self.assertEqual(first.state, "pending")

        # Срок счёта прошёл, но строку в базе никто не чистил.
        second = await self.service.prepare_payment(
            101, "viewer_plus", "stars", "fresh-key", now=100 + 901)

        self.assertEqual(second.state, "pending")
        self.assertNotEqual(second.order_id, first.order_id)

    async def test_cancelling_an_already_paid_order_is_a_calm_refusal(self):
        """Отмена не должна падать исключением, если оплата уже прошла."""
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "paid-key", now=100)
        attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?",
            (result.order_id,),
        )).fetchone()
        from bot.billing_models import VerifiedPaymentEvidence

        evidence = VerifiedPaymentEvidence(
            "telegram_stars", "stx_expiry_charge", result.order_id, attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", 110,
        )
        self.assertEqual((await self.service.apply_payment_evidence(evidence, now=110)).state, "applied")

        cancelled = await self.db.cancel_billing_order(101, result.order_id, now=120)

        self.assertFalse(cancelled)
        order = await self.db.get_billing_order(result.order_id)
        self.assertEqual(order.status, "paid")
        self.assertTrue(await self.db.has_viewer_plus(101, now=121))

    async def test_cancelling_a_pending_order_clears_the_active_marker(self):
        """Отмена незакрытого заказа должна снимать блокировку новой покупки."""
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "cancel-key", now=100)
        self.assertTrue(await self.db.cancel_billing_order(101, result.order_id, now=120))

        follow_up = await self.service.prepare_payment(101, "viewer_plus", "stars", "cancel-next", now=121)

        self.assertEqual(follow_up.state, "pending")


if __name__ == "__main__":
    unittest.main()
