"""Три канала оплаты должны быть явными и независимыми.

Аудит (B1, R2): контракт допуска объявлял «деньги выключены»
(`PRODUCTION_PAYMENT_POLICY=off`), но денежную политику задавали только
переменные `BILLING_*`, и они с контрактом не сверялись. Здесь фиксируется
честная модель: звёзды, внешние платежи и тестовый контур управляются отдельно,
а значение контракта действительно запрещает банковский канал.
"""
import os
import unittest
from unittest.mock import patch

from bot.config import first_release_payment_policy
from bot.plan_catalog import BillingRuntimePolicy, payment_channels


class PaymentChannelModelTests(unittest.TestCase):
    def test_default_state_has_every_channel_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            policy = first_release_payment_policy()
        channels = payment_channels(policy)

        self.assertFalse(channels["stars"].enabled)
        self.assertFalse(channels["external"].enabled)
        self.assertFalse(channels["sandbox"].enabled)
        self.assertEqual(channels["stars"].reason, "mode_offline")

    def test_provider_credentials_alone_never_open_the_bank_channel(self):
        """Наличие ключей провайдера не является разрешением на деньги."""
        with patch.dict(os.environ, {"PLATEGA_MERCHANT_ID": "m", "PLATEGA_SECRET": "s",
                                     "PLATEGA_API_KEY": "k"}, clear=True):
            policy = first_release_payment_policy()
        channels = payment_channels(policy)

        self.assertFalse(channels["external"].enabled)
        self.assertEqual(channels["external"].reason, "mode_offline")

    def test_the_contract_switch_really_disables_the_bank_channel(self):
        with patch.dict(os.environ, {"BILLING_MODE": "sandbox", "BILLING_TARGET_VERIFIED": "1",
                                     "BILLING_ALLOW_EXTERNAL": "1", "BILLING_ALLOW_INVOICE": "1",
                                     "BILLING_PERIOD_APPROVED": "1",
                                     "BILLING_REFUND_APPROVED": "1", "BILLING_PUBLIC_STARS": "1"},
                        clear=True):
            with_contract = payment_channels(first_release_payment_policy(contract_policy="off"))
            without_contract = payment_channels(first_release_payment_policy())

        self.assertFalse(with_contract["external"].enabled)
        self.assertEqual(with_contract["external"].reason, "contract_disabled")
        self.assertTrue(without_contract["external"].enabled)
        # Звёзды — отдельный канал: контракт их не выключает.
        self.assertTrue(with_contract["stars"].enabled)
    def test_the_stars_channel_needs_its_own_explicit_flag(self):
        with patch.dict(os.environ, {"BILLING_MODE": "sandbox", "BILLING_TARGET_VERIFIED": "1",
                                     "BILLING_ALLOW_INVOICE": "1", "BILLING_PERIOD_APPROVED": "1",
                                     "BILLING_REFUND_APPROVED": "1"}, clear=True):
            policy = first_release_payment_policy()

        channels = payment_channels(policy)

        self.assertFalse(channels["stars"].enabled)
        self.assertEqual(channels["stars"].reason, "public_stars_disabled")

    def test_the_test_channel_never_opens_outside_a_verified_contour(self):
        offline = payment_channels(BillingRuntimePolicy())
        sandbox = payment_channels(BillingRuntimePolicy(
            mode="sandbox", target_verified=True, period_approved=True,
            refund_policy_approved=True,
        ))

        self.assertFalse(offline["sandbox"].enabled)
        self.assertTrue(sandbox["sandbox"].enabled)


if __name__ == "__main__":
    unittest.main()
