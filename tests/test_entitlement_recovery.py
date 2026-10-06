"""Деньги списаны, доступ не выдан: состояние должно быть видно и восстановимо.

Аудит (A1): когда подтверждённая оплата не превращалась в доступ (например,
условия заказа разошлись с замороженным снимком), заказ помечался
«подтверждён», доступа не было, и ни следа в журнале, ни сигнала владельцу.
Здесь проверяется, что проблема фиксируется, повторяется безопасно и
заканчивается ровно одной выдачей.
"""
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money, VerifiedPaymentEvidence
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.stars_provider import TelegramStarsProvider

PERIOD = AccessPeriodPolicy("30_days", "recovery-fixture-v1")
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
    bot_id = 77

    def __init__(self):
        self.invoices = []

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=21)


class EntitlementRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = Sender()
        self.provider = TelegramStarsProvider(self.sender, POLICY)
        self.service = BillingService(
            self.db, self.provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="recovery-terms-v1",
        )

    async def paid_order_with_broken_snapshot(self):
        """Заказ с оплатой, но выдача не проходит из-за испорченного снимка."""
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "recovery-key", now=100)
        order = await self.db.get_billing_order(result.order_id)
        attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?",
            (order.order_id,),
        )).fetchone()
        original = order.product_snapshot_json
        await self.db.conn.execute(
            "UPDATE billing_orders SET product_snapshot_json=? WHERE order_id=?",
            ('{"product_id":"viewer_plus"}', order.order_id),
        )
        await self.db.conn.commit()
        evidence = VerifiedPaymentEvidence(
            "telegram_stars", "stx_recovery_charge", order.order_id, attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", 110,
        )
        return order.order_id, original, evidence

    async def test_a_paid_order_without_access_is_flagged_and_logged(self):
        order_id, _original, evidence = await self.paid_order_with_broken_snapshot()

        result = await self.service.apply_payment_evidence(evidence, now=120)
        order = await self.db.get_billing_order(order_id)
        audit = [row[0] for row in await (await self.db.conn.execute(
            "SELECT action FROM billing_audit WHERE order_id=? ORDER BY happened_at", (order_id,),
        )).fetchall()]

        self.assertEqual(result.state, "manual_review")
        self.assertIsNone(order.grant_id)
        self.assertEqual(order.entitlement_state, "review")
        self.assertIn("entitlement_review", audit)
        self.assertTrue(order.entitlement_error)
        self.assertIsNotNone(order.entitlement_updated_at)

    async def test_recovery_finishes_the_grant_once_and_then_stops(self):
        order_id, original, evidence = await self.paid_order_with_broken_snapshot()
        await self.service.apply_payment_evidence(evidence, now=120)
        await self.db.conn.execute(
            "UPDATE billing_orders SET product_snapshot_json=? WHERE order_id=?",
            (original, order_id),
        )
        await self.db.conn.commit()

        first = await self.service.retry_pending_entitlements(now=200)
        second = await self.service.retry_pending_entitlements(now=260)
        order = await self.db.get_billing_order(order_id)
        grants = (await (await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key=?",
            ("paid-order:" + order_id,),
        )).fetchone())[0]

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(order.entitlement_state, "applied")
        self.assertIsNotNone(order.grant_id)
        self.assertEqual(grants, 1)
        self.assertTrue(await self.db.has_viewer_plus(101, now=201))

    async def test_recovery_ignores_orders_without_confirmed_money(self):
        await self.service.prepare_payment(101, "viewer_plus", "stars", "unpaid-key", now=100)

        self.assertEqual(await self.service.retry_pending_entitlements(now=200), 0)

    async def test_parallel_recovery_never_grants_twice(self):
        order_id, original, evidence = await self.paid_order_with_broken_snapshot()
        await self.service.apply_payment_evidence(evidence, now=120)
        await self.db.conn.execute(
            "UPDATE billing_orders SET product_snapshot_json=? WHERE order_id=?",
            (original, order_id),
        )
        await self.db.conn.commit()

        import asyncio

        outcomes = await asyncio.gather(*(
            self.service.retry_pending_entitlements(now=300) for _ in range(8)
        ))
        grants = (await (await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key=?",
            ("paid-order:" + order_id,),
        )).fetchone())[0]

        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(grants, 1)

    async def test_a_still_broken_order_stays_visible_for_the_owner(self):
        order_id, _original, evidence = await self.paid_order_with_broken_snapshot()
        await self.service.apply_payment_evidence(evidence, now=120)

        self.assertEqual(await self.service.retry_pending_entitlements(now=200), 0)
        pending = await self.db.list_orders_needing_entitlement_review()

        self.assertEqual([row.order_id for row in pending], [order_id])
        self.assertEqual(json.loads('{"product_id":"viewer_plus"}')["product_id"], "viewer_plus")


if __name__ == "__main__":
    unittest.main()
