"""Persistent order and payment records, separate from provider callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AccessPeriodPolicy:
    rule: str
    version: str

    def __post_init__(self):
        if self.rule not in {"30_days", "calendar_month"} or not isinstance(self.version, str) or not 1 <= len(self.version) <= 128:
            raise ValueError("invalid approved access period")


@dataclass(frozen=True)
class CheckoutResult:
    state: str
    order_id: str | None = None
    hosted_url: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    state: str
    order_id: str
    grant_id: str | None = None


@dataclass(frozen=True)
class NoticeReceipt:
    durable: bool
    duplicate: bool
    event_key: str


@dataclass(frozen=True)
class ReconcileSummary:
    attempted: int = 0
    deferred: int = 0
    applied: int = 0
    manual_review: int = 0


@dataclass(frozen=True)
class BillingSubject:
    kind: Literal["viewer", "streamer"]
    subject_id: str


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: Literal["RUB", "XTR", "TEST"]

    def __post_init__(self):
        if type(self.amount_minor) is not int or not 0 < self.amount_minor <= 2**63 - 1 or self.currency not in {"RUB", "XTR", "TEST"}:
            raise ValueError("invalid monetary amount or currency")


@dataclass(frozen=True)
class ProductSnapshot:
    product_id: str
    catalog_version: str
    rub: Money
    xtr: Money | None
    period_code: str
    period_rule: str
    period_rule_version: str | None
    auto_renew: bool
    includes: tuple[str, ...]
    feature_ids: tuple[str, ...]


@dataclass(frozen=True)
class ServerOrderSnapshot:
    order_id: str
    telegram_user_id: int
    beneficiary_telegram_user_id: int
    subject: BillingSubject
    broadcaster_id: str | None
    product: ProductSnapshot
    money: Money
    provider: str
    method: str
    terms_version: str
    created_at: float
    checkout_expires_at: float


@dataclass(frozen=True)
class PaymentAttempt:
    attempt_id: str
    order_id: str
    provider: str
    method: str
    state: str
    provider_reference: str | None
    created_at: float
    next_reconcile_at: float | None
    reconcile_count: int
    lease_until: float | None
    payload_digest: str


@dataclass(frozen=True)
class VerifiedPaymentEvidence:
    provider: str
    transaction_id: str
    order_id: str
    attempt_id: str
    money: Money
    method: str
    status: str
    raw_status: str
    observed_at: float


@dataclass(frozen=True)
class BillingOrder:
    order_id: str
    request_key: str
    telegram_user_id: int
    subject_kind: Literal["viewer", "streamer"]
    subject_id: str
    broadcaster_id: str | None
    plan: str
    provider: str
    status: str
    units: int
    currency: str
    duration_seconds: int
    created_at: float
    checkout_expires_at: float
    checkout_reference: str | None
    paid_at: float | None
    closed_at: float | None
    grant_id: str | None
    beneficiary_telegram_user_id: int | None = None
    catalog_version: str = "legacy-test"
    method: str = "mock"
    period_code: str = "test_duration"
    period_rule: str = "legacy_test_seconds"
    period_rule_version: str | None = None
    terms_version: str = "legacy-test"
    financial_status: str = "pending"
    access_starts_at: float | None = None
    access_expires_at: float | None = None
    product_snapshot_json: str | None = None
    checkout_url: str | None = None
    entitlement_state: str = "none"
    entitlement_error: str | None = None
    entitlement_updated_at: float | None = None

    @property
    def subject(self) -> BillingSubject:
        return BillingSubject(self.subject_kind, self.subject_id)


@dataclass(frozen=True)
class PaymentRecord:
    provider: str
    payment_id: str
    order_id: str
    status: str
    units: int
    currency: str
    captured_at: float
    refunded_at: float | None
