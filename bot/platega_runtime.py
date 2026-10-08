"""Сборка реального Platega: ключи из личного кабинета плюс разрешение владельца.

Модуль намеренно отделён от `main`, чтобы правило «ключи сами по себе ничего не
включают» можно было проверить тестом, а не глазами.
"""

from __future__ import annotations

from .platega_provider import PlategaProvider
from .platega_transport import PlategaHttpTransport
from .plan_catalog import BillingRuntimePolicy

# Куда возвращается человек после оплаты: приложение, а не платёжная страница.
RETURN_PATH = "/app"


def build_platega_provider(config, policy: BillingRuntimePolicy) -> PlategaProvider | None:
    """Создаёт провайдера только при ключах и явном разрешении банковского канала."""
    if not isinstance(policy, BillingRuntimePolicy):
        raise ValueError("invalid billing runtime policy")
    if policy.mode == "offline" or not policy.allow_external_create:
        return None
    merchant = getattr(config, "platega_merchant_id", None)
    secret = getattr(config, "platega_secret", None)
    hosts = frozenset(getattr(config, "platega_hosted_hosts", ()) or ())
    methods = dict(getattr(config, "platega_status_methods", ()) or ())
    base = (getattr(config, "oauth_public_base_url", None) or "").strip().rstrip("/")
    if not merchant or not secret or not hosts or not base.startswith("https://"):
        return None
    return PlategaProvider(
        PlategaHttpTransport(),
        merchant, secret,
        hosted_hosts=hosts,
        runtime_policy=policy,
        status_methods=methods,
        return_url=base + RETURN_PATH,
        failed_url=base + RETURN_PATH,
    )
