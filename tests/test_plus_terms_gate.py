"""Условия покупки в боте: документы, поддержка и подтверждение до счёта.

Правило находок D24 и B4: перед покупкой человек видит тариф, цену, период,
состав, поддержку и доступные документы, а счёт создаётся только после явного
подтверждения условий. Без подтверждения в базе не появляется заказ.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy
from bot.database import Database
from bot.handlers.telegram_plus import cb_accept_terms, cb_buy, cb_payment_method, cb_plus
from bot.handlers.telegram_terms import (
    cb_plus_info,
    info_data,
    on_terms,
    terms_row,
)
from bot.plan_catalog import (
    PLUS_PERIOD_RULE,
    PLUS_PERIOD_VERSION,
    PLUS_TERMS_VERSION,
    BillingRuntimePolicy,
)
from bot.stars_provider import TelegramStarsProvider
from tests.test_telegram_navigation import CONFIG, message

# Реквизиты оператора и контакт поддержки: без них общий гейт документов
# намеренно не публикует политику, соглашение и оплату.
OWNER_INPUTS = dict(
    support_username="verified_support_person",
    support_email="support@example.com",
    legal_operator="Оператор из локальной проверки",
    legal_operator_address="Адрес из локальной проверки",
    legal_retention="Сроки хранения из локальной проверки.",
    legal_refund_policy="Правило возвратов из локальной проверки.",
    legal_chargeback_policy="Правило сверки из локальной проверки.",
)
READY_CONFIG = SimpleNamespace(**vars(CONFIG), **OWNER_INPUTS)
STARS_POLICY = BillingRuntimePolicy(
    mode="sandbox", target_verified=True, allow_invoice=True,
    period_approved=True, refund_policy_approved=True, allow_public_stars=True,
)
ACCESS_PERIOD = AccessPeriodPolicy(PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION)


class LocalInvoiceSender:
    """Локальный отправитель счёта: помнит вызовы и не ходит в сеть."""

    network_free = True

    def __init__(self):
        self.invoices = []

    async def send_invoice(self, **fields):
        self.invoices.append(fields)
        return SimpleNamespace(message_id=70)


class PlusTermsGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=999, chat_id=101, user_id=101)
        )
        self.msg = message()
        self.sender = LocalInvoiceSender()
        self.service = BillingService(
            self.db, TelegramStarsProvider(self.sender, STARS_POLICY),
            runtime_policy=STARS_POLICY, access_policy=ACCESS_PERIOD,
            terms_version=PLUS_TERMS_VERSION,
        )
        self.addAsyncCleanup(self.service.close)

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

    def rows(self):
        return self.msg.edit_text.await_args.kwargs["reply_markup"].inline_keyboard

    async def count(self, table):
        cursor = await self.db.conn.execute("SELECT count(*) FROM " + table)
        return (await cursor.fetchone())[0]

    async def open_methods(self, product="viewer_plus", billing=None, config=READY_CONFIG):
        await cb_buy(
            self.cb("plus:buy:" + product), self.state, self.db,
            billing_service=billing or self.service, config=config,
        )
        return next(
            button.callback_data for button in self.buttons() if button.text == "Telegram Stars"
        )

    async def accept_button(self, billing=None):
        await cb_payment_method(
            self.cb(await self.open_methods(billing=billing)), self.state,
            billing or self.service, READY_CONFIG, self.db,
        )
        return next(
            button.callback_data for button in self.buttons() if button.text == "Я принимаю условия"
        )

    async def test_terms_command_is_registered_beside_paysupport(self):
        from bot.handlers.payments import build_payment_router
        from main import _private_bot_commands

        handlers = [handler.callback for handler in build_payment_router().message.handlers]
        self.assertIn(on_terms, handlers)
        commands = {
            command.command
            for command in _private_bot_commands([], owner=False, growth_enabled=False)
        }
        self.assertIn("terms", commands)

    async def test_terms_screen_keeps_price_period_and_ready_documents_only(self):
        answer = SimpleNamespace(answer=AsyncMock())
        await on_terms(answer, CONFIG)
        text = answer.answer.call_args.args[0]
        keyboard = answer.answer.call_args.kwargs["reply_markup"]
        for fact in ("150 ₽", "300 ₽", "месяц", "Автопродление"):
            self.assertIn(fact, text)
        # Контакт не выдуман: без настроек поддержка честно об этом говорит.
        self.assertNotIn("t.me/", text)
        links = {
            button.url: button.text
            for row in keyboard.inline_keyboard
            for button in row
            if button.url
        }
        # Без реквизитов оператора гейт пропускает только тарифы.
        self.assertEqual(links, {CONFIG.oauth_public_base_url + "/app/legal/tariffs": "Тарифы"})
        self.assertTrue(
            any(button.text == "Поддержка" for row in keyboard.inline_keyboard for button in row)
        )

        await on_terms(answer, READY_CONFIG)
        keyboard = answer.answer.call_args.kwargs["reply_markup"]
        titles = {
            button.text for row in keyboard.inline_keyboard for button in row if button.url
        }
        self.assertTrue({"Политика конфиденциальности", "Пользовательское соглашение"} <= titles)

    async def test_tariff_and_method_screens_add_exactly_one_info_row(self):
        await cb_plus(self.cb("menu:plus"), self.state, self.db, READY_CONFIG)
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual([button.text for button in rows[-2]], ["Условия", "Документы", "Поддержка"])
        self.assertEqual(rows[-1][0].callback_data, "menu:more")

        await self.open_methods()
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual([button.text for button in rows[-2]], ["Условия", "Документы", "Поддержка"])
        self.assertEqual(
            next(button.text for button in rows[0] if button.callback_data.endswith(":stars")),
            "Telegram Stars",
        )

    async def test_documents_button_is_hidden_when_no_document_can_be_opened(self):
        locked = SimpleNamespace(**{**vars(READY_CONFIG), "oauth_public_base_url": ""})
        labels = [button.text for button in terms_row(locked)]
        self.assertNotIn("Документы", labels)
        # Документ проходит гейт, но открыть его нечем: интерфейс молчит о ссылке.
        answer = SimpleNamespace(answer=AsyncMock())
        await on_terms(answer, locked)
        keyboard = answer.answer.call_args.kwargs["reply_markup"]
        self.assertFalse(
            any(button.url for row in keyboard.inline_keyboard for button in row)
        )

    async def test_documents_screen_lists_only_ready_documents(self):
        await cb_plus_info(self.cb(info_data("docs", "plus")), self.state, CONFIG, self.db)
        text = self.msg.edit_text.await_args.args[0]
        links = {
            button.text: button.url for row in self.rows() for button in row if button.url
        }
        self.assertEqual(links, {"Тарифы": CONFIG.oauth_public_base_url + "/app/legal/tariffs"})
        self.assertNotIn("Политика конфиденциальности", links)
        self.assertIn("готовит", text)

    async def test_support_screen_uses_the_same_contact_as_paysupport(self):
        from bot.handlers.payments import on_payment_support

        await cb_plus_info(self.cb(info_data("support", "plus")), self.state, CONFIG, self.db)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn("Контакт поддержки пока не указан", text)
        self.assertNotIn("t.me/", text)

        answer = SimpleNamespace(answer=AsyncMock())
        await on_payment_support(answer, READY_CONFIG)
        self.assertIn("https://t.me/verified_support_person", answer.answer.call_args.args[0])
        await cb_plus_info(
            self.cb(info_data("support", "plus")), self.state, READY_CONFIG, self.db
        )
        configured = self.msg.edit_text.await_args.args[0]
        self.assertIn("https://t.me/verified_support_person", configured)
        self.assertIn("support@example.com", configured)

    async def test_no_order_without_terms_acceptance(self):
        await self.open_methods()
        self.assertEqual(await self.count("billing_orders"), 0)

        method = next(
            button.callback_data for button in self.buttons() if button.text == "Telegram Stars"
        )
        await cb_payment_method(self.cb(method), self.state, self.service, READY_CONFIG, self.db)
        self.assertEqual(await self.count("billing_orders"), 0)
        self.assertEqual(await self.count("billing_payment_attempts"), 0)
        self.assertEqual(self.sender.invoices, [])
        confirmation = self.msg.edit_text.await_args.args[0]
        self.assertIn("тариф", confirmation.lower())
        self.assertIn("Автопродление выключено", confirmation)
        self.assertIn("Я принимаю условия", [button.text for button in self.buttons()])

    async def test_acceptance_creates_one_order_with_the_frozen_terms_version(self):
        accept = await self.accept_button()
        self.assertTrue(accept.startswith("plus:agree:"))
        nonce = accept.split(":")[2]
        await cb_accept_terms(self.cb(accept), self.state, self.service, self.db, READY_CONFIG)

        self.assertEqual(await self.count("billing_orders"), 1)
        self.assertEqual(await self.count("billing_payment_attempts"), 1)
        self.assertEqual(len(self.sender.invoices), 1)
        order = await self.db.get_billing_order_by_request_key(nonce)
        self.assertIsNotNone(order)
        self.assertEqual(order.terms_version, PLUS_TERMS_VERSION)
        self.assertEqual((order.plan, order.method, order.currency), ("viewer_plus", "stars", "XTR"))
        self.assertIn("Счёт отправлен", self.msg.edit_text.await_args.args[0])

    async def test_stale_or_replayed_acceptance_creates_nothing(self):
        accept = await self.accept_button()
        stale = self.cb("plus:agree:" + "0" * 16)
        await cb_accept_terms(stale, self.state, self.service, self.db, READY_CONFIG)
        self.assertEqual(await self.count("billing_orders"), 0)
        self.assertEqual(self.sender.invoices, [])
        self.assertIn("устарел", stale.answer.await_args.args[0])

        await cb_accept_terms(self.cb(accept), self.state, self.service, self.db, READY_CONFIG)
        self.assertEqual(await self.count("billing_orders"), 1)
        # Повтор того же подтверждения не создаёт второй заказ и второй счёт:
        # состояние покупки уже закрыто.
        replay = self.cb(accept)
        await cb_accept_terms(replay, self.state, self.service, self.db, READY_CONFIG)
        self.assertIn("устарел", replay.answer.await_args.args[0])
        self.assertEqual(await self.count("billing_orders"), 1)
        self.assertEqual(len(self.sender.invoices), 1)

    async def test_sbp_and_card_stay_closed_after_acceptance(self):
        for label in ("СБП", "Банковская карта"):
            with self.subTest(method=label):
                await cb_buy(
                    self.cb("plus:buy:viewer_plus"), self.state, self.db,
                    billing_service=self.service, config=READY_CONFIG,
                )
                choice = next(
                    button.callback_data for button in self.buttons() if button.text == label
                )
                await cb_payment_method(
                    self.cb(choice), self.state, self.service, READY_CONFIG, self.db
                )
                accept = next(
                    button.callback_data
                    for button in self.buttons()
                    if button.text == "Я принимаю условия"
                )
                await cb_accept_terms(
                    self.cb(accept), self.state, self.service, self.db, READY_CONFIG
                )
                text = self.msg.edit_text.await_args.args[0]
                self.assertIn("Деньги не списаны", text)
        self.assertEqual(await self.count("billing_orders"), 0)
        self.assertEqual(self.sender.invoices, [])


if __name__ == "__main__":
    unittest.main()
