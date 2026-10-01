import hashlib
import hmac
import unittest

from bot.billing_provider import MockPaymentProvider, PaymentVerificationError, VerifiedPaymentEvent


SECRET = b"local-test-secret-with-at-least-32-bytes"
CAPTURE_BODY = (
    b'{"event_id":"event-1","order_id":"order-1","payment_id":"payment-1",'
    b'"type":"captured","units":1,"currency":"TEST"}'
)


def signed_headers(body: bytes, timestamp: str = "1000") -> dict[str, str]:
    signature = hmac.new(SECRET, timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Mock-Timestamp": timestamp, "X-Mock-Signature": signature}


class MockPaymentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkout_is_opaque_and_never_exposes_a_payment_site(self):
        provider = MockPaymentProvider(SECRET)
        checkout = await provider.create_checkout("order-1", 1, "TEST")
        self.assertEqual(checkout.order_id, "order-1")
        self.assertEqual(checkout.reference, "mock-checkout:order-1")
        self.assertNotIn("https://", checkout.reference)
        self.assertEqual(await provider.request_refund("payment-1", "refund-key"),
                         "mock-refund:payment-1:refund-key")
        self.assertIsNone(await provider.cancel_checkout(checkout.reference))

    async def test_independently_signed_capture_and_refund_are_verified(self):
        provider = MockPaymentProvider(SECRET)
        captured = provider.verify_webhook(CAPTURE_BODY, signed_headers(CAPTURE_BODY), now=1000)
        self.assertEqual(captured, VerifiedPaymentEvent(
            provider="mock", event_id="event-1", order_id="order-1",
            payment_id="payment-1", event_type="captured", units=1, currency="TEST",
        ))
        refunded = VerifiedPaymentEvent(
            provider="mock", event_id="event-2", order_id="order-1",
            payment_id="payment-1", event_type="refunded", units=1, currency="TEST",
        )
        body, headers = provider.sign_test_event(refunded, now=1001)
        self.assertEqual(headers["X-Mock-Signature"], signed_headers(body, "1001")["X-Mock-Signature"])
        self.assertEqual(provider.verify_webhook(body, headers, now=1001), refunded)

    async def test_signature_age_and_schema_fail_closed(self):
        provider = MockPaymentProvider(SECRET)
        bad_cases = (
            (CAPTURE_BODY + b" ", signed_headers(CAPTURE_BODY), 1000),
            (CAPTURE_BODY, {"X-Mock-Timestamp": "1000"}, 1000),
            (CAPTURE_BODY, signed_headers(CAPTURE_BODY, "1000"), 1301),
            (CAPTURE_BODY, signed_headers(CAPTURE_BODY, "1000"), 699),
            (CAPTURE_BODY, {**signed_headers(CAPTURE_BODY), "x-mock-signature": "0" * 64}, 1000),
        )
        for body, headers, now in bad_cases:
            with self.subTest(body=body[-4:], now=now), self.assertRaises(PaymentVerificationError):
                provider.verify_webhook(body, headers, now=now)
        for body in (
            CAPTURE_BODY[:-1] + b',"unexpected":true}',
            CAPTURE_BODY.replace(b'"units":1', b'"units":true'),
            CAPTURE_BODY.replace(b'"event_id":"event-1"', b'"event_id":"event-1","event_id":"event-2"'),
            CAPTURE_BODY.replace(b'"currency":"TEST"', b'"currency":"USD"'),
            CAPTURE_BODY.replace(b'"type":"captured"', b'"type":[]'),
            b"x" * 4097,
        ):
            with self.subTest(body=body[-20:]), self.assertRaises(PaymentVerificationError):
                provider.verify_webhook(body, signed_headers(body), now=1000)


if __name__ == "__main__":
    unittest.main()
