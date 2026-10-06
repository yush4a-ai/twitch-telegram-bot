"""Transactional payment storage on Database's existing connection and writer lock."""

import json
import math
import re
from contextlib import asynccontextmanager
from dataclasses import asdict

from .billing_models import NoticeReceipt, PaymentAttempt, ServerOrderSnapshot
from .billing_provider import ProviderNotice


_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class PaymentInProgress(Exception):
    def __init__(self, order_id):
        super().__init__("another payment is unresolved")
        self.order_id = order_id


class PaymentAlreadyActive(Exception):
    pass


class BillingStore:
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def transaction(self):
        async with self.db._write_lock:
            conn = self.db.conn
            if conn.in_transaction:
                raise RuntimeError("unexpected open billing transaction")
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def create_order(self, snapshot: ServerOrderSnapshot, request_key: str,
                           attempt: PaymentAttempt, *, duration_seconds: int = 0,
                           enforce_payment_guard: bool = False):
        """Persist server-owned order/attempt before transport. No network or grant."""
        if not isinstance(snapshot, ServerOrderSnapshot) or not isinstance(attempt, PaymentAttempt):
            raise ValueError("server snapshot and attempt required")
        if not isinstance(request_key, str) or not _KEY.fullmatch(request_key):
            raise ValueError("invalid payment request key")
        if type(snapshot.telegram_user_id) is not int or snapshot.telegram_user_id <= 0:
            raise ValueError("invalid payment buyer")
        if snapshot.beneficiary_telegram_user_id != snapshot.telegram_user_id or type(snapshot.beneficiary_telegram_user_id) is not int:
            raise PermissionError("payment beneficiary must be its verified buyer")
        if (snapshot.product.product_id not in {"viewer_plus", "streamer_plus"}
            or snapshot.product.auto_renew is not False
            or snapshot.money != (snapshot.product.rub if snapshot.money.currency == "RUB" else snapshot.product.xtr)
            or type(duration_seconds) is not int or not 0 <= duration_seconds <= 2**31 - 1
            or type(snapshot.created_at) not in {int, float} or not math.isfinite(snapshot.created_at)
            or type(snapshot.checkout_expires_at) not in {int, float} or not math.isfinite(snapshot.checkout_expires_at)
            or snapshot.checkout_expires_at <= snapshot.created_at):
            raise ValueError("invalid payment product snapshot")
        if (snapshot.provider, snapshot.method, snapshot.money.currency) not in {
            ("platega", "sbp", "RUB"), ("platega", "bank_card", "RUB"), ("telegram_stars", "stars", "XTR"),
        }:
            raise ValueError("payment provider/method/currency mismatch")
        if (attempt.order_id != snapshot.order_id or attempt.provider != snapshot.provider
            or attempt.method != snapshot.method or attempt.state != "creating"
            or attempt.provider_reference is not None or attempt.reconcile_count != 0
            or not isinstance(attempt.attempt_id, str) or not _KEY.fullmatch(attempt.attempt_id)):
            raise ValueError("invalid initial payment attempt")
        if not isinstance(snapshot.order_id, str) or not re.fullmatch(r"[0-9a-f]{32}", snapshot.order_id):
            raise ValueError("invalid payment order ID")
        product_json = json.dumps(asdict(snapshot.product), sort_keys=True, separators=(",", ":"))
        async with self.transaction() as conn:
            if snapshot.product.product_id == "viewer_plus":
                owned = snapshot.subject.kind == "viewer" and snapshot.subject.subject_id == str(snapshot.telegram_user_id) and snapshot.broadcaster_id is None
            else:
                identity = await self.db.get_streamer_identity(snapshot.telegram_user_id)
                owned = identity is not None and snapshot.subject.kind == "streamer" and snapshot.subject.subject_id == identity[0] == snapshot.broadcaster_id
            if not owned:
                raise PermissionError("payment subject is not owned by buyer")
            existing = await self.db.get_billing_order_by_request_key(request_key)
            if existing is not None:
                if (existing.telegram_user_id, existing.beneficiary_telegram_user_id, existing.subject,
                    existing.plan, existing.provider, existing.method, existing.units, existing.currency,
                    existing.product_snapshot_json, existing.terms_version, existing.duration_seconds) != (
                    snapshot.telegram_user_id, snapshot.beneficiary_telegram_user_id, snapshot.subject,
                    snapshot.product.product_id, snapshot.provider, snapshot.method, snapshot.money.amount_minor,
                    snapshot.money.currency, product_json, snapshot.terms_version, duration_seconds):
                    raise ValueError("payment request key conflicts with frozen order")
                return existing
            active = await (await conn.execute(
                "SELECT order_id FROM billing_orders WHERE telegram_user_id=? "
                "AND provider IN ('platega','telegram_stars') "
                "AND financial_status IN ('pending','manual_review') "
                "AND status = 'pending' AND checkout_expires_at > ? LIMIT 1",
                (snapshot.telegram_user_id, snapshot.created_at),
            )).fetchone()
            if active is not None:
                raise PaymentInProgress(active[0])
            if enforce_payment_guard and await self.db.has_viewer_plus(snapshot.telegram_user_id, now=snapshot.created_at):
                raise PaymentAlreadyActive("effective Viewer access already active")
            await conn.execute(
                "INSERT INTO billing_orders(order_id,request_key,telegram_user_id,subject_kind,subject_id,broadcaster_id,"
                "plan,provider,status,units,currency,duration_seconds,created_at,checkout_expires_at,beneficiary_telegram_user_id,"
                "catalog_version,method,period_code,period_rule,period_rule_version,terms_version,product_snapshot_json) "
                "VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot.order_id, request_key, snapshot.telegram_user_id, snapshot.subject.kind, snapshot.subject.subject_id,
                 snapshot.broadcaster_id, snapshot.product.product_id, snapshot.provider, snapshot.money.amount_minor,
                 snapshot.money.currency, duration_seconds, snapshot.created_at, snapshot.checkout_expires_at,
                 snapshot.beneficiary_telegram_user_id, snapshot.product.catalog_version, snapshot.method,
                 snapshot.product.period_code, snapshot.product.period_rule, snapshot.product.period_rule_version,
                 snapshot.terms_version, product_json),
            )
            await conn.execute(
                "INSERT INTO billing_payment_attempts(attempt_id,order_id,provider,method,state,created_at,payload_digest) VALUES (?,?,?,?,?,?,?)",
                (attempt.attempt_id, snapshot.order_id, attempt.provider, attempt.method, attempt.state, attempt.created_at, attempt.payload_digest),
            )
            await conn.execute("INSERT INTO billing_audit(order_id,action,happened_at) VALUES (?,'order_created',?)", (snapshot.order_id, snapshot.created_at))
            return await self.db.get_billing_order(snapshot.order_id)

    async def get_attempt(self, attempt_id: str) -> PaymentAttempt | None:
        row = await (await self.db.conn.execute(
            "SELECT attempt_id,order_id,provider,method,state,provider_reference,created_at,next_reconcile_at,reconcile_count,lease_until,payload_digest "
            "FROM billing_payment_attempts WHERE attempt_id=?", (attempt_id,),
        )).fetchone()
        return PaymentAttempt(*row) if row else None

    async def accept_notice(self, notice: ProviderNotice, digest: str, *, now: float) -> NoticeReceipt:
        async with self.transaction() as conn:
            existing = await (await conn.execute(
                "SELECT transaction_id,raw_status,payload_digest FROM billing_provider_inbox WHERE provider=? AND event_key=?",
                (notice.provider, notice.event_key),
            )).fetchone()
            if existing is not None:
                if existing != (notice.transaction_id, notice.raw_status, digest):
                    raise ValueError("provider notice conflicts with durable event")
                return NoticeReceipt(True, True, notice.event_key)
            count = (await (await conn.execute(
                "SELECT count(*) FROM billing_provider_inbox WHERE provider=? AND state<>'done'", (notice.provider,),
            )).fetchone())[0]
            if count >= 4096:
                raise OverflowError("provider inbox capacity reached")
            await conn.execute(
                "INSERT INTO billing_provider_inbox(provider,event_key,transaction_id,raw_status,payload_digest,received_at,state,next_reconcile_at) "
                "VALUES (?,?,?,?,?,?,'pending',?)",
                (notice.provider, notice.event_key, notice.transaction_id, notice.raw_status, digest, now, now),
            )
            return NoticeReceipt(True, False, notice.event_key)
