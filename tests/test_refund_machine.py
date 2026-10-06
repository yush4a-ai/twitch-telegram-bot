"""Возврат средств: подтверждённый возврат обязан снять доступ.

Аудит (A4, B7): путь возврата был недостижим, а сам вызов не отзывал грант —
человек получал деньги назад и оставался с подпиской. Здесь проверяется
внутренняя машина: отзыв ровно своего гранта, идемпотентность и сохранение
независимой подписки.
"""
import unittest
from dataclasses import replace
from types import SimpleNamespace

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money, VerifiedPaymentEvidence
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.stars_provider import TelegramStarsProvider

PERIOD = AccessPeriodPolicy("30_days", "refund-fixture-v1")
POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_external_create=False,
    allow_invoice=True, period_approved=True, refund_policy_approved=True,
    allow_public_stars=True,
)
OWNER = 999


def fixture_product(product_id):
    return replace(
        get_product(product_id), xtr=Money(100, "XTR"),
        period_rule=PERIOD.rule, period_rule_version=PERIOD.version,
    )


class Sender:
    network_free = True
    bot_id = 314

    def __init__(self, refund_ok=True):
        self.refund_ok = refund_ok
        self.refunds = []

    async def send_invoice(self, **fields):
        return SimpleNamespace(message_id=51)

    async def refund_star_payment(self, **fields):
        self.refunds.append(fields)
        return self.refund_ok


class RefundMachineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = Sender()
        self.provider = TelegramStarsProvider(self.sender, POLICY)
        self.service = BillingService(
            self.db, self.provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="refund-terms-v1",
            merchant_actor_ids=frozenset({OWNER}),
        )

    async def paid_order(self, key: str = "refund-key"):
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", key, now=100)
        attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?",
            (result.order_id,),
        )).fetchone()
        evidence = VerifiedPaymentEvidence(
            "telegram_stars", "stx_refund_charge_" + key, result.order_id, attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", 110,
        )
        await self.service.apply_payment_evidence(evidence, now=110)
        return result.order_id

    async def test_an_accepted_refund_takes_the_access_away(self):
        order_id = await self.paid_order()

        outcome = await self.service.request_payment_refund(OWNER, order_id, "refund-1", now=200)
        order = await self.db.get_billing_order(order_id)
        grants = await (await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key=? AND revoked_at IS NOT NULL",
            ("paid-order:" + order_id,),
        )).fetchone()

        self.assertEqual(outcome.state, "accepted")
        self.assertEqual(order.status, "refunded")
        self.assertEqual(order.financial_status, "refunded")
        self.assertEqual(grants[0], 1)
        self.assertFalse(await self.db.has_viewer_plus(101, now=201))

    async def test_a_refund_does_not_touch_an_independent_grant(self):
        order_id = await self.paid_order("independent-key")
        await self.db.issue_test_viewer_plus(101, "independent", starts_at=90, expires_at=900, issued_by=OWNER, now=90)

        await self.service.request_payment_refund(OWNER, order_id, "refund-2", now=200)

        # Независимый доступ остаётся: возврат снимает только свой заказ.
        self.assertTrue(await self.db.has_viewer_plus(101, now=201))

    async def test_a_declined_refund_keeps_the_access(self):
        self.sender.refund_ok = False
        order_id = await self.paid_order("declined-key")

        outcome = await self.service.request_payment_refund(OWNER, order_id, "refund-3", now=200)

        self.assertEqual(outcome.state, "declined")
        self.assertTrue(await self.db.has_viewer_plus(101, now=201))

    async def test_only_the_merchant_can_request_a_refund(self):
        order_id = await self.paid_order("authority-key")

        with self.assertRaises(PermissionError):
            await self.service.request_payment_refund(101, order_id, "refund-4", now=200)

    async def test_a_repeated_refund_is_idempotent(self):
        order_id = await self.paid_order("repeat-key")
        await self.service.request_payment_refund(OWNER, order_id, "refund-5", now=200)

        again = await self.service.request_payment_refund(OWNER, order_id, "refund-6", now=260)

        self.assertIn(again.state, {"accepted", "refunded"})
        self.assertEqual(len(self.sender.refunds), 1)


if __name__ == "__main__":
    unittest.main()
