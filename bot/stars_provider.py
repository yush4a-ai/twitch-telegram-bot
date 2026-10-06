"""Telegram Stars contract restricted to an explicitly network-free sender."""

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from .billing_models import Money, ServerOrderSnapshot, VerifiedPaymentEvidence
from .billing_provider import CheckoutSession, PaymentCreationUnknown, PaymentVerificationError, RefundOutcome
from .plan_catalog import BillingRuntimePolicy


_PAYLOAD = re.compile(r"ts1:([a-f0-9]{32}):([a-f0-9]{32})\Z")
_CHARGE = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
# РЎСЃС‹Р»РєР° РЅР° СЃС‡С‘С‚ РґРѕР»Р¶РЅР° РІРµСЃС‚Рё РЅР° РґРѕРјРµРЅ Telegram: РµС‘ РѕС‚РєСЂС‹РІР°РµС‚ РєР»РёРµРЅС‚, Рё
# РїРѕРґРјРµРЅС‘РЅРЅС‹Р№ Р°РґСЂРµСЃ РЅРµ РґРѕР»Р¶РµРЅ РїРѕРїР°РґР°С‚СЊ РІ РёРЅС‚РµСЂС„РµР№СЃ.
_INVOICE_HOSTS = frozenset({"t.me", "telegram.me", "www.t.me"})


def is_telegram_invoice_link(link: object) -> bool:
    if not isinstance(link, str) or not link:
        return False
    try:
        parsed = urlsplit(link)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in _INVOICE_HOSTS


@dataclass(frozen=True)
class PrecheckoutDecision:
    ok: bool
    error_message: str | None = None


