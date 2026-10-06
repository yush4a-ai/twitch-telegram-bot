"""Служебные данные не должны расти бесконечно.

Аудит (D17, R1): журнал входящих апдейтов Telegram и незакрытые заказы копились
без очистки. Удалять можно только то, что не является финансовой записью:
оплаченный заказ, заказ с подтверждённым платежом или выданным доступом
сохраняется всегда.
"""
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money, VerifiedPaymentEvidence
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.stars_provider import TelegramStarsProvider

PERIOD = AccessPeriodPolicy("30_days", "cleanup-fixture-v1")
POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_external_create=False,
    allow_invoice=True, period_approved=True, refund_policy_approved=True,
    allow_public_stars=True,
)
NOW = 1_800_000_000.0
DAY = 86400.0


def fixture_product(product_id):
    return replace(
        get_product(product_id), xtr=Money(100, "XTR"),
        period_rule=PERIOD.rule, period_rule_version=PERIOD.version,
    )


class Sender:
    network_free = True
    bot_id = 55

    def __init__(self):
        self.invoices = []

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=41)


class TransientCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.sender = Sender()
        self.provider = TelegramStarsProvider(self.sender, POLICY)
        self.service = BillingService(
            self.db, self.provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="cleanup-terms-v1",
        )

    async def add_update(self, update_id: int, *, status: str, age_days: float):
        stamp = NOW - age_days * DAY
        await self.db.conn.execute(
            "INSERT INTO telegram_update_inbox(bot_id,update_id,kind,status,payload,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (55, update_id, "message", status, "", stamp, stamp),
        )
        await self.db.conn.commit()

    async def count(self, table: str) -> int:
        return (await (await self.db.conn.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]

    async def test_old_finished_updates_are_removed_and_fresh_ones_stay(self):
        await self.add_update(1, status="done", age_days=30)
        await self.add_update(2, status="unknown", age_days=30)
        await self.add_update(3, status="done", age_days=1)
        await self.add_update(4, status="received", age_days=30)

        removed = await self.db.clean_transient_data(now=NOW, update_inbox_days=7)

        self.assertEqual(removed["updates"], 2)
        self.assertEqual(await self.count("telegram_update_inbox"), 2)

    async def test_an_expired_unpaid_order_is_removed_with_its_attempts(self):
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "old-key", now=NOW - 40 * DAY)
        await self.db.conn.execute(
            "UPDATE billing_orders SET status='expired', financial_status='canceled', closed_at=? WHERE order_id=?",
            (NOW - 40 * DAY, result.order_id),
        )
        await self.db.conn.commit()

        removed = await self.db.clean_transient_data(now=NOW, expired_order_days=30)

        self.assertEqual(removed["orders"], 1)
        self.assertEqual(await self.count("billing_orders"), 0)
        self.assertEqual(await self.count("billing_payment_attempts"), 0)

    async def test_a_paid_order_is_never_removed(self):
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "paid-key", now=NOW - 400 * DAY)
        attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (result.order_id,),
        )).fetchone()
        evidence = VerifiedPaymentEvidence(
            "telegram_stars", "stx_cleanup_charge", result.order_id, attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", NOW - 400 * DAY,
        )
        await self.service.apply_payment_evidence(evidence, now=NOW - 400 * DAY)

        removed = await self.db.clean_transient_data(now=NOW, expired_order_days=30)

        self.assertEqual(removed["orders"], 0)
        self.assertEqual(await self.count("billing_orders"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 1)

    async def test_an_order_without_access_but_with_money_is_kept(self):
        result = await self.service.prepare_payment(101, "viewer_plus", "stars", "review-key", now=NOW - 40 * DAY)
        attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (result.order_id,),
        )).fetchone()
        await self.db.conn.execute(
            "UPDATE billing_orders SET product_snapshot_json='{}', status='expired', "
            "financial_status='canceled', closed_at=? WHERE order_id=?",
            (NOW - 40 * DAY, result.order_id),
        )
        await self.db.conn.commit()
        evidence = VerifiedPaymentEvidence(
            "telegram_stars", "stx_review_charge", result.order_id, attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", NOW - 40 * DAY,
        )
        await self.service.apply_payment_evidence(evidence, now=NOW - 40 * DAY)
        self.assertEqual(await self.count("billing_provider_facts"), 1)

        removed = await self.db.clean_transient_data(now=NOW, expired_order_days=30)

        self.assertEqual(removed["orders"], 0)
        self.assertEqual(await self.count("billing_orders"), 1)


if __name__ == "__main__":
    unittest.main()
