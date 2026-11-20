"""Сверка звёзд не должна звонить в Telegram, когда восстанавливать нечего.

Аудит (B3): воркер сверки не проверял политику и всегда запрашивал историю
транзакций бота. При выключенной оплате это лишние внешние вызовы; кроме того,
ссылка на счёт должна вести только на домен Telegram (B8).
"""
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money
from bot.billing_provider import PaymentCreationUnknown
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.stars_provider import TelegramStarsProvider

PERIOD = AccessPeriodPolicy("30_days", "gate-fixture-v1")
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
    bot_id = 909

    def __init__(self, link="https://t.me/invoice/ok"):
        self.link = link
        self.invoices = []
        self.link_calls = []
        self.history_calls = 0
        self.history_offsets = []
        self.transactions = []

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=31)

    async def create_invoice_link(self, **fields):
        self.link_calls.append(fields)
        return self.link

    async def get_star_transactions(self, *, limit: int = 30, offset: int = 0):
        self.history_calls += 1
        self.history_offsets.append(offset)
        return SimpleNamespace(transactions=list(self.transactions[offset:offset + limit]))


class StarsReconcileGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(str(Path(directory.name) / "billing.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    def service(self, sender):
        provider = TelegramStarsProvider(sender, POLICY)
        return BillingService(
            self.db, provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="gate-terms-v1",
        )

    async def _payment(self, service, request_key):
        order = await service.prepare_payment(101, "viewer_plus", "stars", request_key, now=100)
        row = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (order.order_id,),
        )).fetchone()
        transaction = SimpleNamespace(
            id="synthetic-charge", amount=100, date=datetime.fromtimestamp(110, timezone.utc),
            source=SimpleNamespace(invoice_payload=service._provider.invoice_payload(order.order_id, row[0])),
        )
        return order, transaction

    async def test_history_scan_resumes_after_five_pages_in_new_service_instance(self):
        sender = Sender()
        service = self.service(sender)
        _order, payment = await self._payment(service, "audit-pagination")
        sender.transactions = [SimpleNamespace(source=None) for _ in range(150)] + [payment]

        self.assertEqual(await service.apply_stars_transactions(now=120), 0)
        self.assertEqual(sender.history_offsets, [0, 30, 60, 90, 120])
        await self.db.close()
        await self.db.connect()
        restarted = self.service(sender)
        self.assertEqual(await restarted.apply_stars_transactions(now=420), 1)
        self.assertEqual(sender.history_offsets[-1], 150)
        self.assertTrue(await self.db.has_viewer_plus(101, now=420))
        self.assertEqual(await restarted.apply_stars_transactions(now=720), 0)

    async def test_old_closed_order_is_checked_after_a_reconciliation_outage(self):
        sender = Sender()
        service = self.service(sender)
        order, payment = await self._payment(service, "audit-outage")
        self.assertEqual(await service.apply_stars_transactions(now=120), 0)
        self.assertEqual(await service.expire_pending(now=20_000), 1)
        sender.transactions = [payment]

        self.assertEqual(await self.service(sender).apply_stars_transactions(now=106_401), 1)
        self.assertIsNotNone((await self.db.get_billing_order(order.order_id)).grant_id)

    async def test_history_retries_a_failed_page_from_the_same_offset(self):
        sender = Sender()
        service = self.service(sender)
        _order, payment = await self._payment(service, "audit-page-failure")
        first_page = SimpleNamespace(transactions=[SimpleNamespace(source=None)] * 30)
        sender.get_star_transactions = AsyncMock(side_effect=[
            first_page, TimeoutError("history unavailable"), SimpleNamespace(transactions=[payment]),
        ])

        self.assertEqual(await service.apply_stars_transactions(now=120), 0)
        self.assertEqual(await service.apply_stars_transactions(now=420), 1)
        self.assertEqual([call.kwargs["offset"] for call in sender.get_star_transactions.await_args_list], [0, 30, 30])

    async def test_a_repeat_sweep_does_not_inflate_the_counter(self):
        """Счётчик показывает реально восстановленные оплаты, а не повторы."""
        from bot.billing_models import Money, VerifiedPaymentEvidence

        sender = Sender()
        service = self.service(sender)
        paid = await service.prepare_payment(101, "viewer_plus", "stars", "counter-a", now=100)
        waiting = await service.prepare_payment(202, "viewer_plus", "stars", "counter-b", now=100)
        paid_attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (paid.order_id,),
        )).fetchone()
        waiting_attempt = await (await self.db.conn.execute(
            "SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (waiting.order_id,),
        )).fetchone()
        # Первый заказ уже выдан обычным путём, второй ждёт восстановления.
        await service.apply_payment_evidence(VerifiedPaymentEvidence(
            "telegram_stars", "stx_counter_paid", paid.order_id, paid_attempt[0],
            Money(100, "XTR"), "stars", "confirmed", "successful_payment", 110,
        ), now=110)
        sender.transactions = [
            SimpleNamespace(
                id="stx_counter_paid", amount=100,
                date=datetime.fromtimestamp(110, timezone.utc),
                source=SimpleNamespace(invoice_payload=service._provider.invoice_payload(
                    paid.order_id, paid_attempt[0])),
            ),
            SimpleNamespace(
                id="stx_counter_waiting", amount=100,
                date=datetime.fromtimestamp(111, timezone.utc),
                source=SimpleNamespace(invoice_payload=service._provider.invoice_payload(
                    waiting.order_id, waiting_attempt[0])),
            ),
        ]

        applied = await service.apply_stars_transactions(now=120)

        self.assertEqual(applied, 1)

    async def test_no_telegram_call_when_there_is_nothing_to_recover(self):
        sender = Sender()
        service = self.service(sender)

        applied = await service.apply_stars_transactions(now=100)

        self.assertEqual(applied, 0)
        self.assertEqual(sender.history_calls, 0)

    async def test_history_is_read_when_an_unfinished_stars_order_exists(self):
        sender = Sender()
        service = self.service(sender)
        await service.prepare_payment(101, "viewer_plus", "stars", "gate-key", now=100)

        await service.apply_stars_transactions(now=120)

        self.assertEqual(sender.history_calls, 1)

    async def test_an_invoice_link_must_point_at_telegram(self):
        sender = Sender(link="https://evil.example/invoice")
        provider = TelegramStarsProvider(sender, POLICY)
        service = BillingService(
            self.db, provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="gate-terms-v1",
        )

        result = await service.prepare_payment(101, "viewer_plus", "stars", "link-key",
                                               now=100, prefer_link=True)

        # Подменённая ссылка не попадает в заказ: исход попытки неизвестен.
        self.assertEqual(result.state, "creation_unknown")
        order = await self.db.get_billing_order(result.order_id)
        self.assertIsNone(order.checkout_url)
        self.assertEqual(sender.history_calls, 0)


if __name__ == "__main__":
    unittest.main()