class TelegramStarsProvider:
    provider_id = "telegram_stars"
    can_reconcile = True

    def __init__(self, sender, runtime_policy: BillingRuntimePolicy):
        if not isinstance(runtime_policy, BillingRuntimePolicy):
            raise ValueError("invalid Stars runtime policy")
        self._sender = sender
        self._policy = runtime_policy
        # РџРѕРїС‹С‚РєРё СЃРѕР·РґР°РЅРёСЏ СЃС‡С‘С‚Р°: РїРѕРїС‹С‚РєР° РЅРµ РґРѕР»Р¶РЅР° РѕС‚РїСЂР°РІР»СЏС‚СЊСЃСЏ РґРІР°Р¶РґС‹, РЅРѕ Рё
        # РєРѕРїРёС‚СЊСЃСЏ РІРµС‡РЅРѕ РѕРЅР° РЅРµ РјРѕР¶РµС‚ вЂ” РёРЅР°С‡Рµ РїРѕСЃР»Рµ 4096 СЃС‡РµС‚РѕРІ РїСЂРѕРґР°Р¶Рё РІСЃС‚Р°СЋС‚.
        self._attempts: dict[str, float] = {}
        self._refunds = {}

    def _prune_attempts(self, now: float, *, ttl: float = 3600.0) -> None:
        expired = [key for key, stamp in self._attempts.items() if now - stamp > ttl]
        for key in expired:
            self._attempts.pop(key, None)
        if len(self._attempts) < 4096:
            return
        # РџРµСЂРµРїРѕР»РЅРµРЅРёРµ: РѕСЃРІРѕР±РѕР¶РґР°РµРј РїРѕР»РѕРІРёРЅСѓ СЃР°РјС‹С… СЃС‚Р°СЂС‹С… Р·Р°РїРёСЃРµР№, С‡С‚РѕР±С‹ РїСЂРёС‘Рј
        # РѕРїР»Р°С‚ РЅРµ РѕСЃС‚Р°РЅР°РІР»РёРІР°Р»СЃСЏ РґРѕ РїРµСЂРµР·Р°РїСѓСЃРєР° РїСЂРѕС†РµСЃСЃР°.
        for key, _stamp in sorted(self._attempts.items(), key=lambda item: item[1])[
            : max(1, len(self._attempts) // 2)
        ]:
            self._attempts.pop(key, None)

    @property
    def network_free(self):
        """Р“РѕС‚РѕРІ Р»Рё РїСЂРѕРІР°Р№РґРµСЂ Рє РґРµРЅРµР¶РЅС‹Рј РѕРїРµСЂР°С†РёСЏРј Р±РµР· РІРЅРµС€РЅРµРіРѕ С‚СЂР°РЅСЃРїРѕСЂС‚Р°.

        РЈ Р·РІС‘Р·Рґ РІРЅРµС€РЅРµРіРѕ РїР»Р°С‚С‘Р¶РЅРѕРіРѕ РїСЂРѕРІР°Р№РґРµСЂР° РЅРµС‚: СЃС‡С‘С‚ Рё РѕРїР»Р°С‚Сѓ РїСЂРѕРІРѕРґРёС‚ СЃР°Рј
        Telegram С‡РµСЂРµР· Bot API, РєРѕС‚РѕСЂС‹Р№ Р±РѕС‚ Рё С‚Р°Рє РёСЃРїРѕР»СЊР·СѓРµС‚. РџРѕСЌС‚РѕРјСѓ СЂРµР°Р»СЊРЅС‹Р№
        Р±РѕС‚ СЃС‡РёС‚Р°РµС‚СЃСЏ РїРѕРґС…РѕРґСЏС‰РёРј РѕС‚РїСЂР°РІРёС‚РµР»РµРј вЂ” РЅРѕ С‚РѕР»СЊРєРѕ РїСЂРё СЏРІРЅРѕРј СЂР°Р·СЂРµС€РµРЅРёРё
        РІР»Р°РґРµР»СЊС†Р° (``allow_public_stars``), Р° РЅРµ РїСЂРѕСЃС‚Рѕ РїРѕС‚РѕРјСѓ, С‡С‚Рѕ РµСЃС‚СЊ РєР»СЋС‡Рё.
        """
        if getattr(self._sender, "network_free", None) is True:
            return True
        return bool(
            isinstance(self._sender, Bot)
            and getattr(self._policy, "allow_public_stars", False)
        )

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
        """РЎС‡С‘С‚-СЃРѕРѕР±С‰РµРЅРёРµ РІ С‡Р°С‚ СЃ Р±РѕС‚РѕРј (РєРЅРѕРїРєР° В«РћРїР»Р°С‚РёС‚СЊВ»)."""
        return await self._create_payment(snapshot, attempt_id, prefer_link=False)

    async def create_link_payment(self, snapshot: ServerOrderSnapshot, attempt_id: str):
        """РЎСЃС‹Р»РєР° РЅР° СЃС‡С‘С‚ РґР»СЏ РјРёРЅРё-Р°РїРїР° (РѕС‚РєСЂС‹РІР°РµС‚СЃСЏ С‡РµСЂРµР· WebApp.openInvoice)."""
        return await self._create_payment(snapshot, attempt_id, prefer_link=True)

    async def _create_payment(
        self, snapshot: ServerOrderSnapshot, attempt_id: str, *, prefer_link: bool
    ):
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
        self._prune_attempts(time.monotonic())
        if attempt_id in self._attempts:
            raise PaymentCreationUnknown("invoice outcome unresolved; do not resend")
        self._attempts[attempt_id] = time.monotonic()
        title = "Viewer Plus" if snapshot.product.product_id == "viewer_plus" else "Streamer Plus"
        description = "РџРѕРґРїРёСЃРєР° РЅР° 1 РјРµСЃСЏС†"
        prices = [LabeledPrice(label=title, amount=snapshot.money.amount_minor)]
        # Telegram РїСЂРѕРІРѕРґРёС‚ РѕРїР»Р°С‚Сѓ С†РёС„СЂРѕРІС‹С… С‚РѕРІР°СЂРѕРІ СЃС‡С‘С‚РѕРј-СЃРѕРѕР±С‰РµРЅРёРµРј: Сѓ РЅРµРіРѕ
        # РµСЃС‚СЊ РєРЅРѕРїРєР° В«РћРїР»Р°С‚РёС‚СЊВ», РѕС‚РєСЂС‹РІР°СЋС‰Р°СЏ РїР»Р°С‚С‘Р¶РЅСѓСЋ С„РѕСЂРјСѓ. РњРёРЅРё-Р°РїРї СЃС‡С‘С‚-
        # СЃРѕРѕР±С‰РµРЅРёРµ РїРѕРєР°Р·Р°С‚СЊ РЅРµ РјРѕР¶РµС‚, РїРѕСЌС‚РѕРјСѓ РґР»СЏ РЅРµРіРѕ СЃРѕР·РґР°С‘Рј СЃСЃС‹Р»РєСѓ РЅР° СЃС‡С‘С‚,
        # РєРѕС‚РѕСЂСѓСЋ РєР»РёРµРЅС‚ РѕС‚РєСЂС‹РІР°РµС‚ С‡РµСЂРµР· WebApp.openInvoice.
        send_invoice = getattr(self._sender, "send_invoice", None)
        create_link = getattr(self._sender, "create_invoice_link", None)
        hosted_url = None
        if prefer_link and callable(create_link):
            try:
                link = await asyncio.wait_for(create_link(
                    title=title, description=description, payload=payload,
                    provider_token="", currency="XTR", prices=prices,
                ), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PaymentCreationUnknown("invoice link outcome unknown; do not resend") from None
            if not is_telegram_invoice_link(link):
                raise PaymentCreationUnknown("invalid invoice link")
            hosted_url = link
        elif callable(send_invoice):
            try:
                await asyncio.wait_for(send_invoice(
                    chat_id=snapshot.telegram_user_id, title=title,
                    description=description, payload=payload, provider_token="",
                    currency="XTR", prices=prices, start_parameter="subscription",
                    protect_content=True), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PaymentCreationUnknown("invoice outcome unknown; do not resend") from None
        elif callable(create_link):
            try:
                # createInvoiceLink РЅРµ РїСЂРёРЅРёРјР°РµС‚ start_parameter (РІ РѕС‚Р»РёС‡РёРµ РѕС‚
                # sendInvoice): Р»РёС€РЅРёР№ Р°СЂРіСѓРјРµРЅС‚ Р»РѕРјР°Р» РІС‹Р·РѕРІ РµС‰С‘ РґРѕ Telegram.
                link = await asyncio.wait_for(create_link(
                    title=title, description=description, payload=payload,
                    provider_token="", currency="XTR", prices=prices,
                ), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PaymentCreationUnknown("invoice link outcome unknown; do not resend") from None
            if not is_telegram_invoice_link(link):
                raise PaymentCreationUnknown("invalid invoice link")
            hosted_url = link
        else:
            raise PaymentCreationUnknown("no invoice transport")
        # sendInvoice РІРѕР·РІСЂР°С‰Р°РµС‚ СЃРѕРѕР±С‰РµРЅРёРµ, Р° РЅРµ РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ СЃРїРёСЃР°РЅРёСЏ.
        return CheckoutSession(snapshot.order_id, None, hosted_url, "pending", snapshot.checkout_expires_at)

    async def star_transactions(self, *, limit: int = 30, pages: int = 5):
        """РћРїР»Р°С‡РµРЅРЅС‹Рµ Р·РІС‘Р·РґРЅС‹Рµ СЃС‡РµС‚Р° Р±РѕС‚Р°: (charge_id, payload, amount, date).

        Telegram РїСЂРёСЃС‹Р»Р°РµС‚ СЃРѕРѕР±С‰РµРЅРёРµ РѕР± РѕРїР»Р°С‚Рµ РѕРґРёРЅ СЂР°Р·. Р•СЃР»Рё РѕРЅРѕ РїРѕС‚РµСЂСЏР»РѕСЃСЊ,
        РѕРїР»Р°С‚Р° РѕСЃС‚Р°С‘С‚СЃСЏ РІ РёСЃС‚РѕСЂРёРё С‚СЂР°РЅР·Р°РєС†РёР№ вЂ” РїРѕ РЅРµР№ РїР»Р°С‚С‘Р¶ РјРѕР¶РЅРѕ РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЊ.
        РЎРїРёСЃРѕРє РѕС‚РґР°С‘С‚СЃСЏ РїРѕСЃС‚СЂР°РЅРёС‡РЅРѕ Рё РІ С…СЂРѕРЅРѕР»РѕРіРёС‡РµСЃРєРѕРј РїРѕСЂСЏРґРєРµ, РїРѕСЌС‚РѕРјСѓ С‡РёС‚Р°РµРј
        СЃС‚СЂР°РЅРёС†С‹ РґРѕ РєРѕРЅС†Р°: РёРЅР°С‡Рµ СЃРІРµР¶РёРµ РѕРїР»Р°С‚С‹ РѕСЃС‚Р°Р»РёСЃСЊ Р±С‹ Р·Р° РѕРєРЅРѕРј РІС‹Р±РѕСЂРєРё.
        """
        getter = getattr(self._sender, "get_star_transactions", None)
        if not callable(getter):
            return []
        page_size = max(1, min(100, limit))
        collected = []
        offset = 0
        for _page in range(max(1, pages)):
            try:
                result = await asyncio.wait_for(
                    getter(limit=page_size, offset=offset), timeout=10
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                return collected
            transactions = list(getattr(result, "transactions", None) or ())
            for transaction in transactions:
                source = getattr(transaction, "source", None)
                payload = getattr(source, "invoice_payload", None)
                charge = getattr(transaction, "id", None)
                amount = getattr(transaction, "amount", None)
                moment = getattr(transaction, "date", None)
                if (not isinstance(payload, str) or not payload.startswith("ts1:")
                        or not isinstance(charge, str) or type(amount) is not int
                        or amount <= 0 or moment is None):
                    continue
                try:
                    observed = float(moment.timestamp())
                except (AttributeError, TypeError, ValueError, OverflowError):
                    observed = 0.0
                collected.append((charge, payload, amount, observed))
            if len(transactions) < page_size:
                break
            offset += page_size
        return collected

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
            return PrecheckoutDecision(valid, None if valid else "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґС‚РІРµСЂРґРёС‚СЊ Р·Р°РєР°Р·. РћС‚РєСЂРѕР№С‚Рµ С‚Р°СЂРёС„ РµС‰С‘ СЂР°Р·.")
        except (PaymentVerificationError, AttributeError, TypeError):
            return PrecheckoutDecision(False, "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґС‚РІРµСЂРґРёС‚СЊ Р·Р°РєР°Р·. РћС‚РєСЂРѕР№С‚Рµ С‚Р°СЂРёС„ РµС‰С‘ СЂР°Р·.")

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
