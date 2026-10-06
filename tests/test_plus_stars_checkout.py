"""Оплата звёздами в боте: счёт создаётся только при явном разрешении владельца.

Правило первого релиза остаётся: СБП и карта закрыты, а звёзды открываются
отдельным флагом — наличие провайдера и утверждённых условий само по себе
оплату живым людям не включает.
"""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.billing import BillingService
from bot.billing_models import CheckoutResult
from bot.database import Database
from bot.handlers.telegram_plus import (
    cb_buy,
    cb_payment_method,
    cb_plus,
    payment_status_text,
    stars_checkout_ready,
)
from bot.plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE, BillingRuntimePolicy
from tests.test_telegram_navigation import CONFIG, message


def ready_policy(**overrides) -> BillingRuntimePolicy:
    values = dict(
        mode="sandbox", target_verified=True, allow_invoice=True,
        period_approved=True, refund_policy_approved=True, allow_public_stars=True,
    )
    values.update(overrides)
    return BillingRuntimePolicy(**values)


class StarsCheckoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=999, chat_id=101, user_id=101)
        )
        self.msg = message()
        self.provider = SimpleNamespace(
            provider_id="telegram_stars", network_free=True,
            create_payment=AsyncMock(), can_reconcile=True,
        )

    def cb(self, data, actor=101):
        return SimpleNamespace(
            data=data, message=self.msg,
            from_user=SimpleNamespace(id=actor), answer=AsyncMock(),
        )

    def service(self, policy):
        return BillingService(self.db, self.provider, runtime_policy=policy,
                              terms_version="2026-10-01")

    async def test_stars_is_ready_only_with_the_owner_flag(self):
        self.assertTrue(stars_checkout_ready(self.service(ready_policy())))
        self.assertFalse(
            stars_checkout_ready(self.service(ready_policy(allow_public_stars=False)))
        )
        self.assertFalse(stars_checkout_ready(self.service(ready_policy(mode="offline"))))
        self.assertIn("Stars", payment_status_text(self.service(ready_policy())))
        self.assertEqual(
            payment_status_text(self.service(ready_policy(allow_public_stars=False))),
            PAYMENT_UNAVAILABLE_MESSAGE,
        )

    async def test_tariff_page_says_stars_is_available(self):
        await cb_plus(self.cb("menu:plus"), self.state, self.db, CONFIG,
                      self.service(ready_policy()))

        text = self.msg.edit_text.await_args.args[0]
        self.assertIn("Telegram Stars", text)
        self.assertIn("подключим позже", text)
        self.assertNotIn(PAYMENT_UNAVAILABLE_MESSAGE, text)

    async def test_choosing_stars_creates_an_invoice_button(self):
        billing = self.service(ready_policy())
        checkout = CheckoutResult("pending", "order-1", "https://t.me/invoice/abc")
        with patch.object(billing, "prepare_payment", AsyncMock(return_value=checkout)) as prepare:
            await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db, billing)
            method = next(
                button.callback_data
                for row in self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
                for button in row
                if button.text == "Telegram Stars"
            )
            await cb_payment_method(self.cb(method), self.state, billing)

        prepare.assert_awaited_once()
        user_id, product_id, called_method = prepare.await_args.args[:3]
        self.assertEqual((user_id, product_id, called_method), (101, "viewer_plus", "stars"))
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn("Счёт готов", text)
        buttons = [
            button
            for row in self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(buttons[0].url, "https://t.me/invoice/abc")

    async def test_sbp_and_card_stay_closed_even_when_stars_is_open(self):
        billing = self.service(ready_policy())
        with patch.object(billing, "prepare_payment", AsyncMock()) as prepare:
            await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db, billing)
            buttons = [
                button
                for row in self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
                for button in row
            ]
            for label in ("СБП", "Банковская карта"):
                choice = next(b.callback_data for b in buttons if b.text == label)
                await cb_payment_method(self.cb(choice), self.state, billing)
                text = self.msg.edit_text.await_args.args[0]
                self.assertIn("Деньги не списаны", text)

        prepare.assert_not_awaited()

    async def test_already_active_subscription_is_explained_without_retry(self):
        await self.db.add_channel(101, "alpha")
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "stars-active", starts_at=now - 5, expires_at=now + 3600,
            issued_by=425785231, now=now,
        )
        billing = self.service(ready_policy())
        with patch.object(
            billing, "prepare_payment",
            AsyncMock(return_value=CheckoutResult("unavailable", reason_code="already_active")),
        ):
            await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db, billing)
            method = next(
                button.callback_data
                for row in self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
                for button in row
                if button.text == "Telegram Stars"
            )
            await cb_payment_method(self.cb(method), self.state, billing)

        self.assertIn("Подписка уже действует", self.msg.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
