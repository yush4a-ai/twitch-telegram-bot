"""Настройки и сборка реального Platega: ключи без разрешения ничего не включают."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.config import ConfigError, platega_settings
from bot.platega_runtime import build_platega_provider
from bot.plan_catalog import BillingRuntimePolicy
from tests.test_platega_provider import MERCHANT, SECRET

EXTERNAL_POLICY = BillingRuntimePolicy("sandbox", True, True, False, True, True)


def config(**overrides):
    values = {
        "platega_merchant_id": MERCHANT,
        "platega_secret": SECRET,
        "platega_hosted_hosts": frozenset({"pay.platega.io"}),
        "platega_status_methods": (("SBPQR", "sbp"),),
        "oauth_public_base_url": "https://worker-production-cee5.up.railway.app",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class PlategaSettingsTests(unittest.TestCase):
    def test_settings_are_empty_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = platega_settings()

        self.assertIsNone(settings.merchant_id)
        self.assertIsNone(settings.secret)
        self.assertEqual(settings.hosted_hosts, frozenset({"pay.platega.io"}))
        self.assertEqual(dict(settings.status_methods), {"SBPQR": "sbp"})

    def test_credentials_are_validated_before_use(self):
        with patch.dict(os.environ, {"PLATEGA_MERCHANT_ID": MERCHANT,
                                     "PLATEGA_SECRET": SECRET}, clear=True):
            settings = platega_settings()
        self.assertEqual((settings.merchant_id, settings.secret), (MERCHANT, SECRET))

        for bad_merchant in ("not-a-uuid", "11111111-1111-4111-8111-11111111111A",
                             MERCHANT.replace("-", "")):
            with self.subTest(merchant=bad_merchant):
                with patch.dict(os.environ, {"PLATEGA_MERCHANT_ID": bad_merchant,
                                             "PLATEGA_SECRET": SECRET}, clear=True):
                    with self.assertRaises(ConfigError):
                        platega_settings()
        for bad_secret in ("short", "x" * 513, "секрет-на-кириллице-длинный"):
            with self.subTest(secret=bad_secret[:12]):
                with patch.dict(os.environ, {"PLATEGA_MERCHANT_ID": MERCHANT,
                                             "PLATEGA_SECRET": bad_secret}, clear=True):
                    with self.assertRaises(ConfigError):
                        platega_settings()

    def test_hosts_and_status_methods_are_bounded(self):
        with patch.dict(os.environ, {
            "PLATEGA_HOSTED_HOSTS": "pay.platega.io, checkout.platega.io",
            "PLATEGA_STATUS_METHODS": "SBPQR:sbp,CARD:bank_card",
        }, clear=True):
            settings = platega_settings()
        self.assertEqual(settings.hosted_hosts,
                         frozenset({"pay.platega.io", "checkout.platega.io"}))
        self.assertEqual(dict(settings.status_methods),
                         {"SBPQR": "sbp", "CARD": "bank_card"})

        for bad_hosts in ("https://pay.platega.io", "pay.platega.io/../evil", "pay.platega.io:443",
                          "PAY.platega.io", ""):
            with self.subTest(hosts=bad_hosts):
                with patch.dict(os.environ, {"PLATEGA_HOSTED_HOSTS": bad_hosts}, clear=True):
                    with self.assertRaises(ConfigError):
                        platega_settings()
        for bad_methods in ("SBPQR:card", "SBPQR:stars", "SBPQR", ":sbp", "SBPQR:sbp,CARD:card"):
            with self.subTest(methods=bad_methods):
                with patch.dict(os.environ, {"PLATEGA_STATUS_METHODS": bad_methods}, clear=True):
                    with self.assertRaises(ConfigError):
                        platega_settings()


class PlategaProviderWiringTests(unittest.IsolatedAsyncioTestCase):
    def test_no_provider_without_credentials_or_explicit_permission(self):
        self.assertIsNone(build_platega_provider(config(), BillingRuntimePolicy()))
        self.assertIsNone(build_platega_provider(
            config(), BillingRuntimePolicy("sandbox", True, False, False, True, True)))
        self.assertIsNone(build_platega_provider(
            config(platega_merchant_id=None), EXTERNAL_POLICY))
        self.assertIsNone(build_platega_provider(
            config(platega_secret=None), EXTERNAL_POLICY))
        self.assertIsNone(build_platega_provider(
            config(oauth_public_base_url=None), EXTERNAL_POLICY))

    async def test_provider_is_ready_for_money_and_reconciliation(self):
        provider = build_platega_provider(config(), EXTERNAL_POLICY)
        try:
            self.assertIsNotNone(provider)
            self.assertEqual(provider.provider_id, "platega")
            self.assertIs(provider.money_capable, True)
            self.assertIs(provider.network_free, False)
            self.assertIs(provider.can_reconcile, True)
            self.assertIs(provider.requires_buyer_name, True)
            self.assertIs(provider.has_buyer_name(101), False)
            provider.remember_buyer(101, "@buyer")
            self.assertIs(provider.has_buyer_name(101), True)
        finally:
            await provider.close()


if __name__ == "__main__":
    unittest.main()
