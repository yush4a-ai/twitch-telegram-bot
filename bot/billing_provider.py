"""Provider contract and a network-free, non-monetary mock implementation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from .billing_models import ServerOrderSnapshot, VerifiedPaymentEvidence


class PaymentVerificationError(ValueError):
    """A provider notification is unsigned, stale, or malformed."""


class PaymentCreationUnknown(Exception):
    """An external create may have happened; do not repeat its POST."""


class PaymentCreationRejected(Exception):
    """Провайдер явно отказал в создании счёта: повтор не поможет.

    Отказ не является неизвестным исходом: счёт не создан и списаний нет,
    поэтому заказ закрывается, а не уходит в ручную проверку.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ProviderRateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__("provider rate limit")
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProviderHttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ProviderNotice:
    provider: str
    transaction_id: str
    raw_status: str
    event_key: str
    order_hint: str | None = None


@dataclass(frozen=True)
class RefundOutcome:
    state: str
    provider_reference: str | None = None


@dataclass(frozen=True)
class CheckoutSession:
    order_id: str
    reference: str | None
    hosted_url: str | None = None
    status: str = "pending"
    checkout_expires_at: float | None = None


@dataclass(frozen=True)
class VerifiedPaymentEvent:
    provider: str
    event_id: str
    order_id: str
    payment_id: str
    event_type: str
    units: int
    currency: str


class PaymentProvider(Protocol):
    provider_id: str

    async def create_checkout(self, order_id: str, units: int, currency: str) -> CheckoutSession: ...

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: float | None = None,
    ) -> VerifiedPaymentEvent: ...

    async def request_refund(self, payment_id: str, request_key: str) -> str: ...

    async def cancel_checkout(self, reference: str) -> None: ...


class MonetaryPaymentProvider(Protocol):
    provider_id: str

    async def create_payment(self, snapshot: ServerOrderSnapshot, attempt_id: str) -> CheckoutSession: ...

    async def get_payment_status(self, reference: str) -> VerifiedPaymentEvidence: ...

    def handle_callback(self, body: bytes, headers: Mapping[str, str]) -> ProviderNotice: ...

    async def refund_payment(self, reference: str, request_id: str) -> RefundOutcome: ...


_EVENT_FIELDS = {"event_id", "order_id", "payment_id", "type", "units", "currency"}
_ASCII_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HEX_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ASCII_ID.fullmatch(value) is not None


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PaymentVerificationError("duplicate JSON field")
        result[key] = value
    return result


class MockPaymentProvider:
    provider_id = "mock"

    def __init__(self, secret: bytes) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("mock webhook secret must have at least 32 bytes")
        self._secret = secret

    async def create_checkout(self, order_id: str, units: int, currency: str) -> CheckoutSession:
        if not _valid_id(order_id) or type(units) is not int or units != 1 or currency != "TEST":
            raise ValueError("invalid mock checkout")
        return CheckoutSession(order_id=order_id, reference=f"mock-checkout:{order_id}")

    async def request_refund(self, payment_id: str, request_key: str) -> str:
        if not _valid_id(payment_id) or not _valid_id(request_key):
            raise ValueError("invalid mock refund")
        return f"mock-refund:{payment_id}:{request_key}"

    async def cancel_checkout(self, reference: str) -> None:
        if not isinstance(reference, str) or not reference.startswith("mock-checkout:"):
            raise ValueError("invalid mock checkout reference")

    def verify_webhook(
        self, body: bytes, headers: Mapping[str, str], *, now: float | None = None,
    ) -> VerifiedPaymentEvent:
        if not isinstance(body, bytes) or not 1 <= len(body) <= 4096:
            raise PaymentVerificationError("invalid mock webhook size")
        normalized: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise PaymentVerificationError("duplicate or invalid mock webhook header")
            lowered = key.lower()
            if lowered in normalized:
                raise PaymentVerificationError("duplicate or invalid mock webhook header")
            normalized[lowered] = value
        timestamp = normalized.get("x-mock-timestamp", "")
        signature = normalized.get("x-mock-signature", "")
        if re.fullmatch(r"[0-9]{1,12}", timestamp) is None or _HEX_SIGNATURE.fullmatch(signature) is None:
            raise PaymentVerificationError("missing mock webhook signature")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at) or abs(at - int(timestamp)) > 300:
            raise PaymentVerificationError("stale mock webhook")
        expected = hmac.new(
            self._secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PaymentVerificationError("invalid mock webhook signature")
        try:
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_strict_pairs)
        except (UnicodeError, ValueError, TypeError) as error:
            raise PaymentVerificationError("invalid mock webhook JSON") from error
        if not isinstance(payload, dict) or set(payload) != _EVENT_FIELDS:
            raise PaymentVerificationError("invalid mock webhook fields")
        if not all(_valid_id(payload[key]) for key in ("event_id", "order_id", "payment_id")):
            raise PaymentVerificationError("invalid mock webhook ID")
        if not isinstance(payload["type"], str) or payload["type"] not in {"captured", "refunded"}:
            raise PaymentVerificationError("invalid mock webhook event")
        if type(payload["units"]) is not int or payload["units"] != 1 or payload["currency"] != "TEST":
            raise PaymentVerificationError("invalid mock webhook amount")
        return VerifiedPaymentEvent(
            provider="mock", event_id=payload["event_id"], order_id=payload["order_id"],
            payment_id=payload["payment_id"], event_type=payload["type"],
            units=1, currency="TEST",
        )

    def sign_test_event(
        self, event: VerifiedPaymentEvent, *, now: float | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        if event.provider != "mock":
            raise ValueError("wrong mock event provider")
        at = time.time() if now is None else now
        if not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError("invalid mock event time")
        timestamp = str(int(at))
        body = json.dumps({
            "event_id": event.event_id, "order_id": event.order_id,
            "payment_id": event.payment_id, "type": event.event_type,
            "units": event.units, "currency": event.currency,
        }, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(
            self._secret, timestamp.encode("ascii") + b"." + body, hashlib.sha256,
        ).hexdigest()
        return body, {"X-Mock-Timestamp": timestamp, "X-Mock-Signature": signature}
