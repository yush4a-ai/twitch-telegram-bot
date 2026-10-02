"""Billing orchestration over local provider contracts and the existing ledger."""

from __future__ import annotations

import math
import hashlib
import time
import uuid
import json
import asyncio
import calendar
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Mapping

from .billing_models import (
    AccessPeriodPolicy, ApplyResult, BillingSubject, CheckoutResult, Money,
    PaymentAttempt, ServerOrderSnapshot, VerifiedPaymentEvidence,
)
from .billing_provider import CheckoutSession, PaymentProvider, RefundOutcome
from .billing_store import BillingStore, PaymentAlreadyActive, PaymentInProgress
from .plan_catalog import BillingRuntimePolicy, get_product
from .database import Database


class BillingService:
    def __init__(self, db: Database, provider: PaymentProvider | None = None, *,
                 runtime_policy: BillingRuntimePolicy | None = None,
                 access_policy: AccessPeriodPolicy | None = None,
                 catalog=get_product, merchant_actor_ids: frozenset[int] = frozenset(),
                 terms_version: str | None = None) -> None:
        if provider is not None and provider.provider_id not in {"mock", "platega", "telegram_stars"}:
            raise ValueError("unsupported payment provider")
        if runtime_policy is not None and not isinstance(runtime_policy, BillingRuntimePolicy):
            raise ValueError("invalid runtime policy")
        if access_policy is not None and not isinstance(access_policy, AccessPeriodPolicy):
            raise ValueError("invalid access policy")
        if not callable(catalog) or not isinstance(merchant_actor_ids, frozenset) or any(type(actor) is not int or actor <= 0 for actor in merchant_actor_ids):
            raise ValueError("invalid billing dependencies")
        if terms_version is not None and (not isinstance(terms_version, str) or not 1 <= len(terms_version) <= 128 or terms_version == "unapproved"):
            raise ValueError("invalid approved terms version")
        self._db = db
        self._provider = provider
        self._store = BillingStore(db)
        self._runtime_policy = runtime_policy or BillingRuntimePolicy()
        self._access_policy = access_policy
        self._catalog = catalog
        self._merchant_actor_ids = merchant_actor_ids
        self._terms_version = terms_version
        from .billing_reconciliation import PaymentReconciler
        self._reconciler = PaymentReconciler(self)

    @staticmethod
    def _check_now(now):
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError("invalid billing time")

    def _local_runtime(self) -> bool:
        return (self._provider is not None and self._provider.provider_id in {"platega", "telegram_stars"}
                and getattr(self._provider, "network_free", False) is True
                and self._runtime_policy.mode == "sandbox" and self._runtime_policy.target_verified)

    def _period_ready(self, rule: str, version: str | None) -> bool:
        return (self._local_runtime() and self._runtime_policy.period_approved
                and self._runtime_policy.refund_policy_approved and self._access_policy is not None
                and (rule, version) == (self._access_policy.rule, self._access_policy.version))

    def _access_end(self, start: float) -> float:
        if self._access_policy.rule == "30_days":
            return start + 30 * 86400
        date = datetime.fromtimestamp(start, timezone.utc)
        year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
        return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1])).timestamp()

    async def prepare_payment(self, user_id: int, product_id: str, method: str,
                              request_key: str, *, now: float) -> CheckoutResult:
        self._check_now(now)
        if type(user_id) is not int or user_id <= 0 or method not in {"sbp", "bank_card", "stars"}:
            raise ValueError("invalid purchase request")
        product = self._catalog(product_id)
        if not self._period_ready(product.period_rule, product.period_rule_version) or self._terms_version is None:
            return CheckoutResult("unavailable", reason_code="payments_unavailable")
        provider_id = "telegram_stars" if method == "stars" else "platega"
        money = product.xtr if method == "stars" else product.rub
        if (self._provider.provider_id != provider_id or money is None
                or (method == "stars" and not self._runtime_policy.allow_invoice)
                or (method != "stars" and not self._runtime_policy.allow_external_create)):
            return CheckoutResult("unavailable", reason_code="payments_unavailable")
        existing = await self._db.get_billing_order_by_request_key(request_key)
        if existing is not None:
            if (existing.telegram_user_id, existing.plan, existing.method, existing.provider) != (user_id, product_id, method, provider_id):
                raise ValueError("purchase request key conflict")
            attempt = await (await self._db.conn.execute(
                "SELECT state FROM billing_payment_attempts WHERE order_id=? ORDER BY created_at LIMIT 1", (existing.order_id,),
            )).fetchone()
            state = attempt[0] if attempt else "manual_review"
            return CheckoutResult("pending" if state in {"pending", "done"} else "creation_unknown" if state in {"creating", "creation_unknown"} else "manual_review", existing.order_id,
                                  existing.checkout_url if state == "pending" else None)
        if await self._db.has_viewer_plus(user_id, now=now):
            return CheckoutResult("unavailable", reason_code="already_active" if product_id == "viewer_plus" else "upgrade_unapproved")
        if product_id == "streamer_plus":
            identity = await self._db.get_streamer_identity(user_id)
            if identity is None:
                raise PermissionError("verified Twitch identity required")
            subject, broadcaster = BillingSubject("streamer", identity[0]), identity[0]
            if await self._db.has_streamer_plus(user_id, now=now):
                return CheckoutResult("unavailable", reason_code="already_active")
        else:
            subject, broadcaster = BillingSubject("viewer", str(user_id)), None
        snapshot = ServerOrderSnapshot(uuid.uuid4().hex, user_id, user_id, subject, broadcaster,
            product, money, provider_id, method, self._terms_version, now, now + 900)
        attempt_id = uuid.uuid4().hex
        digest = hashlib.sha256(json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        attempt = PaymentAttempt(attempt_id, snapshot.order_id, provider_id, method, "creating", None, now, None, 0, None, digest)
        try:
            saved = await self._store.create_order(snapshot, request_key, attempt, enforce_payment_guard=True)
        except PaymentInProgress as error:
            return CheckoutResult("unavailable", error.order_id, reason_code="payment_in_progress")
        except PaymentAlreadyActive:
            return CheckoutResult("unavailable", reason_code="already_active")
        if saved.order_id != snapshot.order_id:
            # Another connection already owns the durable send; never POST again.
            return CheckoutResult("creation_unknown", saved.order_id)
        try:
            checkout = await self._provider.create_payment(snapshot, attempt_id)
            if checkout.order_id != snapshot.order_id:
                raise ValueError("provider checkout identity mismatch")
        except asyncio.CancelledError:
            await asyncio.shield(self._mark_creation_unknown(attempt_id))
            raise
        except Exception:
            await self._mark_creation_unknown(attempt_id)
            return CheckoutResult("creation_unknown", snapshot.order_id)
        async with self._store.transaction() as conn:
            current = await self._store.get_attempt(attempt_id)
            if current.provider_reference not in {None, checkout.reference}:
                await conn.execute("UPDATE billing_payment_attempts SET state='manual_review' WHERE attempt_id=?", (attempt_id,))
                return CheckoutResult("manual_review", snapshot.order_id)
            await conn.execute(
                "UPDATE billing_payment_attempts SET provider_reference=?,state=CASE WHEN state='creating' THEN 'pending' ELSE state END,"
                "next_reconcile_at=CASE WHEN state='creating' THEN ? ELSE next_reconcile_at END WHERE attempt_id=?",
                (checkout.reference, now, attempt_id),
            )
            await conn.execute("UPDATE billing_orders SET checkout_reference=?,checkout_url=? WHERE order_id=?", (checkout.reference, checkout.hosted_url, snapshot.order_id))
        return CheckoutResult("pending", snapshot.order_id, checkout.hosted_url)

    async def _mark_creation_unknown(self, attempt_id):
        async with self._store.transaction() as conn:
            await conn.execute("UPDATE billing_payment_attempts SET state=CASE WHEN state='creating' THEN 'creation_unknown' ELSE state END WHERE attempt_id=?", (attempt_id,))

    async def accept_provider_notice(self, body: bytes, headers: Mapping[str, str], *, now: float):
        self._check_now(now)
        if not isinstance(body, bytes) or not 1 <= len(body) <= 8192:
            raise ValueError("invalid provider notice size")
        if self._provider is None or not callable(getattr(self._provider, "handle_callback", None)):
            raise PermissionError("provider callback disabled")
        notice = self._provider.handle_callback(body, headers)
        if notice.provider != self._provider.provider_id:
            raise ValueError("provider notice mismatch")
        return await self._store.accept_notice(notice, hashlib.sha256(body).hexdigest(), now=now)

    async def apply_payment_evidence(self, evidence: VerifiedPaymentEvidence, *, now: float) -> ApplyResult:
        self._check_now(now)
        if not isinstance(evidence, VerifiedPaymentEvidence):
            raise ValueError("verified provider evidence required")
        self._check_now(evidence.observed_at)
        if (not isinstance(evidence.money, Money) or evidence.status not in {"pending", "confirmed", "canceled", "refunded"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii()
                       for value in (evidence.provider, evidence.transaction_id, evidence.order_id, evidence.attempt_id, evidence.method, evidence.raw_status))):
            raise ValueError("invalid verified evidence")
        statuses = {"platega": {"PENDING": "pending", "CONFIRMED": "confirmed", "CANCELED": "canceled", "CHARGEBACKED": "refunded"},
                    "telegram_stars": {"successful_payment": "confirmed", "refunded_payment": "refunded"}}
        if evidence.provider in statuses and statuses[evidence.provider].get(evidence.raw_status) != evidence.status:
            raise ValueError("provider status normalization mismatch")
        async with self._store.transaction() as conn:
            order = await self._db.get_billing_order(evidence.order_id)
            attempt = await self._store.get_attempt(evidence.attempt_id)
            if (order is None or attempt is None or self._provider is None
                    or evidence.provider != self._provider.provider_id or evidence.provider != order.provider
                    or attempt.order_id != order.order_id or attempt.provider != order.provider):
                return ApplyResult("manual_review", evidence.order_id)
            digest = hashlib.sha256(json.dumps(asdict(evidence), sort_keys=True).encode()).hexdigest()
            frozen_valid = (order.beneficiary_telegram_user_id == order.telegram_user_id
                and type(order.beneficiary_telegram_user_id) is int and order.telegram_user_id > 0
                and ((order.plan == "viewer_plus" and order.subject == BillingSubject("viewer", str(order.telegram_user_id)) and order.broadcaster_id is None)
                     or (order.plan == "streamer_plus" and order.subject == BillingSubject("streamer", order.broadcaster_id))))
            if (not frozen_valid or (evidence.money.amount_minor, evidence.money.currency, evidence.method) != (order.units, order.currency, order.method)
                    or attempt.method != order.method):
                await conn.execute(
                    "INSERT OR IGNORE INTO billing_provider_quarantine(provider,transaction_id,payload_digest,order_hint,raw_status,amount_minor,currency,method,observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (evidence.provider, evidence.transaction_id, digest, evidence.order_id, evidence.raw_status,
                     evidence.money.amount_minor, evidence.money.currency, evidence.method, now))
                await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'evidence_mismatch',?)", (order.order_id, now))
                return ApplyResult("manual_review", order.order_id)
            previous = await (await conn.execute("SELECT order_id,attempt_id,status,amount_minor,currency,method FROM billing_provider_facts WHERE provider=? AND transaction_id=?",
                (evidence.provider, evidence.transaction_id))).fetchone()
            if previous is not None and (previous[0], previous[1], previous[3], previous[4], previous[5]) != (
                    order.order_id, attempt.attempt_id, order.units, order.currency, order.method):
                return ApplyResult("manual_review", order.order_id)
            if previous is not None and previous[2] == "refunded":
                return ApplyResult("already_applied", order.order_id, order.grant_id)
            if previous is not None and previous[2] in {"confirmed", "canceled"}:
                if evidence.status == "pending":
                    return ApplyResult("already_applied", order.order_id, order.grant_id)
                if (previous[2], evidence.status) in {("confirmed", "canceled"), ("canceled", "confirmed")}:
                    # Preserve the known fact and the contradictory observation
                    # for review; neither stale state nor a second grant wins.
                    await conn.execute(
                        "INSERT OR IGNORE INTO billing_provider_quarantine(provider,transaction_id,payload_digest,order_hint,raw_status,amount_minor,currency,method,observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (evidence.provider, evidence.transaction_id, digest, evidence.order_id, evidence.raw_status,
                         evidence.money.amount_minor, evidence.money.currency, evidence.method, now))
                    await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'status_conflict',?)", (order.order_id, now))
                    return ApplyResult("manual_review", order.order_id, order.grant_id)
            await conn.execute(
                "INSERT INTO billing_provider_facts(provider,transaction_id,order_id,attempt_id,status,raw_status,amount_minor,currency,method,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,transaction_id) DO UPDATE SET status=excluded.status,raw_status=excluded.raw_status,observed_at=excluded.observed_at",
                (evidence.provider, evidence.transaction_id, order.order_id, attempt.attempt_id, evidence.status,
                 evidence.raw_status, order.units, order.currency, order.method, now))
            if attempt.provider_reference not in {None, evidence.transaction_id}:
                await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'extra_transaction',?)", (order.order_id, now))
                return ApplyResult("manual_review", order.order_id, order.grant_id)
            await conn.execute("UPDATE billing_payment_attempts SET provider_reference=? WHERE attempt_id=?", (evidence.transaction_id, attempt.attempt_id))
            if order.financial_status == "refunded":
                return ApplyResult("already_applied", order.order_id, order.grant_id)
            if evidence.status == "pending":
                return ApplyResult("pending", order.order_id, order.grant_id)
            if evidence.status == "canceled" and order.grant_id is not None:
                return ApplyResult("manual_review", order.order_id, order.grant_id)
            if evidence.status == "refunded":
                changed = False
                if order.grant_id is not None and self._period_ready(order.period_rule, order.period_rule_version):
                    update = await conn.execute("UPDATE entitlement_grants SET revoked_at=? WHERE grant_id=? AND source='paid' AND revoked_at IS NULL", (now, order.grant_id))
                    changed = update.rowcount == 1
                    if changed:
                        await conn.execute("INSERT INTO entitlement_events(grant_id,action,actor_telegram_id,happened_at) VALUES (?,'revoke',0,?)", (order.grant_id, now))
                await conn.execute("UPDATE billing_orders SET status='refunded',financial_status='refunded',closed_at=? WHERE order_id=?", (now, order.order_id))
                await conn.execute("UPDATE billing_payment_attempts SET state='done',lease_until=NULL,next_reconcile_at=NULL WHERE attempt_id=?", (attempt.attempt_id,))
                await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'payment_refunded',?)", (order.order_id, now))
                return ApplyResult("applied" if changed else "recorded", order.order_id, order.grant_id)
            if evidence.status == "canceled":
                await conn.execute("UPDATE billing_orders SET status='cancelled',financial_status='canceled',closed_at=? WHERE order_id=?", (now, order.order_id))
                await conn.execute("UPDATE billing_payment_attempts SET state='done',lease_until=NULL,next_reconcile_at=NULL WHERE attempt_id=?", (attempt.attempt_id,))
                return ApplyResult("recorded", order.order_id)
            if order.grant_id is not None:
                return ApplyResult("already_applied", order.order_id, order.grant_id)
            await conn.execute("UPDATE billing_orders SET financial_status='confirmed' WHERE order_id=?", (order.order_id,))
            if (not self._period_ready(order.period_rule, order.period_rule_version)
                    or self._terms_version is None or order.terms_version != self._terms_version):
                return ApplyResult("recorded", order.order_id)
            try:
                frozen_product = json.loads(order.product_snapshot_json)
                expected_money = frozen_product["rub" if order.currency == "RUB" else "xtr"]
                matches = (expected_money == {"amount_minor": order.units, "currency": order.currency}
                    and frozen_product["product_id"] == order.plan and frozen_product["catalog_version"] == order.catalog_version
                    and frozen_product["period_rule"] == order.period_rule and frozen_product["period_rule_version"] == order.period_rule_version
                    and frozen_product["auto_renew"] is False)
            except (ValueError, TypeError, KeyError):
                matches = False
            if order.subject_kind == "streamer":
                identity = await self._db.get_streamer_identity(order.telegram_user_id)
                matches = matches and identity is not None and identity[0] == order.broadcaster_id
            if not matches:
                return ApplyResult("manual_review", order.order_id)
            grant_id = uuid.uuid4().hex
            end = self._access_end(now)
            await conn.execute(
                "INSERT INTO entitlement_grants(grant_id,request_key,subject_kind,subject_id,plan,source,starts_at,expires_at,issued_by,created_at,beneficiary_telegram_user_id) "
                "VALUES (?,?,?,?,?,'paid',?,?,0,?,?)",
                (grant_id, "paid-order:" + order.order_id, order.subject_kind, order.subject_id, order.plan, now, end, now, order.beneficiary_telegram_user_id))
            await conn.execute("INSERT INTO entitlement_events(grant_id,action,actor_telegram_id,happened_at) VALUES (?,'grant',0,?)", (grant_id, now))
            await conn.execute("UPDATE billing_orders SET status='paid',paid_at=?,grant_id=?,access_starts_at=?,access_expires_at=?,duration_seconds=? WHERE order_id=?",
                (now, grant_id, now, end, int(end-now), order.order_id))
            await conn.execute("UPDATE billing_payment_attempts SET state='done',lease_until=NULL,next_reconcile_at=NULL WHERE attempt_id=?", (attempt.attempt_id,))
            await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'access_applied',?)", (order.order_id, now))
            return ApplyResult("applied", order.order_id, grant_id)

    async def request_payment_refund(self, actor_id: int, order_id: str, request_key: str, *, now: float) -> RefundOutcome:
        self._check_now(now)
        if type(actor_id) is not int or actor_id not in self._merchant_actor_ids:
            raise PermissionError("merchant refund authority required")
        if not self._local_runtime() or not self._runtime_policy.refund_policy_approved:
            return RefundOutcome("unsupported")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128 or not request_key.isascii():
            raise ValueError("invalid refund request key")
        async with self._store.transaction() as conn:
            order = await self._db.get_billing_order(order_id)
            if order is None or order.provider != self._provider.provider_id or order.financial_status != "confirmed":
                raise ValueError("confirmed provider order required")
            existing = await (await conn.execute("SELECT order_id,state,provider_reference FROM billing_payment_refunds WHERE request_key=?", (request_key,))).fetchone()
            if existing is not None:
                if existing[0] != order_id:
                    raise ValueError("refund request key conflict")
                return RefundOutcome("unknown" if existing[1] == "requesting" else existing[1], existing[2])
            active = await (await conn.execute("SELECT state,provider_reference FROM billing_payment_refunds WHERE order_id=? AND state IN ('requesting','unknown','accepted','manual_control_required')", (order_id,))).fetchone()
            if active is not None:
                return RefundOutcome("unknown" if active[0] == "requesting" else active[0], active[1])
            attempt = await (await conn.execute("SELECT provider_reference FROM billing_payment_attempts WHERE order_id=? AND provider_reference IS NOT NULL ORDER BY created_at LIMIT 1", (order_id,))).fetchone()
            if attempt is None:
                raise ValueError("provider transaction missing")
            reference = attempt[0]
            await conn.execute("INSERT INTO billing_payment_refunds(request_key,order_id,actor_telegram_user_id,state,provider_reference,requested_at) VALUES (?,?,?,'requesting',?,?)", (request_key, order_id, actor_id, reference, now))
        try:
            result = await self._provider.refund_payment(reference, request_key)
            if not isinstance(result, RefundOutcome) or result.state not in {"unsupported", "accepted", "manual_control_required", "declined", "unknown"}:
                raise ValueError("unverified refund outcome")
        except asyncio.CancelledError:
            await asyncio.shield(self._finish_refund(request_key, "unknown"))
            raise
        except Exception:
            result = RefundOutcome("unknown", reference)
        await self._finish_refund(request_key, result.state)
        return result

    async def _finish_refund(self, request_key, state):
        async with self._store.transaction() as conn:
            await conn.execute("UPDATE billing_payment_refunds SET state=? WHERE request_key=?", (state, request_key))

    async def reconcile_due(self, *, now: float, limit: int = 10):
        return await self._reconciler.reconcile_due(now=now, limit=limit)

    async def close(self):
        await self._reconciler.close()

    async def create_checkout(
        self, telegram_user_id: int, request_key: str, duration_seconds: int,
        *, plan: str = "streamer_plus", now: float | None = None,
    ) -> CheckoutSession:
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid checkout time")
        if plan == "viewer_plus":
            subject = BillingSubject("viewer", str(telegram_user_id))
        elif plan == "streamer_plus":
            identity = await self._db.get_streamer_identity(telegram_user_id)
            if identity is None:
                raise PermissionError("streamer identity is not linked")
            subject = BillingSubject("streamer", identity[0])
        else:
            raise ValueError("unsupported billing plan")
        order = await self._db.create_billing_order(
            uuid.uuid4().hex, request_key, telegram_user_id, duration_seconds,
            now=at, plan=plan, subject=subject,
        )
        if order.status in {"cancelled", "expired", "refunded"} or at >= order.checkout_expires_at:
            raise ValueError("checkout is closed")
        if order.checkout_reference is not None:
            return CheckoutSession(order.order_id, order.checkout_reference)
        checkout = await self._provider.create_checkout(order.order_id, order.units, order.currency)
        if checkout.order_id != order.order_id:
            raise ValueError("provider returned wrong order ID")
        saved = await self._db.save_billing_checkout_reference(
            order.order_id, checkout.reference, now=at,
        )
        return CheckoutSession(saved.order_id, saved.checkout_reference)

    async def cancel_order(
        self, telegram_user_id: int, order_id: str, *, now: float | None = None,
    ) -> bool:
        at = time.time() if now is None else now
        order = await self._db.get_billing_order(order_id)
        if order is None or order.telegram_user_id != telegram_user_id:
            raise PermissionError("billing order is not owned by Telegram user")
        if order.status in {"cancelled", "expired"}:
            return False
        if order.status != "pending":
            raise ValueError("only pending billing orders can be cancelled")
        if order.checkout_reference is not None and at < order.checkout_expires_at:
            await self._provider.cancel_checkout(order.checkout_reference)
        return await self._db.cancel_billing_order(telegram_user_id, order_id, now=at)

    async def expire_pending(self, *, now: float | None = None) -> int:
        at = time.time() if now is None else now
        return await self._db.expire_pending_billing_orders(now=at)

    async def handle_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: float | None = None,
    ) -> str:
        at = time.time() if now is None else now
        event = self._provider.verify_webhook(body, headers, now=at)
        return await self._db.apply_verified_billing_event(
            event, hashlib.sha256(body).hexdigest(), now=at,
        )

    async def request_refund(
        self, telegram_user_id: int, order_id: str, request_key: str,
        *, now: float | None = None,
    ) -> str:
        at = time.time() if now is None else now
        order = await self._db.get_billing_order(order_id)
        if order is None or order.telegram_user_id != telegram_user_id:
            raise PermissionError("billing order is not owned by Telegram user")
        existing = await self._db.get_billing_refund_request(request_key)
        if existing is not None:
            if existing[0] != order_id:
                raise ValueError("refund request key conflict")
            return existing[1]
        if order.status != "paid":
            raise ValueError("only paid orders can request a refund")
        payment = await self._db.get_billing_payment(order_id)
        if payment is None or payment.status != "captured":
            raise ValueError("captured payment is missing")
        reference = await self._provider.request_refund(payment.payment_id, request_key)
        return await self._db.save_billing_refund_request(
            order_id, request_key, reference, now=at,
        )
