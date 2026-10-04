"""Stars contracts: explicit fake XTR fixture; no Telegram invoice or money."""

import asyncio
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher
from aiogram.methods import AnswerPreCheckoutQuery
from aiogram.types import Chat, Message, PreCheckoutQuery, RefundedPayment, SuccessfulPayment, Update, User
from aiogram.client.session.base import BaseSession

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, Money
from bot.billing_provider import PaymentVerificationError
from bot.config import first_release_payment_policy
from bot.database import Database
from bot.handlers.payments import build_payment_router, on_precheckout, on_successful_payment, on_refunded_payment
from bot.plan_catalog import BillingRuntimePolicy, get_product
from bot.middlewares import ThrottleMiddleware
from bot.stars_provider import TelegramStarsProvider


PERIOD = AccessPeriodPolicy("30_days", "stars-fixture-v1")
POLICY = BillingRuntimePolicy("sandbox", True, False, True, True, True)


def fixture_product(product_id):
    # This is deliberately not an approved catalog price or period.
    return replace(get_product(product_id), xtr=Money(17, "XTR"),
                   period_rule=PERIOD.rule, period_rule_version=PERIOD.version)


class FakeSender:
    network_free = True
    bot_id = 12345

    def __init__(self):
        self.invoices = []
        self.refunds = []
        self.error = None

    async def send_invoice(self, **kwargs):
        self.invoices.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(message_id=70)

    async def refund_star_payment(self, **kwargs):
        self.refunds.append(kwargs)
        return True


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


class FakeTelegram:
    id = 12345

    def __init__(self, session):
        self.session = session

    async def answer_pre_checkout_query(self, **kwargs):
        return await self.session.make_request(self, AnswerPreCheckoutQuery(**kwargs))


def payment_message(payload, *, buyer=101, chat_id=None, refund=False, charge="stars_charge_1", **changes):
    values = dict(currency="XTR", total_amount=17, invoice_payload=payload,
                  telegram_payment_charge_id=charge, provider_payment_charge_id="")
    values.update(changes)
    payment = RefundedPayment(**values) if refund else SuccessfulPayment(**values)
    return Message(message_id=90, date=datetime.fromtimestamp(110, timezone.utc),
        chat=Chat(id=buyer if chat_id is None else chat_id, type="private"),
        from_user=User(id=buyer, is_bot=False, first_name="Покупатель"),
        **{"refunded_payment" if refund else "successful_payment": payment})


class StarsContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(str(Path(self.temp.name)/"stars.sqlite3"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        self.sender = FakeSender()
        self.provider = TelegramStarsProvider(self.sender, POLICY)
        self.service = self.make_service()
        self.addAsyncCleanup(self.service.close)

    def make_service(self, *, db=None, provider=None, policy=POLICY, catalog=fixture_product):
        return BillingService(db or self.db, provider or self.provider, runtime_policy=policy,
            access_policy=PERIOD, catalog=catalog, merchant_actor_ids=frozenset({999}), terms_version="fixture-terms-v1")

    async def create(self, product="viewer_plus", key="stars-request-1"):
        result = await self.service.prepare_payment(101, product, "stars", key, now=100)
        self.assertEqual(result.state, "pending")
        order = await self.db.get_billing_order(result.order_id)
        attempt = await (await self.db.conn.execute("SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?", (order.order_id,))).fetchone()
        payload = self.provider.invoice_payload(order.order_id, attempt[0])
        return order, payload

    async def test_xtr_tbd_disabled_runtime_and_client_paid_cannot_send_invoice_or_grant(self):
        for policy, catalog in ((first_release_payment_policy(), fixture_product), (POLICY, get_product)):
            with self.subTest(policy=policy.mode, xtr=catalog("viewer_plus").xtr):
                result = await self.make_service(policy=policy, catalog=catalog).prepare_payment(101, "viewer_plus", "stars", "disabled", now=100)
                self.assertEqual(result.state, "unavailable")
        self.assertEqual(self.sender.invoices, [])
        self.assertEqual((await (await self.db.conn.execute("SELECT count(*) FROM billing_orders")).fetchone())[0], 0)
        order, payload = await self.create()
        for event in (SimpleNamespace(status="paid", invoice_payload=payload), {"invoiceClosed": "paid", "payload": payload}):
            with self.subTest(event=type(event).__name__), self.assertRaises(PaymentVerificationError):
                self.provider.successful_payment_to_evidence(event, order, now=110)
        self.assertFalse(await self.db.has_viewer_plus(101, now=110))
        with self.assertRaises(PaymentVerificationError):
            TelegramStarsProvider(SimpleNamespace(network_free=False), POLICY).validate_precheckout(
                PreCheckoutQuery(id="q", from_user=User(id=101,is_bot=False,first_name="A"), currency="XTR", total_amount=17, invoice_payload=payload), order, now=110)

    async def test_invoice_is_single_xtr_nonrecurring_and_has_no_fake_charge_reference(self):
        order, payload = await self.create()
        invoice = self.sender.invoices[0]
        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(invoice["provider_token"], "")
        self.assertEqual(invoice["chat_id"], 101)
        self.assertEqual(invoice["payload"], payload)
        self.assertEqual(len(invoice["prices"]), 1)
        self.assertEqual(invoice["prices"][0].amount, 17)
        self.assertNotIn("subscription_period", invoice)
        self.assertNotIn("provider_data", invoice)
        self.assertTrue(invoice["start_parameter"])
        self.assertIsNone(order.checkout_reference)
        self.assertIsNone(order.checkout_url)
        self.assertFalse(self.provider.can_reconcile)
        self.assertFalse(hasattr(self.provider, "get_payment_status"))
        await self.service.reconcile_due(now=110)
        self.assertEqual(len(self.sender.invoices), 1)

    async def test_precheckout_checks_buyer_payload_amount_currency_expiry_and_binding_without_grant(self):
        order, payload = await self.create("streamer_plus")
        query = PreCheckoutQuery(id="q",from_user=User(id=101,is_bot=False,first_name="A"),currency="XTR",total_amount=17,invoice_payload=payload)
        self.assertTrue(self.provider.validate_precheckout(query, order, now=110).ok)
        bad = (query.model_copy(update={"from_user": User(id=202,is_bot=False,first_name="B")}),
               query.model_copy(update={"total_amount":18}), query.model_copy(update={"currency":"RUB"}),
               query.model_copy(update={"invoice_payload":payload+"x"}), query.model_copy(update={"total_amount": True}))
        for value in bad:
            with self.subTest(value=value.model_dump()):
                self.assertFalse(self.provider.validate_precheckout(value, order, now=110).ok)
        for invalid_order in (replace(order, beneficiary_telegram_user_id=202), replace(order, broadcaster_id="22"), replace(order,status="paid")):
            with self.subTest(order=invalid_order):
                self.assertFalse(self.provider.validate_precheckout(query, invalid_order, now=110).ok)
        self.assertFalse(self.provider.validate_precheckout(query, order, now=order.checkout_expires_at).ok)
        self.assertFalse(await self.db.has_viewer_plus(101, now=110))

    async def test_verified_stars_payment_is_buyer_scoped_unique_and_refund_tracked(self):
        order, payload = await self.create("streamer_plus")
        message = payment_message(payload)
        evidence = self.provider.successful_payment_to_evidence(message, order, now=110)
        first = await self.service.apply_payment_evidence(evidence, now=110)
        self.assertEqual(first.state, "applied")
        await self.service.apply_payment_evidence(evidence, now=999)
        grant = await (await self.db.conn.execute("SELECT beneficiary_telegram_user_id,starts_at,expires_at FROM entitlement_grants WHERE grant_id=?", (first.grant_id,))).fetchone()
        self.assertEqual(grant, (101, 110, 110+30*86400))
        self.assertTrue(await self.db.has_viewer_plus(101, now=120))
        self.assertFalse(await self.db.has_viewer_plus(202, now=120))
        updated = await self.db.get_billing_order(order.order_id)
        self.assertEqual(updated.checkout_reference, None)
        with self.assertRaises(PermissionError):
            await self.service.request_payment_refund(101, order.order_id, "refund-user", now=125)
        accepted = await self.service.request_payment_refund(999, order.order_id, "refund-merchant", now=125)
        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(self.sender.refunds, [{"user_id":101,"telegram_payment_charge_id":"stars_charge_1"}])
        self.assertTrue(await self.db.has_viewer_plus(101, now=126))
        refund = self.provider.refunded_payment_to_evidence(payment_message(payload,refund=True), updated, expected_charge="stars_charge_1", now=130)
        await self.service.apply_payment_evidence(refund, now=130)
        await self.service.apply_payment_evidence(evidence, now=135)
        self.assertFalse(await self.db.has_viewer_plus(101, now=140))
        self.assertEqual((await (await self.db.conn.execute("SELECT count(*) FROM entitlement_grants")).fetchone())[0],1)

    async def test_wrong_success_or_refund_and_reused_charge_cannot_grant_or_revoke_other_order(self):
        order, payload = await self.create()
        for value in (payment_message(payload,buyer=202), payment_message(payload,chat_id=202),
                      payment_message(payload,currency="RUB"),payment_message(payload,total_amount=18),
                      payment_message(payload+"x"),payment_message(payload,is_recurring=True),
                      payment_message(payload,subscription_expiration_date=999), payment_message(payload,charge="bad\ncharge")):
            with self.subTest(value=value.model_dump()), self.assertRaises(PaymentVerificationError):
                self.provider.successful_payment_to_evidence(value, order, now=110)
        evidence=self.provider.successful_payment_to_evidence(payment_message(payload), order, now=110)
        await self.service.apply_payment_evidence(evidence,now=110)
        for value in (payment_message(payload,refund=True,buyer=202),payment_message(payload,refund=True,charge="foreign")):
            with self.subTest(value=value.model_dump()), self.assertRaises(PaymentVerificationError):
                self.provider.refunded_payment_to_evidence(value,order,expected_charge="stars_charge_1",now=120)
        await self.db.link_streamer_identity(202,"22","beta",verified_at=50)
        other = await self.service.prepare_payment(202,"viewer_plus","stars","other-buyer",now=120)
        other_order=await self.db.get_billing_order(other.order_id)
        other_attempt=(await (await self.db.conn.execute("SELECT attempt_id FROM billing_payment_attempts WHERE order_id=?",(other.order_id,))).fetchone())[0]
        foreign=self.provider.successful_payment_to_evidence(payment_message(self.provider.invoice_payload(other.order_id,other_attempt),buyer=202),other_order,now=130)
        self.assertEqual((await self.service.apply_payment_evidence(foreign,now=130)).state,"manual_review")
        self.assertFalse(await self.db.has_viewer_plus(202,now=140))
        self.assertTrue(await self.db.has_viewer_plus(101,now=140))

    async def test_unknown_invoice_is_not_resent_after_reopen_or_new_key(self):
        self.sender.error=TimeoutError("no verified outcome")
        result=await self.service.prepare_payment(101,"viewer_plus","stars","unknown",now=100)
        self.assertEqual(result.state,"creation_unknown")
        otherdb=Database(self.db._path)
        await otherdb.connect()
        self.addAsyncCleanup(otherdb.close)
        reopened=self.make_service(db=otherdb)
        replay=await reopened.prepare_payment(101,"viewer_plus","stars","unknown",now=101)
        self.assertEqual(replay.order_id,result.order_id)
        blocked=await reopened.prepare_payment(101,"viewer_plus","stars","new-key",now=101)
        self.assertEqual(blocked.reason_code,"payment_in_progress")
        self.assertEqual(len(self.sender.invoices),1)

    async def test_handlers_answer_precheckout_bounded_and_use_common_apply_without_client_flags(self):
        order,payload=await self.create()
        session=FakeSession()
        bot=FakeTelegram(session)
        self.addAsyncCleanup(bot.session.close)
        query=PreCheckoutQuery(id="q",from_user=User(id=101,is_bot=False,first_name="A"),currency="XTR",total_amount=17,invoice_payload=payload)
        start=time.monotonic()
        await on_precheckout(query,self.service,bot,now=110)
        self.assertLess(time.monotonic()-start,10)
        self.assertTrue(session.calls[-1].ok)
        self.assertFalse(await self.db.has_viewer_plus(101,now=110))
        await on_successful_payment(payment_message(payload),self.service,now=110)
        self.assertTrue(await self.db.has_viewer_plus(101,now=111))
        await on_refunded_payment(payment_message(payload,refund=True),self.service,now=120)
        self.assertFalse(await self.db.has_viewer_plus(101,now=121))
        dp=Dispatcher()
        dp.include_router(build_payment_router())
        disabled=BillingService(self.db,runtime_policy=first_release_payment_policy())
        await dp.feed_update(bot,Update(update_id=1,pre_checkout_query=query),billing_service=disabled)
        self.assertFalse(session.calls[-1].ok)
        self.assertFalse(await self.db.has_viewer_plus(101,now=121))
        self.assertEqual(len(self.sender.invoices),1)

    async def test_payment_updates_are_not_dropped_by_normal_action_throttle(self):
        order,payload=await self.create()
        throttle=ThrottleMiddleware()
        delegate=AsyncMock()
        user=User(id=101,is_bot=False,first_name="A")
        ordinary=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=101,type="private"),from_user=user,text="/start")
        await throttle(delegate,ordinary,{"event_from_user":user})
        await throttle(delegate,payment_message(payload),{"event_from_user":user})
        await throttle(delegate,payment_message(payload,refund=True),{"event_from_user":user})
        self.assertEqual(delegate.await_count,3)

    async def test_credentials_and_environment_flags_cannot_enable_first_release_money(self):
        with patch.dict("os.environ",{"PLATEGA_SECRET":"fixture", "STARS_ENABLED":"1", "BILLING_MODE":"sandbox"}):
            self.assertEqual(first_release_payment_policy(),BillingRuntimePolicy())

    async def test_ingress_crash_replay_keeps_one_real_ledger_grant_and_refund(self):
        from bot.telegram_replay import ReplayDispatcher
        order, payload = await self.create()
        bot = FakeTelegram(FakeSession())
        def dispatcher():
            dp = ReplayDispatcher()
            dp['db'] = self.db
            dp['billing_service'] = self.service
            dp.include_router(build_payment_router())
            return dp
        success = Update(update_id=501, message=payment_message(payload))
        with patch('bot.handlers.payments.time.time', return_value=110):
            with patch.object(self.db, 'finish_telegram_update', side_effect=RuntimeError('crash after ledger')):
                with self.assertRaises(RuntimeError):
                    await dispatcher()._process_update(bot, success)
            dp = dispatcher()
            await dp.recover_pending(bot)
            await dp._process_update(bot, success)
            self.assertTrue(await self.db.has_viewer_plus(101, now=111))
            self.assertEqual((await (await self.db.conn.execute('SELECT count(*) FROM entitlement_grants')).fetchone())[0], 1)
            self.assertEqual((await (await self.db.conn.execute('SELECT count(*) FROM billing_orders')).fetchone())[0], 1)
        refund = Update(update_id=502, message=payment_message(payload, refund=True))
        with patch('bot.handlers.payments.time.time', return_value=120):
            await dp._process_update(bot, refund)
            await dp._process_update(bot, refund)
            self.assertFalse(await self.db.has_viewer_plus(101, now=121))
            self.assertEqual((await (await self.db.conn.execute('SELECT count(*) FROM entitlement_grants')).fetchone())[0], 1)
        self.assertEqual(len(self.sender.invoices), 1)  # only explicit sandbox fixture preparation


if __name__ == "__main__":
    unittest.main()
