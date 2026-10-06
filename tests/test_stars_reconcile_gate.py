"""Сверка звёзд не должна звонить в Telegram, когда восстанавливать нечего.

Аудит (B3): воркер сверки не проверял политику и всегда запрашивал историю
транзакций бота. При выключенной оплате это лишние внешние вызовы; кроме того,
ссылка на счёт должна вести только на домен Telegram (B8).
"""
import unittest
from dataclasses import replace
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

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=31)

    async def create_invoice_link(self, **fields):
        self.link_calls.append(fields)
        return self.link

    async def get_star_transactions(self, *, limit: int = 30, offset: int = 0):
        self.history_calls += 1
        return SimpleNamespace(transactions=[])


class StarsReconcileGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    def service(self, sender):
        provider = TelegramStarsProvider(sender, POLICY)
        return BillingService(
            self.db, provider, runtime_policy=POLICY, access_policy=PERIOD,
            catalog=fixture_product, terms_version="gate-terms-v1",
        )

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
