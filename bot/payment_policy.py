"""Каналы приёма денег: звёзды Telegram, внешние платежи и тестовый контур.

Модуль отделён от каталога тарифов намеренно: каталог — это то, что видит
человек, а здесь описан технический контур оплаты. Слова вроде «mock» в
пользовательских текстах запрещены проверкой копирайта, поэтому техническая
модель живёт отдельно от витрины.
"""

from dataclasses import dataclass

from .plan_catalog import BillingRuntimePolicy


@dataclass(frozen=True)
class PaymentChannel:
    """Один канал приёма денег: включён или нет, и по какой причине."""

    enabled: bool
    reason: str | None = None


def payment_channels(policy: BillingRuntimePolicy) -> dict[str, PaymentChannel]:
    """Три независимых канала: звёзды Telegram, внешние платежи, тестовый контур.

    Запрет банковского канала со стороны production-контракта
    (`PRODUCTION_PAYMENT_POLICY=off`) живёт в самой политике
    (`external_blocked_by_contract`), потому что витрина, подготовка платежа и
    провайдер должны видеть одно и то же состояние без дополнительных
    параметров.
    """
    if not isinstance(policy, BillingRuntimePolicy):
        raise ValueError("invalid billing runtime policy")

    def closed(reason: str) -> PaymentChannel:
        return PaymentChannel(False, reason)

    common = None
    if policy.mode == "offline":
        common = "mode_offline"
    elif not policy.target_verified:
        common = "target_unverified"
    elif not policy.period_approved:
        common = "period_unapproved"
    elif not policy.refund_policy_approved:
        common = "policy_unapproved"

    if common is not None:
        return {
            "stars": closed(common),
            "external": closed(common),
            "sandbox": closed(common),
        }

    if not policy.allow_invoice:
        stars = closed("invoice_disabled")
    elif not policy.allow_public_stars:
        stars = closed("public_stars_disabled")
    else:
        stars = PaymentChannel(True)
    if policy.external_blocked_by_contract:
        external = closed("contract_disabled")
    elif not policy.allow_external_create:
        external = closed("external_disabled")
    else:
        external = PaymentChannel(True)
    return {"stars": stars, "external": external, "sandbox": PaymentChannel(True)}
