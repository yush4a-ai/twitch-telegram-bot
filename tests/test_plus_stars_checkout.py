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
    cb_accept_terms,
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

    def buttons(self):
        return [
            button
            for row in self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]

    def pick_method(self, label):
        return next(b.callback_data for b in self.buttons() if b.text == label)

    async def accept_terms(self, billing):
        """Подтверждение условий: без него счёт не создаётся (D24, B4)."""
        accept = next(
            b.callback_data for b in self.buttons() if b.text == "Я принимаю условия"
        )
        await cb_accept_terms(self.cb(accept), self.state, billing)
        return accept

    def service(self, policy):
        return BillingService(self.db, self.provider, runtime_policy=policy,
                              terms_version="2026-10-01")

    def bank_service(self, *, ready=True):
        """Банковский сервис: счёт создаётся только когда канал допущен."""
        policy = ready_policy(allow_external_create=ready)
        return SimpleNamespace(
            runtime_policy=policy,
            public_callback_ready=lambda: ready,
            prepare_payment=AsyncMock(return_value=CheckoutResult(
                "pending", "a" * 32, "https://pay.platega.io/pay")),
        )

    async def test_bank_method_creates_a_real_checkout_in_the_bot(self):
        billing = self.service(ready_policy())
        bank = self.bank_service()
        await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db,
                     billing, None, CONFIG, bank)
        await cb_payment_method(self.cb(self.pick_method("СБП")), self.state, billing)
        accept = next(b.callback_data for b in self.buttons() if b.text == "Я принимаю условия")
        await cb_accept_terms(self.cb(accept), self.state, billing, None, CONFIG, bank)

        bank.prepare_payment.assert_awaited_once()
        args = bank.prepare_payment.await_args
        self.assertEqual((args.args[0], args.args[1], args.args[2]), (101, "viewer_plus", "sbp"))
        self.assertIn("buyer_name", args.kwargs)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn("Счёт готов", text)
        self.assertIn("СБП", text)
        url = next(button.url for button in self.buttons() if button.url)
        self.assertTrue(url.startswith("https://pay.platega.io/"))

    async def test_bank_method_without_a_service_stays_unavailable(self):
        billing = self.service(ready_policy())
        await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db,
                     billing, None, CONFIG, None)
        await cb_payment_method(self.cb(self.pick_method("СБП")), self.state, billing)
        accept = next(b.callback_data for b in self.buttons() if b.text == "Я принимаю условия")
        await cb_accept_terms(self.cb(accept), self.state, billing, None, CONFIG, None)

        text = self.msg.edit_text.await_args.args[0]
        self.assertIn("Оплата недоступна", text)

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
        # Банковский канал не подключён: страница говорит об этом прямо, а не
        # обещает будущее подключение.
        self.assertIn("СБП и банковская карта: пока недоступны.", text)
        self.assertNotIn("позже", text)
        self.assertNotIn(PAYMENT_UNAVAILABLE_MESSAGE, text)

    async def test_real_bot_with_the_owner_flag_is_ready(self):
        """Настоящий бот без внешнего транспорта: звёзды проводит сам Telegram.

        Реальный aiogram.Bot в тестах не создаётся (это запрещено изоляцией
        набора): подменяем класс на двойник, чтобы проверялась именно логика
        «транспорт это бот и владелец разрешил публичную оплату».
        """
        from bot import stars_provider
        from bot.stars_provider import TelegramStarsProvider

        class FakeTelegramTransport:
            async def send_invoice(self, **_fields):  # pragma: no cover - не вызывается
                raise AssertionError("в этом тесте счёт не отправляется")

            async def create_invoice_link(self, **_fields):  # pragma: no cover
                raise AssertionError("в этом тесте ссылка не создаётся")

        with patch.object(stars_provider, "Bot", FakeTelegramTransport):
            bot = FakeTelegramTransport()
            provider = TelegramStarsProvider(bot, ready_policy())
            self.assertTrue(provider.network_free)
            # Без разрешения владельца тот же бот денег не принимает.
            locked = TelegramStarsProvider(bot, ready_policy(allow_public_stars=False))
            self.assertFalse(locked.network_free)

    async def test_choosing_stars_creates_an_invoice_button(self):
        billing = self.service(ready_policy())
        checkout = CheckoutResult("pending", "order-1", "https://t.me/invoice/abc")
        with patch.object(billing, "prepare_payment", AsyncMock(return_value=checkout)) as prepare:
            await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db, billing)
            method = self.pick_method("Telegram Stars")
            await cb_payment_method(self.cb(method), self.state, billing)
            # Счёт не создаётся до явного подтверждения условий покупки.
            prepare.assert_not_awaited()
            self.assertIn("Я принимаю условия", [b.text for b in self.buttons()])
            await self.accept_terms(billing)

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
            for label in ("СБП", "Банковская карта"):
                with self.subTest(method=label):
                    await cb_buy(self.cb("plus:buy:viewer_plus"), self.state, self.db, billing)
                    choice = self.pick_method(label)
                    await cb_payment_method(self.cb(choice), self.state, billing)
                    await self.accept_terms(billing)
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
            method = self.pick_method("Telegram Stars")
            await cb_payment_method(self.cb(method), self.state, billing)
            await self.accept_terms(billing)

        self.assertIn("Подписка уже действует", self.msg.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
