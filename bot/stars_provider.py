"""Telegram Stars contract restricted to an explicitly network-free sender."""

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass

from aiogram import Bot
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from .billing_models import Money, ServerOrderSnapshot, VerifiedPaymentEvidence
from .billing_provider import CheckoutSession, PaymentCreationUnknown, PaymentVerificationError, RefundOutcome
from .plan_catalog import BillingRuntimePolicy


_PAYLOAD = re.compile(r"ts1:([a-f0-9]{32}):([a-f0-9]{32})\Z")
_CHARGE = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


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
        # Попытки создания счёта: попытка не должна отправляться дважды, но и
        # копиться вечно она не может — иначе после 4096 счетов продажи встают.
        self._attempts: dict[str, float] = {}
        self._refunds = {}

    def _prune_attempts(self, now: float, *, ttl: float = 3600.0) -> None:
        expired = [key for key, stamp in self._attempts.items() if now - stamp > ttl]
        for key in expired:
            self._attempts.pop(key, None)
        if len(self._attempts) < 4096:
            return
        # Переполнение: освобождаем половину самых старых записей, чтобы приём
        # оплат не останавливался до перезапуска процесса.
        for key, _stamp in sorted(self._attempts.items(), key=lambda item: item[1])[
            : max(1, len(self._attempts) // 2)
        ]:
            self._attempts.pop(key, None)

    @property
    def network_free(self):
        """Готов ли провайдер к денежным операциям без внешнего транспорта.

        У звёзд внешнего платёжного провайдера нет: счёт и оплату проводит сам
        Telegram через Bot API, который бот и так использует. Поэтому реальный
        бот считается подходящим отправителем — но только при явном разрешении
        владельца (``allow_public_stars``), а не просто потому, что есть ключи.
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
        description = "Подписка на 1 месяц"
        prices = [LabeledPrice(label=title, amount=snapshot.money.amount_minor)]
        # Telegram проводит оплату цифровых товаров счётом-сообщением: у него
        # есть кнопка «Оплатить», открывающая платёжную форму. Ссылка на счёт
        # для звёзд такую форму не открывает («payment method is not available»),
        # поэтому ссылку оставляем только тем клиентам, кто не умеет отправлять
        # счёт сообщением (например, мини-аппу).
        send_invoice = getattr(self._sender, "send_invoice", None)
        create_link = getattr(self._sender, "create_invoice_link", None)
        hosted_url = None
        if callable(send_invoice):
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
                # createInvoiceLink не принимает start_parameter (в отличие от
                # sendInvoice): лишний аргумент ломал вызов ещё до Telegram.
                link = await asyncio.wait_for(create_link(
                    title=title, description=description, payload=payload,
                    provider_token="", currency="XTR", prices=prices,
                ), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise PaymentCreationUnknown("invoice link outcome unknown; do not resend") from None
            if not isinstance(link, str) or not link.startswith("https://"):
                raise PaymentCreationUnknown("invalid invoice link")
            hosted_url = link
        else:
            raise PaymentCreationUnknown("no invoice transport")
        # sendInvoice возвращает сообщение, а не идентификатор списания.
        return CheckoutSession(snapshot.order_id, None, hosted_url, "pending", snapshot.checkout_expires_at)

    async def star_transactions(self, *, limit: int = 30, pages: int = 5):
        """Оплаченные звёздные счета бота: (charge_id, payload, amount, date).

        Telegram присылает сообщение об оплате один раз. Если оно потерялось,
        оплата остаётся в истории транзакций — по ней платёж можно восстановить.
        Список отдаётся постранично и в хронологическом порядке, поэтому читаем
        страницы до конца: иначе свежие оплаты остались бы за окном выборки.
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
            return PrecheckoutDecision(valid, None if valid else "Не удалось подтвердить заказ. Откройте тариф ещё раз.")
        except (PaymentVerificationError, AttributeError, TypeError):
            return PrecheckoutDecision(False, "Не удалось подтвердить заказ. Откройте тариф ещё раз.")

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
