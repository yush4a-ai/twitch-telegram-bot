"""Provider contracts exercised with an explicit network-free transport."""

import json
import unittest
from dataclasses import replace
from decimal import Decimal

from multidict import CIMultiDict

from bot.billing_models import BillingSubject, Money, ServerOrderSnapshot
from bot.billing_provider import PaymentCreationRejected, PaymentCreationUnknown, PaymentVerificationError, ProviderHttpResponse, ProviderRateLimited
from bot.platega_provider import PlategaProvider
from bot.plan_catalog import BillingRuntimePolicy, get_product


MERCHANT = "11111111-1111-4111-8111-111111111111"
TRANSACTION = "22222222-2222-4222-8222-222222222222"
SECRET = "local-contract-fixture-not-a-real-merchant-secret"
ORDER = "a" * 32
ATTEMPT = "b" * 32


def snapshot(method="sbp", product="viewer_plus"):
    item = get_product(product)
    return ServerOrderSnapshot(ORDER, 101, 101,
        BillingSubject("viewer", "101") if product == "viewer_plus" else BillingSubject("streamer", "11"),
        None if product == "viewer_plus" else "11", item, item.rub,
        "platega", method, "terms-v1", 100, 1000)


def response(values, *, status=200, headers=None):
    return ProviderHttpResponse(status, headers or {}, json.dumps(values).encode())


class FakeTransport:
    network_free = True

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, path, *, json=None, headers, timeout):
        self.calls.append((method, path, json, headers, timeout))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def provider(transport, policy=None, **kwargs):
    return PlategaProvider(transport, MERCHANT, SECRET,
        hosted_hosts=frozenset({"pay.platega.io"}),
        runtime_policy=policy or BillingRuntimePolicy("sandbox", True, True, False, True, True),
        buyer_names={101: "@verified_buyer"},
        return_url="https://worker-staging-2f74.up.railway.app/app",
        failed_url="https://worker-staging-2f74.up.railway.app/app", **kwargs)


def status_body(**changes):
    value = {"id": TRANSACTION, "status": "CONFIRMED", "mechantId": MERCHANT,
        "paymentDetails": {"amount": 150, "currency": "RUB"},
        "paymentMethod": "SBPQR", "payload": json.dumps({"order_id": ORDER, "attempt_id": ATTEMPT})}
    value.update(changes)
    return value


class PlategaProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sbp_card_server_money_and_hosted_link_contract(self):
        for method, product, method_id, amount in (("sbp", "viewer_plus", 2, 150),
                                                  ("bank_card", "streamer_plus", 11, 300)):
            with self.subTest(method=method):
                transport = FakeTransport(response({"transactionId": TRANSACTION,
                    "redirect": "https://pay.platega.io/checkout?id=" + TRANSACTION,
                    "status": "PENDING", "expiresIn": "00:15:00"}))
                checkout = await provider(transport).create_payment(snapshot(method, product), ATTEMPT)
                self.assertEqual((checkout.order_id, checkout.reference, checkout.status), (ORDER, TRANSACTION, "pending"))
                self.assertTrue(checkout.hosted_url.startswith("https://pay.platega.io/"))
                self.assertEqual(checkout.checkout_expires_at, 1000)
                verb, path, payload, headers, timeout = transport.calls[0]
                self.assertEqual((verb, path, timeout), ("POST", "/transaction/process", 5))
                self.assertEqual(payload["paymentMethod"], method_id)
                self.assertEqual(payload["paymentDetails"], {"amount": amount, "currency": "RUB"})
                self.assertIs(type(payload["paymentDetails"]["amount"]), int)
                self.assertEqual(payload["orderId"], ORDER)
                self.assertEqual(json.loads(payload["payload"]), {"order_id": ORDER, "attempt_id": ATTEMPT})
                self.assertEqual(payload["metadata"], {"userId": "101", "userName": "@verified_buyer"})
                self.assertNotIn("id", payload)
                self.assertEqual(payload["return"], "https://worker-staging-2f74.up.railway.app/app")
                self.assertEqual(payload["failedUrl"], payload["return"])
                self.assertNotIn(SECRET, checkout.hosted_url)
                self.assertEqual(headers["X-MerchantId"], MERCHANT)

    async def test_v2_url_is_normalized_without_changing_request_method(self):
        transport = FakeTransport(response({"transactionId": TRANSACTION, "url": "https://pay.platega.io/pay",
                                            "status": "PENDING", "expiresIn": "00:10:00"}))
        self.assertEqual((await provider(transport).create_payment(snapshot(), ATTEMPT)).hosted_url, "https://pay.platega.io/pay")
        self.assertEqual(transport.calls[0][1], "/transaction/process")

    def test_callback_accepts_the_provider_payload_binding(self):
        """Провайдер присылает нашу привязку к заказу: она проверяется, а не отбрасывается."""
        adapter = provider(FakeTransport())
        headers = {"X-MerchantId": MERCHANT, "X-Secret": SECRET}
        body = json.dumps({
            "id": TRANSACTION, "amount": 162.75, "currency": "RUB",
            "status": "CONFIRMED", "paymentMethod": 2,
            "payload": json.dumps({"order_id": ORDER, "attempt_id": ATTEMPT}, separators=(",", ":")),
        }).encode()
        notice = adapter.handle_callback(body, headers)
        self.assertEqual((notice.transaction_id, notice.raw_status), (TRANSACTION, "CONFIRMED"))

        for bad_payload in ("not json", json.dumps({"order_id": ORDER}),
                            json.dumps({"order_id": "x", "attempt_id": ATTEMPT})):
            with self.subTest(payload=bad_payload):
                broken = json.dumps({
                    "id": TRANSACTION, "amount": 150, "currency": "RUB",
                    "status": "CONFIRMED", "payload": bad_payload,
                }).encode()
                with self.assertRaises(PaymentVerificationError):
                    adapter.handle_callback(broken, headers)

        extra = json.dumps({
            "id": TRANSACTION, "amount": 150, "currency": "RUB",
            "status": "CONFIRMED", "unexpected": 1,
        }).encode()
        with self.assertRaises(PaymentVerificationError):
            adapter.handle_callback(extra, headers)

    async def test_provider_rejection_is_not_an_unknown_outcome(self):
        """Отказ провайдера отличается от неизвестного исхода: повтор не нужен."""
        rejection = {"code": "Common:VAL_0001", "type": 4001, "message": "Wrong input parameters",
                     "data": [{"key": "paymentMethod", "message": "Card"}]}
        adapter = provider(FakeTransport(response(rejection, status=400)))
        with self.assertRaises(PaymentCreationRejected) as caught:
            await adapter.create_payment(snapshot("bank_card", "viewer_plus"), ATTEMPT)
        self.assertEqual(caught.exception.reason, "method_unavailable")

        other = provider(FakeTransport(response({"message": "bad request"}, status=400)))
        with self.assertRaises(PaymentCreationRejected) as caught_other:
            await other.create_payment(snapshot(), ATTEMPT)
        self.assertEqual(caught_other.exception.reason, "rejected")

        broken = provider(FakeTransport(response({}, status=500)))
        with self.assertRaises(PaymentCreationUnknown):
            await broken.create_payment(snapshot(), ATTEMPT)

    async def test_unknown_status_method_does_not_block_the_payment(self):
        """Новое название способа в статусе не мешает зачислению оплаты."""
        adapter = provider(FakeTransport(response(status_body(paymentMethod="SOMETHING_NEW"))))
        evidence = await adapter.get_payment_status(TRANSACTION)
        self.assertEqual((evidence.method, evidence.status), ("unknown", "confirmed"))
        self.assertEqual(evidence.money.amount_minor, 15000)

    async def test_provider_commission_is_subtracted_before_matching_the_order(self):
        # Покупатель платит сумму заказа плюс комиссию: заказу соответствует
        # сумма без комиссии, иначе подтверждённая оплата уходит в ручную проверку.
        body = status_body(paymentDetails={"amount": 162.75, "currency": "RUB"},
                           comission=12.75)
        evidence = await provider(FakeTransport(response(body))).get_payment_status(TRANSACTION)
        self.assertEqual((evidence.money.amount_minor, evidence.money.currency), (15000, "RUB"))

        plain = await provider(FakeTransport(response(status_body()))).get_payment_status(TRANSACTION)
        self.assertEqual(plain.money.amount_minor, 15000)

        # Streamer Plus за 300 ₽: та же комиссия 8,5% (25,50 ₽), заказу
        # соответствует 300 ₽. Проверка не требует реальной оплаты.
        streamer = status_body(paymentDetails={"amount": 325.5, "currency": "RUB"},
                               comission=25.5)
        adapter = provider(FakeTransport(response(streamer)))
        evidence = await adapter.get_payment_status(TRANSACTION)
        self.assertEqual(evidence.money.amount_minor, 30000)

        for bad in (200.0, -1.0, "12.75"):
            with self.subTest(commission=bad):
                broken = provider(FakeTransport(response(status_body(comission=bad))))
                with self.assertRaises(PaymentVerificationError):
                    await broken.get_payment_status(TRANSACTION)

    async def test_provider_merchant_id_inside_the_hosted_link_is_allowed(self):
        # Platega сама добавляет идентификатор мерчанта в ссылку оплаты: это не
        # секрет, и рабочий счёт нельзя из-за него отбрасывать.
        transport = FakeTransport(response({"transactionId": TRANSACTION,
            "redirect": "https://pay.platega.io/checkout?mh=" + MERCHANT,
            "status": "PENDING", "expiresIn": ""}))
        checkout = await provider(transport).create_payment(snapshot(), ATTEMPT)
        self.assertEqual(checkout.hosted_url, "https://pay.platega.io/checkout?mh=" + MERCHANT)
        self.assertNotIn(SECRET, checkout.hosted_url)
        self.assertEqual(checkout.checkout_expires_at, 1000)

    async def test_schema_auth_and_unknown_creation_fail_closed(self):
        for bad_url in ("http://pay.platega.io/", "https://pay.platega.io.attacker.test/",
                        "https://user:password@pay.platega.io/", "https://127.0.0.1/pay", "javascript:alert(1)",
                        "https://pay.platega.io/?secret=" + SECRET, "https://pay.platega.io:444/pay"):
            with self.subTest(url=bad_url):
                transport = FakeTransport(response({"transactionId": TRANSACTION, "redirect": bad_url,
                    "status": "PENDING", "expiresIn": "00:10:00"}))
                adapter = provider(transport)
                with self.assertRaises(PaymentCreationUnknown):
                    await adapter.create_payment(snapshot(), ATTEMPT)
                with self.assertRaises(PaymentCreationUnknown):
                    await adapter.create_payment(snapshot(), ATTEMPT)
                self.assertEqual(len(transport.calls), 1)
        transport = FakeTransport(TimeoutError("connection outcome unknown"))
        adapter = provider(transport)
        with self.assertRaises(PaymentCreationUnknown):
            await adapter.create_payment(snapshot(), ATTEMPT)
        with self.assertRaises(PaymentCreationUnknown):
            await adapter.create_payment(snapshot(), ATTEMPT)
        self.assertEqual(len(transport.calls), 1)

    async def test_default_offline_and_nonlocal_transport_never_send(self):
        transport = FakeTransport()
        with self.assertRaises(PermissionError):
            await provider(transport, BillingRuntimePolicy()).create_payment(snapshot(), ATTEMPT)
        transport.network_free = False
        with self.assertRaises(PermissionError):
            await provider(transport).create_payment(snapshot(), ATTEMPT)
        self.assertEqual(transport.calls, [])
        with self.assertRaises(PaymentVerificationError):
            await provider(FakeTransport()).create_payment(replace(snapshot(), money=Money(1, "RUB")), ATTEMPT)
        for invalid in (replace(snapshot(), telegram_user_id=True, beneficiary_telegram_user_id=True),
                        replace(snapshot(), beneficiary_telegram_user_id=202),
                        replace(snapshot(), order_id=True),
                        replace(snapshot(), subject=BillingSubject("viewer", "202"))):
            with self.subTest(invalid=invalid):
                local = FakeTransport()
                with self.assertRaises(PaymentVerificationError):
                    await provider(local).create_payment(invalid, ATTEMPT)
                self.assertEqual(local.calls, [])

    async def test_canonical_get_requires_all_critical_fields_and_exact_decimal(self):
        transport = FakeTransport(ProviderHttpResponse(200, {},
            json.dumps(status_body()).replace('"amount": 150', '"amount": 150.00').encode()))
        evidence = await provider(transport).get_payment_status(TRANSACTION, now=150)
        self.assertEqual((evidence.order_id, evidence.attempt_id, evidence.money, evidence.method, evidence.status),
            (ORDER, ATTEMPT, Money(15000, "RUB"), "sbp", "confirmed"))
        self.assertEqual(transport.calls[0][:2], ("GET", "/transaction/" + TRANSACTION))
        for body in (status_body(mechantId="foreign"), status_body(id=MERCHANT),
                     status_body(status="SOMETHING_NEW"),
                     status_body(paymentDetails={"amount": True, "currency": "RUB"}),
                     status_body(paymentDetails={"amount": 150.001, "currency": "RUB"}),
                     status_body(paymentDetails={"amount": 150, "currency": "USD"}),
                     status_body(payload="unrelated")):
            with self.subTest(body=body):
                with self.assertRaises(PaymentVerificationError):
                    await provider(FakeTransport(response(body))).get_payment_status(TRANSACTION, now=150)
        missing = status_body(); missing.pop("mechantId"); missing["merchantId"] = MERCHANT
        with self.assertRaises(PaymentVerificationError):
            await provider(FakeTransport(response(missing))).get_payment_status(TRANSACTION, now=150)

    async def test_callback_authenticated_notice_never_verified_evidence(self):
        adapter = provider(FakeTransport())
        headers = {"X-MerchantId": MERCHANT, "X-Secret": SECRET}
        body = json.dumps({"id": TRANSACTION, "amount": 150, "currency": "RUB", "status": "CHARGEBACKED", "paymentMethod": 2}).encode()
        first = adapter.handle_callback(body, headers)
        second = adapter.handle_callback(body, headers)
        self.assertEqual(first, second)
        self.assertEqual((first.transaction_id, first.raw_status), (TRANSACTION, "CHARGEBACKED"))
        self.assertFalse(hasattr(first, "money"))
        self.assertIsNone(first.order_hint)
        for bad_headers in ({}, {**headers, "X-Secret": "wrong"},
            CIMultiDict([("X-MerchantId", MERCHANT), ("x-merchantid", MERCHANT), ("X-Secret", SECRET)])):
            with self.subTest(headers=bad_headers):
                with self.assertRaises(PaymentVerificationError):
                    adapter.handle_callback(body, bad_headers)
        for invalid in (body.replace(b'150', b'true'), body.replace(b'150', b'NaN'),
                        body.replace(b'"id":', b'"id":"duplicate","id":'), b'x'*8193):
            with self.subTest(body=invalid[:30]):
                with self.assertRaises(PaymentVerificationError):
                    adapter.handle_callback(invalid, headers)

    async def test_response_bound_and_refund_acceptance_is_not_completion(self):
        with self.assertRaises(PaymentVerificationError):
            await provider(FakeTransport(ProviderHttpResponse(200, {}, b'x'*65537))).get_payment_status(TRANSACTION)
        transport = FakeTransport(response({"supported": True}), response({"transactionId": TRANSACTION,
            "accepted": True, "manualControlRequired": False, "message": "Accepted"}))
        outcome = await provider(transport).refund_payment(TRANSACTION, "request-1")
        self.assertEqual(outcome.state, "accepted")
        self.assertEqual([call[:2] for call in transport.calls],
            [("GET", "/transaction/"+TRANSACTION+"/cancel-supported"), ("POST", "/transaction/"+TRANSACTION+"/cancel")])
        transport = FakeTransport(response({"supported": True}), TimeoutError())
        adapter = provider(transport)
        self.assertEqual((await adapter.refund_payment(TRANSACTION, "refund-unknown")).state, "unknown")
        self.assertEqual((await adapter.refund_payment(TRANSACTION, "refund-unknown")).state, "unknown")
        self.assertEqual(len(transport.calls), 2)

    async def test_malformed_responses_rate_limit_and_explicit_card_mapping(self):
        for body in (b'{"id":"one","id":"two"}', b'{"value":NaN}', b'[]', b'x'*65537):
            with self.subTest(body=body[:30]):
                with self.assertRaises(PaymentVerificationError):
                    await provider(FakeTransport(ProviderHttpResponse(200, {}, body))).get_payment_status(TRANSACTION)
        for retry, expected in (("120", 120), ("Fri, 02 Oct 2026 12:00:00 GMT", 600)):
            with self.subTest(retry=retry):
                adapter = provider(FakeTransport(ProviderHttpResponse(429, {"Retry-After": retry}, b'')))
                with self.assertRaises(ProviderRateLimited) as caught:
                    await adapter.get_payment_status(TRANSACTION, now=1790941800)
                self.assertEqual(caught.exception.retry_after, expected)
        # The public GET docs do not establish a card enum; this mapping is
        # explicitly a fixture contract, never a verified merchant default.
        adapter = provider(FakeTransport(response(status_body(paymentMethod="FIXTURE_CARD"))),
                           status_methods={"FIXTURE_CARD": "bank_card"})
        self.assertEqual((await adapter.get_payment_status(TRANSACTION)).method, "bank_card")
        for support, result, expected in ((False, {}, "unsupported"),
            (True, {"accepted": False, "manualControlRequired": True}, "manual_control_required"),
            (True, {"accepted": False, "manualControlRequired": False}, "declined")):
            with self.subTest(expected=expected):
                transport = FakeTransport(response({"supported": support}),
                    response({"transactionId": TRANSACTION, **result}))
                self.assertEqual((await provider(transport).refund_payment(TRANSACTION, "r1")).state, expected)


if __name__ == "__main__":
    unittest.main()
