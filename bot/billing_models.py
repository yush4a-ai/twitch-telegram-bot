"""Persistent order and payment records, separate from provider callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BillingSubject:
    kind: Literal["viewer", "streamer"]
    subject_id: str


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
