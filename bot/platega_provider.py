"""Strict Platega adapter: only declared transports, and only under policy."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import time
from decimal import Decimal
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from typing import Mapping, Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID

from .billing_models import BillingSubject, Money, ProductSnapshot, ServerOrderSnapshot, VerifiedPaymentEvidence
from .billing_provider import (
    CheckoutSession, PaymentCreationUnknown, PaymentVerificationError,
    ProviderHttpResponse, ProviderNotice, ProviderRateLimited, RefundOutcome,
    _strict_pairs,
)
from .plan_catalog import BillingRuntimePolicy


class ProviderTransport(Protocol):
    network_free: bool

    async def request(self, method: str, path: str, *, json: dict | None,
                      headers: Mapping[str, str], timeout: float) -> ProviderHttpResponse: ...


_STATUSES = {"PENDING": "pending", "CONFIRMED": "confirmed",
             "CANCELED": "canceled", "CHARGEBACKED": "refunded"}
_METHODS = {"sbp": 2, "bank_card": 11}
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")


def _uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise PaymentVerificationError("invalid provider transaction ID")
    try:
        parsed = str(UUID(value))
    except ValueError as error:
        raise PaymentVerificationError("invalid provider transaction ID") from error
    if parsed != value:
        raise PaymentVerificationError("noncanonical provider transaction ID")
    return parsed


def _clock(now: float | None) -> float:
    at = time.time() if now is None else now
    if type(at) not in (int, float) or not math.isfinite(at):
        raise PaymentVerificationError("invalid observation time")
    return at


def _reject_constant(_value):
    raise PaymentVerificationError("nonfinite JSON number")


def _json(body: bytes, maximum: int) -> dict:
    if not isinstance(body, bytes) or not 1 <= len(body) <= maximum:
        raise PaymentVerificationError("invalid provider body size")
    try:
        values = json.loads(body.decode("utf-8"), object_pairs_hook=_strict_pairs,
                           parse_float=Decimal, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise PaymentVerificationError("invalid provider JSON") from None
    if not isinstance(values, dict):
        raise PaymentVerificationError("invalid provider object")
    return values


def _money(amount: object, currency: object) -> Money:
    if type(amount) not in (int, Decimal) or currency != "RUB":
        raise PaymentVerificationError("invalid provider amount or currency")
    value = Decimal(amount)
    if not value.is_finite() or value <= 0 or value > Decimal(2**63 - 1) / 100:
        raise PaymentVerificationError("invalid provider amount")
    minor = value * 100
    if minor != minor.to_integral_value():
        raise PaymentVerificationError("fractional minor unit")
    return Money(int(minor), "RUB")


class PlategaProvider:
    provider_id = "platega"
    # Возврат и сверку ведёт владелец: провайдер умеет сверяться по статусу.
    can_reconcile = True
    # Платёжка требует имя покупателя в metadata: без него платёж не создаётся.
    requires_buyer_name = True

    @property
    def network_free(self):
        return getattr(self._transport, "network_free", False) is True

    @property
    def _trusted_transport(self) -> bool:
        """Транспорт объявлен явно: локальный контракт или выбранный сетевой API."""
        return (getattr(self._transport, "trusted_contract", None) is True
                or getattr(self._transport, "network_free", None) is True)

    @property
    def money_capable(self) -> bool:
        """Готов ли контракт к денежным операциям в текущем окружении.

        Одного наличия ключей и адреса недостаточно: нужен объявленный транспорт
        и включённая владельцем денежная политика.
        """
        return bool(self._trusted_transport and self._policy.mode == "sandbox"
                    and self._policy.target_verified)

    def __init__(self, transport: ProviderTransport, merchant_id: str, secret: str, *,
                 hosted_hosts: frozenset[str], runtime_policy: BillingRuntimePolicy,
                 buyer_names: Mapping[int, str] | None = None,
                 status_methods: Mapping[str, str] | None = None,
                 return_url: str | None = None, failed_url: str | None = None):
        self._merchant = _uuid(merchant_id)
        if not isinstance(secret, str) or not 16 <= len(secret) <= 512 or not secret.isascii():
            raise ValueError("invalid provider secret")
        if (not isinstance(hosted_hosts, frozenset) or not hosted_hosts
                or any(not isinstance(host, str) or re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", host) is None
                       for host in hosted_hosts)):
            raise ValueError("invalid hosted domain allowlist")
        if not isinstance(runtime_policy, BillingRuntimePolicy):
            raise ValueError("invalid provider policy")
        self._secret = secret
        self._transport = transport
        self._hosts = hosted_hosts
        self._policy = runtime_policy
        self._return_url = self._return_location(return_url)
        self._failed_url = self._return_location(failed_url)
        self._names = MappingProxyType(dict(buyer_names or {}))
        # Only SBPQR is evidenced by the public GET example. Merchant-specific
        # card status names require an explicit verified mapping before use.
        names = {"SBPQR": "sbp"} if status_methods is None else dict(status_methods)
        if any(not isinstance(key, str) or not 1 <= len(key) <= 40 or value not in _METHODS
               for key, value in names.items()):
            raise ValueError("invalid canonical method mapping")
        self._status_methods = MappingProxyType(names)
        self._created: set[str] = set()
        self._refunds: dict[tuple[str, str], RefundOutcome] = {}
        # Имена покупателей приходят из подписанных данных Telegram и живут
        # только в памяти процесса: они нужны для metadata конкретного платежа.
        self._buyers: dict[int, str] = {}

    def remember_buyer(self, user_id: int, name: str) -> None:
        """Запоминает подтверждённое display name покупателя перед созданием счёта."""
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid buyer id")
        if (not isinstance(name, str) or not name.strip() or len(name) > 256
                or any(ord(char) < 32 for char in name)):
            raise ValueError("invalid buyer display name")
        if len(self._buyers) >= 4096:
            # Переполнение памяти не должно останавливать приём оплат.
            for key in list(self._buyers)[:2048]:
                self._buyers.pop(key, None)
        self._buyers[user_id] = name

    def has_buyer_name(self, user_id) -> bool:
        return (type(user_id) is int
                and (user_id in self._buyers or user_id in self._names))

    def _gate(self):
        if not self._trusted_transport:
            raise PermissionError("only declared provider contracts are enabled")
        if self._policy.mode != "sandbox" or not self._policy.target_verified:
            raise PermissionError("provider operations disabled by runtime policy")

    async def close(self) -> None:
        closer = getattr(self._transport, "close", None)
        if callable(closer):
            await closer()

    async def _request(self, method: str, path: str, *, payload: dict | None = None,
                       now: float | None = None) -> dict:
        self._gate()
        response = await asyncio.wait_for(self._transport.request(method, path, json=payload,
            headers={"X-MerchantId": self._merchant, "X-Secret": self._secret,
                     "Content-Type": "application/json", "Accept": "application/json"}, timeout=5), timeout=5)
        if not isinstance(response, ProviderHttpResponse) or type(response.status) is not int:
            raise PaymentVerificationError("invalid provider response")
        if response.status == 429:
            normalized = self._headers(response.headers)
            retry = normalized.get("retry-after", "300")
            if re.fullmatch(r"[0-9]{1,10}", retry):
                seconds = int(retry)
            else:
                try:
                    date = parsedate_to_datetime(retry)
                    seconds = math.ceil(date.timestamp() - _clock(now)) if date.tzinfo else 300
                except (ValueError, TypeError, OverflowError):
                    seconds = 300
            raise ProviderRateLimited(max(1, seconds))
        if response.status != 200:
            raise PaymentVerificationError("provider request not successful")
        return _json(response.body, 65536)

    @staticmethod
    def _headers(headers: Mapping[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str) or not isinstance(value, str) or key.lower() in normalized:
                raise PaymentVerificationError("duplicate or invalid provider header")
            normalized[key.lower()] = value
        return normalized

    def _hosted_url(self, value: object) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(char) <= 32 for char in value):
            raise PaymentVerificationError("invalid hosted URL")
        try:
            parts = urlsplit(value)
            valid = (parts.scheme == "https" and parts.hostname in self._hosts
                     and parts.port in (None, 443) and parts.username is None
                     and parts.password is None and not parts.fragment)
        except ValueError as error:
            raise PaymentVerificationError("invalid hosted URL") from error
        decoded = unquote(value)
        if not valid or self._secret in decoded or self._merchant in decoded or "\\" in decoded:
            raise PaymentVerificationError("invalid hosted URL")
        return value

    def _return_location(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 2048 or any(ord(char) <= 32 for char in value):
            raise ValueError("invalid configured return URL")
        try:
            parts = urlsplit(value)
            valid = (parts.scheme == "https" and bool(parts.hostname)
                     and parts.port in (None, 443) and parts.username is None
                     and parts.password is None and not parts.fragment)
        except ValueError:
            valid = False
        decoded = unquote(value)
        if not valid or "\\" in decoded or self._secret in decoded or self._merchant in decoded:
            raise ValueError("invalid configured return URL")
        return value

    async def create_payment(self, snapshot: ServerOrderSnapshot, attempt_id: str) -> CheckoutSession:
        self._gate()
        if (not self._policy.allow_external_create or not self._policy.period_approved
                or not self._policy.refund_policy_approved):
            raise PermissionError("payment creation is unavailable")
        if (not isinstance(snapshot, ServerOrderSnapshot) or snapshot.provider != self.provider_id
                or not isinstance(snapshot.product, ProductSnapshot) or not isinstance(snapshot.money, Money)
                or not isinstance(snapshot.subject, BillingSubject)
                or snapshot.method not in _METHODS or snapshot.money != snapshot.product.rub
                or snapshot.money.currency != "RUB" or snapshot.money.amount_minor % 100
                or snapshot.product.auto_renew or snapshot.beneficiary_telegram_user_id != snapshot.telegram_user_id
                or type(snapshot.beneficiary_telegram_user_id) is not int
                or type(snapshot.telegram_user_id) is not int or snapshot.telegram_user_id <= 0
                or not isinstance(attempt_id, str) or _HEX_ID.fullmatch(attempt_id) is None
                or not isinstance(snapshot.order_id, str) or _HEX_ID.fullmatch(snapshot.order_id) is None):
            raise PaymentVerificationError("invalid server checkout snapshot")
        if ((snapshot.product.product_id == "viewer_plus"
             and (snapshot.subject != BillingSubject("viewer", str(snapshot.telegram_user_id)) or snapshot.broadcaster_id is not None))
                or (snapshot.product.product_id == "streamer_plus"
                    and (snapshot.subject != BillingSubject("streamer", snapshot.broadcaster_id)
                         or not isinstance(snapshot.broadcaster_id, str) or not snapshot.broadcaster_id.isascii()
                         or not snapshot.broadcaster_id.isdecimal() or int(snapshot.broadcaster_id) <= 0))
                or snapshot.product.product_id not in {"viewer_plus", "streamer_plus"}):
            raise PaymentVerificationError("invalid checkout subject")
        _clock(snapshot.created_at)
        _clock(snapshot.checkout_expires_at)
        if snapshot.checkout_expires_at <= snapshot.created_at:
            raise PaymentVerificationError("invalid checkout interval")
        if self._return_url is None or self._failed_url is None:
            raise PaymentVerificationError("configured return locations required")
        name = self._buyers.get(snapshot.telegram_user_id) or self._names.get(snapshot.telegram_user_id)
        if not isinstance(name, str) or not 1 <= len(name) <= 256 or any(ord(char) < 32 for char in name):
            raise PaymentVerificationError("verified buyer display name required")
        if attempt_id in self._created:
            raise PaymentCreationUnknown("creation already attempted; reconcile its outcome")
        if len(self._created) >= 4096:
            raise PaymentVerificationError("local creation contract capacity reached")
        self._created.add(attempt_id)
        payload = {"paymentMethod": _METHODS[snapshot.method],
            "paymentDetails": {"amount": snapshot.money.amount_minor // 100, "currency": "RUB"},
            "orderId": snapshot.order_id,
            "return": self._return_url, "failedUrl": self._failed_url,
            "description": "TwitchSignalBot " + snapshot.product.product_id,
            "payload": json.dumps({"order_id": snapshot.order_id, "attempt_id": attempt_id}, separators=(",", ":")),
            "metadata": {"userId": str(snapshot.telegram_user_id), "userName": name}}
        try:
            data = await self._request("POST", "/transaction/process", payload=payload)
            reference = _uuid(data.get("transactionId"))
            if data.get("status") != "PENDING":
                raise PaymentVerificationError("creation response requires reconciliation")
            if "merchantId" in data and data["merchantId"] != self._merchant:
                raise PaymentVerificationError("wrong creation merchant")
            url = self._hosted_url(data.get("redirect", data.get("url")))
            expires = data.get("expiresIn")
            if not isinstance(expires, str) or re.fullmatch(r"[0-9]{2}:[0-5][0-9]:[0-5][0-9]", expires) is None:
                raise PaymentVerificationError("invalid checkout expiry")
            hours, minutes, seconds = map(int, expires.split(":"))
            duration = hours * 3600 + minutes * 60 + seconds
            if not 0 < duration <= 86400:
                raise PaymentVerificationError("invalid checkout expiry")
            return CheckoutSession(snapshot.order_id, reference, url, "pending",
                min(snapshot.checkout_expires_at, snapshot.created_at + duration))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PaymentCreationUnknown("creation outcome unknown; do not repeat POST") from None

    async def get_payment_status(self, reference: str, *, now: float | None = None) -> VerifiedPaymentEvidence:
        reference = _uuid(reference)
        at = _clock(now)
        data = await self._request("GET", "/transaction/" + reference, now=at)
        if data.get("id") != reference or data.get("mechantId") != self._merchant:
            raise PaymentVerificationError("canonical identity mismatch")
        raw_status = data.get("status")
        if not isinstance(raw_status, str) or raw_status not in _STATUSES:
            raise PaymentVerificationError("unknown canonical status")
        details = data.get("paymentDetails")
        if not isinstance(details, dict) or set(details) != {"amount", "currency"}:
            raise PaymentVerificationError("canonical payment details missing")
        money = _money(details["amount"], details["currency"])
        method = self._status_methods.get(data.get("paymentMethod")) if isinstance(data.get("paymentMethod"), str) else None
        if method is None:
            raise PaymentVerificationError("unknown canonical method")
        payload = data.get("payload")
        if not isinstance(payload, str):
            raise PaymentVerificationError("canonical correlation missing")
        correlation = _json(payload.encode("utf-8"), 512)
        if set(correlation) != {"order_id", "attempt_id"} or any(
            not isinstance(value, str) or _HEX_ID.fullmatch(value) is None for value in correlation.values()
        ):
            raise PaymentVerificationError("invalid canonical correlation")
        return VerifiedPaymentEvidence(self.provider_id, reference, correlation["order_id"],
            correlation["attempt_id"], money, method, _STATUSES[raw_status], raw_status, at)

    def handle_callback(self, body: bytes, headers: Mapping[str, str]) -> ProviderNotice:
        normalized = self._headers(headers)
        merchant = normalized.get("x-merchantid", "")
        secret = normalized.get("x-secret", "")
        if (not merchant.isascii() or not secret.isascii()
                or not hmac.compare_digest(merchant, self._merchant)
                or not hmac.compare_digest(secret, self._secret)):
            raise PaymentVerificationError("invalid provider authentication")
        data = _json(body, 8192)
        if not {"id", "amount", "currency", "status"} <= set(data) <= {"id", "amount", "currency", "status", "paymentMethod"}:
            raise PaymentVerificationError("invalid callback fields")
        transaction = _uuid(data["id"])
        _money(data["amount"], data["currency"])
        status = data["status"]
        if not isinstance(status, str) or status not in {"CONFIRMED", "CANCELED", "CHARGEBACKED"}:
            raise PaymentVerificationError("unknown callback status")
        if "paymentMethod" in data and (type(data["paymentMethod"]) is not int or data["paymentMethod"] not in _METHODS.values()):
            raise PaymentVerificationError("invalid callback method")
        key = hashlib.sha256((self.provider_id + ":" + self._merchant + ":" + transaction + ":" + status).encode()).hexdigest()
        return ProviderNotice(self.provider_id, transaction, status, key)

    async def refund_payment(self, reference: str, request_id: str) -> RefundOutcome:
        self._gate()
        if not self._policy.refund_policy_approved:
            raise PermissionError("refund policy is unavailable")
        reference = _uuid(reference)
        if not isinstance(request_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id) is None:
            raise PaymentVerificationError("invalid refund request ID")
        key = (reference, request_id)
        if key in self._refunds:
            return self._refunds[key]
        if len(self._refunds) >= 4096:
            raise PaymentVerificationError("local refund contract capacity reached")
        support = await self._request("GET", "/transaction/" + reference + "/cancel-supported")
        if type(support.get("supported")) is not bool:
            raise PaymentVerificationError("refund capability missing")
        if not support["supported"]:
            result = RefundOutcome("unsupported", reference)
        else:
            # Unknown outcomes are sticky before POST; a retry never repeats it.
            self._refunds[key] = RefundOutcome("unknown", reference)
            try:
                values = await self._request("POST", "/transaction/" + reference + "/cancel")
                if (values.get("transactionId") != reference or type(values.get("accepted")) is not bool
                        or type(values.get("manualControlRequired")) is not bool):
                    raise PaymentVerificationError("invalid refund response")
                state = ("manual_control_required" if values["manualControlRequired"]
                         else "accepted" if values["accepted"] else "declined")
                result = RefundOutcome(state, reference)
            except asyncio.CancelledError:
                raise
            except Exception:
                result = RefundOutcome("unknown", reference)
        self._refunds[key] = result
        return result
