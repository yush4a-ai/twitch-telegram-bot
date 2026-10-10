"""Paid lifecycle contracts in disposable SQLite with no payment network."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from bot.billing import BillingService
from bot.billing_models import AccessPeriodPolicy, BillingSubject, Money, PaymentAttempt, ServerOrderSnapshot, VerifiedPaymentEvidence
from bot.billing_provider import CheckoutSession, PaymentCreationRejected, ProviderNotice, ProviderRateLimited, RefundOutcome
from bot.billing_store import BillingStore
from bot.database import Database
from bot.plan_catalog import BillingRuntimePolicy, get_product


POLICY = BillingRuntimePolicy("sandbox", True, True, False, True, True)
PERIOD = AccessPeriodPolicy("30_days", "fixture-only-v1")
TX = "22222222-2222-4222-8222-222222222222"


def product(product_id):
    return replace(get_product(product_id), period_rule=PERIOD.rule, period_rule_version=PERIOD.version)


class FakeProvider:
    provider_id = "platega"
    network_free = True

    def __init__(self):
        self.calls = []
        self.get_calls = []
        self.refund_calls = []
        self.evidence = {}
        self.before_create_response = None
        self.create_error = None
        self.refund_result = RefundOutcome("accepted", TX)
        self.transaction_id = TX
        self.before_get = None

    async def create_payment(self, snapshot, attempt_id):
        self.calls.append((snapshot, attempt_id))
        self.evidence[self.transaction_id] = VerifiedPaymentEvidence("platega", self.transaction_id, snapshot.order_id, attempt_id,
            snapshot.money, snapshot.method, "confirmed", "CONFIRMED", 101)
        if self.before_create_response:
            await self.before_create_response(snapshot, attempt_id)
        if self.create_error:
            raise self.create_error
        return CheckoutSession(snapshot.order_id, self.transaction_id, "https://pay.platega.io/fixture", "pending", 1000)

    async def get_payment_status(self, reference):
        self.get_calls.append(reference)
        if self.before_get:
            await self.before_get()
        value = self.evidence.get(reference, TimeoutError())
        if isinstance(value, BaseException):
            raise value
        return value

    def handle_callback(self, body, headers):
        value = json.loads(body)
        return ProviderNotice("platega", value["id"], value["status"], value["id"]+":"+value["status"])

    async def refund_payment(self, reference, request_id):
        self.refund_calls.append((reference, request_id))
        if isinstance(self.refund_result, BaseException):
            raise self.refund_result
        return self.refund_result


class PaymentFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "payments.db"
        self.db = Database(str(self.path))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=1)
        self.provider = FakeProvider()
        self.service = self.make_service(self.db)

    def make_service(self, db, *, policy=POLICY, period=PERIOD):
        return BillingService(db, self.provider, runtime_policy=policy, access_policy=period,
                              catalog=product, merchant_actor_ids=frozenset({999}), terms_version="fixture-terms-v1")

    async def count(self, table):
        return (await (await self.db.conn.execute("SELECT count(*) FROM " + table)).fetchone())[0]

    async def checkout(self, request="request-1", plan="streamer_plus"):
        result = await self.service.prepare_payment(101, plan, "sbp", request, now=100)
        self.assertEqual(result.state, "pending")
        return result


class PaymentLifecycleV3Tests(PaymentFixture):

    async def test_default_off_catalog_unknown_month_and_active_product_do_not_create(self):
        service = BillingService(self.db, self.provider)
        result = await service.prepare_payment(101, "viewer_plus", "sbp", "off", now=100)
        self.assertEqual((result.state, result.order_id, result.hosted_url), ("unavailable", None, None))
        unknown = BillingService(self.db, self.provider, runtime_policy=POLICY, access_policy=PERIOD)
        self.assertEqual((await unknown.prepare_payment(101, "viewer_plus", "sbp", "unknown", now=100)).state, "unavailable")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(await self.count("billing_orders"), 0)
        await self.db.issue_test_viewer_plus(101, "active", starts_at=90, expires_at=200, issued_by=999, now=90)
        self.assertEqual((await self.service.prepare_payment(101, "viewer_plus", "sbp", "renewal", now=100)).reason_code, "already_active")
        with self.assertRaises(PermissionError):
            await self.service.prepare_payment(202, "streamer_plus", "sbp", "foreign", now=100)
        self.assertEqual(self.provider.calls, [])

    async def test_rejected_creation_closes_the_order_instead_of_parking_it(self):
        """Явный отказ провайдера: заказ закрыт, повторная покупка не блокируется."""
        self.provider.create_error = PaymentCreationRejected("method_unavailable")
        result = await self.service.prepare_payment(101, "viewer_plus", "sbp", "rejected-1", now=100)

        self.assertEqual((result.state, result.reason_code), ("unavailable", "method_unavailable"))
        order = await self.db.get_billing_order(result.order_id)
        self.assertEqual((order.status, order.financial_status), ("cancelled", "canceled"))
        attempt = await (await self.db.conn.execute(
            "SELECT state FROM billing_payment_attempts WHERE order_id=?", (result.order_id,))).fetchone()
        self.assertEqual(attempt[0], "failed")
        self.assertEqual(await self.count("entitlement_grants"), 0)

    async def test_callback_before_create_response_and_duplicate_apply_have_one_grant(self):
        async def callback(snapshot, attempt_id):
            receipt = await self.service.accept_provider_notice(json.dumps({"id": TX, "status": "CONFIRMED"}).encode(), {}, now=100)
            self.assertTrue(receipt.durable)
            summary = await self.service.reconcile_due(now=100, limit=10)
            self.assertEqual(summary.applied, 1)
        self.provider.before_create_response = callback
        checkout = await self.checkout()
        other = Database(str(self.path)); await other.connect(); self.addAsyncCleanup(other.close)
        second = self.make_service(other)
        evidence = self.provider.evidence[TX]
        applied = await asyncio.gather(self.service.apply_payment_evidence(evidence, now=102),
                                       second.apply_payment_evidence(evidence, now=103))
        self.assertEqual({result.state for result in applied}, {"already_applied"})
        self.assertEqual(await self.count("billing_provider_facts"), 1)
        self.assertEqual(await self.count("entitlement_grants"), 1)
        order = await self.db.get_billing_order(checkout.order_id)
        self.assertEqual((order.access_starts_at, order.access_expires_at), (100, 100 + 30*86400))
        self.assertTrue(await self.db.has_viewer_plus(101, now=110))
        self.assertFalse(await self.db.has_viewer_plus(202, now=110))
        self.assertFalse(await self.db.has_viewer_plus(999, now=110))
        receipt = await second.accept_provider_notice(json.dumps({"id": TX, "status": "CONFIRMED"}).encode(), {}, now=105)
        self.assertTrue(receipt.duplicate)

    async def test_chargeback_unknown_and_refund_do_not_restore_or_revoke_wrong_access(self):
        await self.checkout()
        evidence = self.provider.evidence[TX]
        first = await self.service.apply_payment_evidence(replace(evidence, status="refunded", raw_status="CHARGEBACKED"), now=110)
        self.assertEqual(first.state, "recorded")
        self.assertEqual((await self.service.apply_payment_evidence(evidence, now=120)).state, "already_applied")
        self.assertEqual(await self.count("entitlement_grants"), 0)
        self.assertEqual((await self.db.get_billing_order(evidence.order_id)).financial_status, "refunded")

    async def test_late_confirmed_and_refund_preserve_independent_viewer(self):
        checkout = await self.checkout()
        independent = await self.db.issue_test_viewer_plus(101, "independent", starts_at=90,
            expires_at=4000, issued_by=999, now=90)
        evidence = self.provider.evidence[TX]
        result = await self.service.apply_payment_evidence(evidence, now=2000)
        self.assertEqual(result.state, "applied", "invoice expiry must not lose a verified payment")
        with self.assertRaises(PermissionError):
            await self.service.request_payment_refund(101, checkout.order_id, "accepted", now=2100)
        self.assertEqual((await self.service.request_payment_refund(999, checkout.order_id, "accepted", now=2100)).state, "accepted")
        self.provider.refund_result = RefundOutcome("manual_control_required", TX)
        # Деньги уже вернули: повтор отвечает тем же исходом и второй раз не списывает.
        self.assertEqual((await self.service.request_payment_refund(999, checkout.order_id, "second-key", now=2100)).state, "refunded")
        self.assertEqual(len(self.provider.refund_calls), 1)
        # Подтверждённый возврат снял доступ сразу, не дожидаясь сообщения Telegram.
        self.assertFalse(await self.db.has_streamer_plus(101, now=2101))
        refunded = replace(evidence, status="refunded", raw_status="CHARGEBACKED")
        # Возврат уже применён по подтверждению провайдера: повтор идемпотентен.
        self.assertEqual((await self.service.apply_payment_evidence(refunded, now=2200)).state, "already_applied")
        self.assertFalse(await self.db.has_streamer_plus(101, now=2201))
        self.assertTrue(await self.db.has_viewer_plus(101, now=2201))
        self.assertEqual((await self.db.get_current_plus_grant(101, "viewer_plus", now=2201))[1], 4000)
        self.assertEqual((await self.service.apply_payment_evidence(evidence, now=2300)).state, "already_applied")

    async def test_creation_unknown_and_refund_unknown_survive_reopen_without_repeat_post(self):
        self.provider.create_error = TimeoutError()
        result = await self.service.prepare_payment(101, "streamer_plus", "sbp", "unknown", now=100)
        self.assertEqual(result.state, "creation_unknown")
        other = Database(str(self.path)); await other.connect(); self.addAsyncCleanup(other.close)
        second = self.make_service(other)
        result2 = await second.prepare_payment(101, "streamer_plus", "sbp", "unknown", now=120)
        self.assertEqual((result2.state, result2.order_id), ("creation_unknown", result.order_id))
        self.assertEqual(len(self.provider.calls), 1)
        evidence = self.provider.evidence[TX]
        await second.apply_payment_evidence(evidence, now=125)
        self.provider.refund_result = TimeoutError()
        self.assertEqual((await second.request_payment_refund(999, result.order_id, "refund-unknown", now=130)).state, "unknown")
        self.assertEqual((await self.service.request_payment_refund(999, result.order_id, "refund-unknown", now=140)).state, "unknown")
        self.assertEqual(len(self.provider.refund_calls), 1)

    async def test_wrong_money_method_subject_and_unapproved_policy_cannot_grant(self):
        await self.checkout()
        evidence = self.provider.evidence[TX]
        for bad in (replace(evidence, money=Money(15000, "RUB")), replace(evidence, method="bank_card"),
                    replace(evidence, order_id="c"*32), replace(evidence, provider="foreign")):
            with self.subTest(bad=bad):
                result = await self.service.apply_payment_evidence(bad, now=110)
                self.assertEqual(result.state, "manual_review")
                self.assertEqual(await self.count("entitlement_grants"), 0)
        policy_off = self.make_service(self.db, policy=BillingRuntimePolicy(), period=None)
        result = await policy_off.apply_payment_evidence(evidence, now=120)
        # Оплата уже подтверждена провайдером: человек заплатил, поэтому доступ
        # выдаётся по замороженным условиям заказа. Неутверждённая политика
        # запрещает СОЗДАВАТЬ новые счёта (prepare_payment), но не отменяет
        # уже случившуюся оплату.
        self.assertEqual(result.state, "applied")
        self.assertEqual(await self.count("entitlement_grants"), 1)
        self.assertEqual(await self.count("billing_provider_facts"), 1)

    async def test_retry_budget_429_and_concurrency_do_not_grant_or_retry_early(self):
        await self.checkout()
        self.provider.evidence[TX] = ProviderRateLimited(120)
        summary = await self.service.reconcile_due(now=101, limit=10)
        self.assertEqual(summary.attempted, 1)
        self.assertEqual((await self.service.reconcile_due(now=220, limit=10)).attempted, 0)
        self.provider.evidence[TX] = TimeoutError()
        for at in (221, 1000, 2000, 3000, 4000, 5000, 6000):
            await self.service.reconcile_due(now=at, limit=10)
        attempts = await (await self.db.conn.execute("SELECT state,reconcile_count FROM billing_payment_attempts")).fetchall()
        self.assertEqual(attempts, [("manual_review", 8)])
        self.assertEqual(len(self.provider.get_calls), 8)
        self.assertEqual(await self.count("entitlement_grants"), 0)

    async def test_unknown_creation_is_retried_by_reconciliation(self):
        """Неизвестный исход создания с известной ссылкой обязан перепроверяться."""
        await self.checkout()
        await self.service._mark_creation_unknown("attempt-missing")
        await self.db.conn.execute(
            "UPDATE billing_payment_attempts SET next_reconcile_at=NULL,state='creation_unknown'")
        await self.db.conn.commit()

        summary = await self.service.reconcile_due(now=101, limit=10)
        self.assertEqual(summary.attempted, 1)
        self.assertEqual(await self.count("entitlement_grants"), 1)

    async def test_manual_review_is_rechecked_and_the_paid_order_is_granted_later(self):
        """Ручная проверка не тупик: когда сверка сходится позже, доступ выдаётся."""
        await self.checkout()
        good = self.provider.evidence[TX]
        self.provider.evidence[TX] = TimeoutError()
        at = 101
        for _ in range(12):
            await self.service.reconcile_due(now=at, limit=10)
            state, count, next_at = await (await self.db.conn.execute(
                "SELECT state,reconcile_count,next_reconcile_at FROM billing_payment_attempts")).fetchone()
            if state == "manual_review":
                break
            at = max(at + 1, int(next_at or 0)) + 1
        self.assertEqual((state, count), ("manual_review", 8))
        self.assertIsNotNone(next_at)
        self.assertEqual(await self.count("entitlement_grants"), 0)
        # Владелец должен видеть застрявшую оплату, а не узнавать о ней случайно.
        self.assertEqual((await self.db.billing_attention(now=at))["manual_review"], 1)

        self.provider.evidence[TX] = good
        summary = await self.service.reconcile_due(now=next_at, limit=10)
        self.assertEqual(summary.attempted, 1)
        self.assertEqual(await self.count("entitlement_grants"), 1)
        self.assertEqual((await self.db.get_billing_order(good.order_id)).status, "paid")

    async def test_apply_transaction_failure_rolls_back_fact_grant_order_and_reopens(self):
        await self.checkout()
        await self.db.conn.execute("CREATE TEMP TRIGGER fail_paid BEFORE INSERT ON entitlement_grants BEGIN SELECT RAISE(ABORT,'paid failure'); END")
        with self.assertRaisesRegex(Exception, "paid failure"):
            await self.service.apply_payment_evidence(self.provider.evidence[TX], now=110)
        self.assertEqual(await self.count("billing_provider_facts"), 0)
        self.assertEqual(await self.count("entitlement_grants"), 0)
        self.assertEqual((await self.db.get_billing_order(self.provider.evidence[TX].order_id)).status, "pending")
        await self.db.conn.execute("DROP TRIGGER fail_paid")
        self.assertEqual((await self.service.apply_payment_evidence(self.provider.evidence[TX], now=120)).state, "applied")

    async def test_two_connections_different_keys_have_one_create_and_reload_keeps_hosted_link(self):
        other = Database(str(self.path)); await other.connect(); self.addAsyncCleanup(other.close)
        second = self.make_service(other)
        results = await asyncio.gather(
            self.service.prepare_payment(101, "streamer_plus", "sbp", "first-key", now=100),
            second.prepare_payment(101, "streamer_plus", "bank_card", "second-key", now=100))
        self.assertEqual(sorted(result.state for result in results), ["pending", "unavailable"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(await self.count("billing_orders"), 1)
        first_request = "first-key" if results[0].state == "pending" else "second-key"
        first_method = "sbp" if results[0].state == "pending" else "bank_card"
        replay = await second.prepare_payment(101, "streamer_plus", first_method, first_request, now=120)
        self.assertEqual(replay.hosted_url, "https://pay.platega.io/fixture")
        self.assertEqual(len(self.provider.calls), 1)

    async def test_cancellation_persists_unknown_and_worker_releases_lease(self):
        self.provider.create_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service.prepare_payment(101, "streamer_plus", "sbp", "cancel-create", now=100)
        self.assertEqual((await (await self.db.conn.execute("SELECT state FROM billing_payment_attempts")).fetchone())[0], "creation_unknown")
        await self.service.apply_payment_evidence(self.provider.evidence[TX], now=110)
        self.provider.refund_result = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.service.request_payment_refund(999, self.provider.evidence[TX].order_id, "cancel-refund", now=120)
        self.assertEqual((await (await self.db.conn.execute("SELECT state FROM billing_payment_refunds")).fetchone())[0], "unknown")

    async def test_extra_external_payment_is_kept_without_second_grant_or_wrong_refund(self):
        await self.checkout()
        evidence = self.provider.evidence[TX]
        applied = await self.service.apply_payment_evidence(evidence, now=110)
        extra = replace(evidence, transaction_id="33333333-3333-4333-8333-333333333333")
        self.assertEqual((await self.service.apply_payment_evidence(extra, now=120)).state, "manual_review")
        self.assertEqual(await self.count("billing_provider_facts"), 2)
        self.assertEqual(await self.count("entitlement_grants"), 1)
        self.assertEqual((await self.service.apply_payment_evidence(replace(extra, status="refunded", raw_status="CHARGEBACKED"), now=130)).state, "manual_review")
        self.assertTrue(await self.db.has_streamer_plus(101, now=140))
        self.assertEqual((await self.db.get_billing_order(evidence.order_id)).grant_id, applied.grant_id)

    async def test_stale_pending_or_canceled_cannot_replace_confirmed_fact(self):
        await self.checkout()
        evidence = self.provider.evidence[TX]
        first = await self.service.apply_payment_evidence(evidence, now=110)
        self.assertEqual((await self.service.apply_payment_evidence(replace(evidence, status="pending", raw_status="PENDING"), now=120)).state, "already_applied")
        self.assertEqual((await self.service.apply_payment_evidence(replace(evidence, status="canceled", raw_status="CANCELED"), now=130)).state, "manual_review")
        fact = await (await self.db.conn.execute("SELECT status FROM billing_provider_facts WHERE transaction_id=?", (TX,))).fetchone()
        self.assertEqual(fact, ("confirmed",))
        order = await self.db.get_billing_order(evidence.order_id)
        # Месяц отсчитывается от даты платежа, а не от момента обработки: иначе
        # задержка подтверждения (или восстановление потерянной оплаты) съедала
        # бы оплаченные дни.
        self.assertEqual(
            (order.grant_id, order.access_starts_at),
            (first.grant_id, evidence.observed_at),
        )
        self.assertTrue(await self.db.has_streamer_plus(101, now=140))

    async def test_tick_is_limited_to_ten_and_shared_worker_lease_blocks_second_connection(self):
        for index in range(11):
            self.provider.transaction_id = f"{index+3:08x}-2222-4222-8222-222222222222"
            result = await self.service.prepare_payment(202+index, "viewer_plus", "sbp", f"batch-{index}", now=100)
            self.assertEqual(result.state, "pending")
        entered, release = asyncio.Event(), asyncio.Event()
        async def before_get():
            entered.set(); await release.wait()
        self.provider.before_get = before_get
        other = Database(str(self.path)); await other.connect(); self.addAsyncCleanup(other.close)
        second = self.make_service(other)
        first = asyncio.create_task(self.service.reconcile_due(now=101, limit=10))
        await asyncio.wait_for(entered.wait(), timeout=2)
        try:
            blocked = await asyncio.wait_for(second.reconcile_due(now=101, limit=10), timeout=1)
            self.assertEqual((blocked.attempted, blocked.deferred), (0, 1))
            self.assertEqual(len(self.provider.get_calls), 1)
        finally:
            release.set()
            summary = await first
        self.assertEqual(summary.attempted, 10)
        self.assertEqual(len(self.provider.get_calls), 10)
        self.provider.before_get = None
        self.assertEqual((await second.reconcile_due(now=102, limit=10)).attempted, 1)
        self.assertEqual(await self.count("entitlement_grants"), 11)

    async def test_worker_shutdown_leaves_no_lease_or_running_task(self):
        await self.checkout()
        entered = asyncio.Event()
        async def before_get():
            entered.set(); await asyncio.Event().wait()
        self.provider.before_get = before_get
        self.service._reconciler.start(interval=5)
        await asyncio.wait_for(entered.wait(), timeout=2)
        await self.service.close()
        self.assertIsNone(self.service._reconciler._task)
        attempts = await (await self.db.conn.execute("SELECT state,lease_until FROM billing_payment_attempts")).fetchall()
        self.assertEqual(attempts, [("pending", None)])
        leases = await (await self.db.conn.execute("SELECT owner,lease_until FROM billing_worker_lease")).fetchall()
        self.assertEqual(leases, [(None, None)])
