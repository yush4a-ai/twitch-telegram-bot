"""Telegram Stars contract restricted to an explicitly network-free sender."""

import asyncio
import json
import math
import re
from dataclasses import dataclass

from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from .billing_models import Money, ServerOrderSnapshot, VerifiedPaymentEvidence
from .billing_provider import CheckoutSession, PaymentCreationUnknown, PaymentVerificationError, RefundOutcome
from .plan_catalog import BillingRuntimePolicy


_PAYLOAD = re.compile(r"ts1:([a-f0-9]{32}):([a-f0-9]{32})\Z")
_CHARGE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True)
class PrecheckoutDecision:
    ok: bool
    error_message: str | None = None


class TelegramStarsProvider:
    provider_id = "telegram_stars"
    can_reconcile = False

    def __init__(self, sender, runtime_policy: BillingRuntimePolicy):
        if not isinstance(runtime_policy, BillingRuntimePolicy):
            raise ValueError("invalid Stars runtime policy")
        self._sender = sender
        self._policy = runtime_policy
        self._attempts = set()
        self._refunds = {}

    @property
    def network_free(self):
        return getattr(self._sender, "network_free", False) is True

    def _require_ready(self):
        if not (self.network_free and self._policy.mode == "sandbox" and self._policy.target_verified
                and self._policy.allow_invoice and self._policy.period_approved and self._policy.refund_policy_approved):
            raise PaymentVerificationError("Stars operations disabled")

    @staticmethod
    def invoice_payload(order_id, attempt_id):
        payload = f"ts1:{order_id}:{attempt_id}"
        if _PAYLOAD.fullmatch(payload) is None:
            raise PaymentVerificationError("invalid Stars correlation")
        return payload

    @staticmethod
    def parse_payload(payload):
        match = _PAYLOAD.fullmatch(payload) if isinstance(payload, str) else None
        if match is None:
            raise PaymentVerificationError("invalid Stars payload")
        return match.groups()

    @staticmethod
    def _clock(now):
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise PaymentVerificationError("invalid Stars clock")

    @staticmethod
    def _binding(order):
        valid = (order.provider == "telegram_stars" and order.method == "stars" and order.currency == "XTR"
            and type(order.units) is int and order.units > 0 and type(order.telegram_user_id) is int
            and order.telegram_user_id > 0 and order.beneficiary_telegram_user_id == order.telegram_user_id
            and ((order.plan == "viewer_plus" and order.subject_kind == "viewer"
                  and order.subject_id == str(order.telegram_user_id) and order.broadcaster_id is None)
                 or (order.plan == "streamer_plus" and order.subject_kind == "streamer"
                     and isinstance(order.broadcaster_id, str) and order.broadcaster_id.isascii()
                     and order.broadcaster_id.isdecimal() and int(order.broadcaster_id) > 0
                     and order.subject_id == order.broadcaster_id)))
        if not valid:
            raise PaymentVerificationError("invalid frozen Stars buyer/product")
        try:
            product = json.loads(order.product_snapshot_json)
            valid = (product["product_id"] == order.plan and product["catalog_version"] == order.catalog_version
                and product["xtr"] == {"amount_minor": order.units, "currency": "XTR"}
                and product["period_rule"] == order.period_rule and product["period_rule_version"] == order.period_rule_version
                and product["auto_renew"] is False and order.period_rule in {"30_days", "calendar_month"}
                and isinstance(order.period_rule_version, str) and bool(order.period_rule_version))
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise PaymentVerificationError("unapproved frozen Stars product")

    async def create_payment(self, snapshot: ServerOrderSnapshot, attempt_id: str):
        self._require_ready()
        if (not isinstance(snapshot, ServerOrderSnapshot) or snapshot.provider != self.provider_id or snapshot.method != "stars"
                or snapshot.money != snapshot.product.xtr or snapshot.money is None or snapshot.money.currency != "XTR"
                or snapshot.product.product_id not in {"viewer_plus", "streamer_plus"} or snapshot.product.auto_renew is not False
                or snapshot.product.period_rule not in {"30_days", "calendar_month"} or not snapshot.product.period_rule_version
                or type(snapshot.telegram_user_id) is not int or snapshot.telegram_user_id <= 0
                or snapshot.beneficiary_telegram_user_id != snapshot.telegram_user_id
                or not ((snapshot.product.product_id == "viewer_plus" and snapshot.subject.kind == "viewer"
                         and snapshot.subject.subject_id == str(snapshot.telegram_user_id) and snapshot.broadcaster_id is None)
                        or (snapshot.product.product_id == "streamer_plus" and snapshot.subject.kind == "streamer"
                            and snapshot.subject.subject_id == snapshot.broadcaster_id and isinstance(snapshot.broadcaster_id, str)
                            and snapshot.broadcaster_id.isdecimal() and int(snapshot.broadcaster_id) > 0))):
            raise PaymentVerificationError("invalid Stars server snapshot")
        payload = self.invoice_payload(snapshot.order_id, attempt_id)
        if attempt_id in self._attempts or len(self._attempts) >= 4096:
            raise PaymentCreationUnknown("invoice outcome unresolved; do not resend")
        self._attempts.add(attempt_id)
        title = "Viewer Plus" if snapshot.product.product_id == "viewer_plus" else "Streamer Plus"
        try:
            await asyncio.wait_for(self._sender.send_invoice(chat_id=snapshot.telegram_user_id, title=title,
                description="Подписка на 1 месяц", payload=payload, provider_token="", currency="XTR",
                prices=[LabeledPrice(label=title, amount=snapshot.money.amount_minor)],
                start_parameter="subscription", protect_content=True), timeout=5)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PaymentCreationUnknown("invoice outcome unknown; do not resend") from None
        # sendInvoice returns a message, not a payment charge ID.
        return CheckoutSession(snapshot.order_id, None, None, "pending", snapshot.checkout_expires_at)

    def validate_precheckout(self, query, order, *, now):
        self._require_ready()
        self._clock(now)
        try:
            if not isinstance(query, PreCheckoutQuery) or order is None:
                raise PaymentVerificationError("invalid precheckout source")
            self._binding(order)
            order_id, _ = self.parse_payload(query.invoice_payload)
            valid = (query.from_user.id == order.telegram_user_id and not query.from_user.is_bot
                and query.currency == "XTR" and type(query.total_amount) is int and query.total_amount == order.units
                and order_id == order.order_id and order.status == "pending" and order.financial_status == "pending"
                and order.created_at <= now < order.checkout_expires_at
                and query.shipping_option_id is None and query.order_info is None)
            return PrecheckoutDecision(valid, None if valid else "Не удалось подтвердить заказ. Откройте Plus ещё раз.")
        except (PaymentVerificationError, AttributeError, TypeError):
            return PrecheckoutDecision(False, "Не удалось подтвердить заказ. Откройте Plus ещё раз.")

    def _message_evidence(self, message, order, *, refund, now, expected_charge=None):
        self._require_ready()
        self._clock(now)
        if not isinstance(message, Message) or order is None:
            raise PaymentVerificationError("verified Bot API message required")
        self._binding(order)
        payment = message.refunded_payment if refund else message.successful_payment
        if (payment is None or message.chat.type != "private" or message.chat.id != order.telegram_user_id
                or message.from_user is None or message.from_user.id != order.telegram_user_id or message.from_user.is_bot
                or message.forward_origin is not None):
            raise PaymentVerificationError("Stars message buyer mismatch")
        order_id, attempt_id = self.parse_payload(payment.invoice_payload)
        charge = payment.telegram_payment_charge_id
        if (order_id != order.order_id or payment.currency != "XTR" or type(payment.total_amount) is not int
                or payment.total_amount != order.units or not isinstance(charge, str) or _CHARGE.fullmatch(charge) is None
                or (refund and (not isinstance(expected_charge, str) or charge != expected_charge))
                or (not refund and (payment.is_recurring or payment.is_first_recurring or payment.subscription_expiration_date is not None))):
            raise PaymentVerificationError("Stars payment fields mismatch")
        return VerifiedPaymentEvidence(self.provider_id, charge, order_id, attempt_id,
            Money(payment.total_amount, "XTR"), "stars", "refunded" if refund else "confirmed",
            "refunded_payment" if refund else "successful_payment", now)

    def successful_payment_to_evidence(self, message, order, *, now):
        return self._message_evidence(message, order, refund=False, now=now)

    def refunded_payment_to_evidence(self, message, order, *, expected_charge, now):
        return self._message_evidence(message, order, refund=True, expected_charge=expected_charge, now=now)

    async def refund_payment(self, reference, request_key, *, user_id):
        self._require_ready()
        if (type(user_id) is not int or user_id <= 0 or not isinstance(reference, str) or _CHARGE.fullmatch(reference) is None
                or not isinstance(request_key, str) or not 1 <= len(request_key) <= 128):
            raise PaymentVerificationError("invalid Stars refund")
        key = (reference, request_key, user_id)
        if key in self._refunds:
            return self._refunds[key]
        if len(self._refunds) >= 4096:
            return RefundOutcome("manual_control_required", reference)
        self._refunds[key] = RefundOutcome("unknown", reference)
        try:
            result = await asyncio.wait_for(self._sender.refund_star_payment(user_id=user_id,
                telegram_payment_charge_id=reference), timeout=5)
            state = "accepted" if result is True else "declined" if result is False else "unknown"
        except asyncio.CancelledError:
            raise
        except Exception:
            state = "unknown"
        self._refunds[key] = RefundOutcome(state, reference)
        return self._refunds[key]
