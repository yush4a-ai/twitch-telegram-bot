"""Реальный сетевой транспорт Platega: фиксированный хост, границы и отказы.

Транспорт — единственное место, которое ходит во внешнюю сеть за деньги.
Здесь фиксируется: адрес не подменяется, заголовки провайдера уходят как есть,
тело ограничено, редиректы запрещены, а политика по умолчанию не отправляет
ничего даже при наличии ключей.
"""

import json
import unittest

from bot.billing_provider import PaymentCreationUnknown, PaymentVerificationError, ProviderHttpResponse
from bot.platega_provider import PlategaProvider
from bot.platega_transport import PLATEGA_API_BASE, PlategaHttpTransport
from bot.plan_catalog import BillingRuntimePolicy
from tests.test_platega_provider import (
    ATTEMPT, MERCHANT, SECRET, TRANSACTION, response, snapshot,
)

# Владелец явно разрешил банковский канал: без этого флага транспорт не должен
# отправлять ни одного запроса.
EXTERNAL_POLICY = BillingRuntimePolicy("sandbox", True, True, False, True, True)


class FakeContent:
    def __init__(self, payload: bytes):
        self._payload = payload

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._payload), size):
            yield self._payload[start:start + size]
        if not self._payload:
            yield b""


class FakeResponse:
    def __init__(self, status: int, payload: bytes, headers: dict | None = None):
        self.status = status
        self.headers = dict(headers or {})
        self.content = FakeContent(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Минимальная замена aiohttp.ClientSession: только запись вызовов."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)

    async def close(self):
        self.closed = True


def adapter(transport, policy=EXTERNAL_POLICY, **kwargs):
    return PlategaProvider(
        transport, MERCHANT, SECRET,
        hosted_hosts=frozenset({"pay.platega.io"}),
        runtime_policy=policy,
        return_url="https://worker-production-cee5.up.railway.app/app",
        failed_url="https://worker-production-cee5.up.railway.app/app",
        **kwargs,
    )


def created_body():
    return {
        "transactionId": TRANSACTION,
        "redirect": "https://pay.platega.io/checkout?id=" + TRANSACTION,
        "status": "PENDING",
        "expiresIn": "00:15:00",
    }


class PlategaTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_transport_targets_fixed_host_with_server_headers(self):
        session = FakeSession(FakeResponse(200, json.dumps(created_body()).encode()))
        transport = PlategaHttpTransport(session=session)
        checkout = await adapter(transport, buyer_names={101: "@verified_buyer"}).create_payment(
            snapshot(), ATTEMPT)

        self.assertEqual(checkout.reference, TRANSACTION)
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], PLATEGA_API_BASE + "/transaction/process")
        self.assertIs(call["allow_redirects"], False)
        self.assertEqual(call["headers"]["X-MerchantId"], MERCHANT)
        self.assertEqual(call["headers"]["X-Secret"], SECRET)
        self.assertNotIn(SECRET, call["url"])
        self.assertIs(transport.network_free, False)
        self.assertIs(transport.trusted_contract, True)
        self.assertNotIn(SECRET, str(call.get("headers").get("Referer", "")))

    async def test_transport_refuses_foreign_hosts_paths_and_methods(self):
        for bad_base in ("http://app.platega.io", "https://app.platega.io.evil.test",
                         "https://evil.test", "https://app.platega.io:444",
                         "https://user:pass@app.platega.io", PLATEGA_API_BASE + "/v2"):
            with self.subTest(base=bad_base):
                with self.assertRaises(ValueError):
                    PlategaHttpTransport(base_url=bad_base, session=FakeSession())

        session = FakeSession()
        transport = PlategaHttpTransport(session=session)
        for bad_path in ("transaction/process", "//evil.test/transaction",
                         "/../admin", "/transaction/process?x=1",
                         "/transaction/process#frag", "/transaction/process\n"):
            with self.subTest(path=bad_path):
                with self.assertRaises(ValueError):
                    await transport.request("POST", bad_path, json={}, headers={}, timeout=5)
        with self.assertRaises(ValueError):
            await transport.request("DELETE", "/transaction/x", json=None, headers={}, timeout=5)
        self.assertEqual(session.calls, [])

    async def test_oversized_or_redirected_responses_never_reach_the_money_path(self):
        oversized = FakeSession(FakeResponse(200, b"x" * 70000))
        with self.assertRaises(PaymentVerificationError):
            await adapter(PlategaHttpTransport(session=oversized)).get_payment_status(TRANSACTION)

        redirected = FakeSession(FakeResponse(302, b"", {"Location": "https://evil.test/"}))
        with self.assertRaises(PaymentVerificationError):
            await adapter(PlategaHttpTransport(session=redirected)).get_payment_status(TRANSACTION)

    async def test_default_policy_never_sends_even_with_real_transport(self):
        session = FakeSession()
        transport = PlategaHttpTransport(session=session)
        closed = adapter(transport, policy=BillingRuntimePolicy(),
                         buyer_names={101: "@verified_buyer"})
        with self.assertRaises(PermissionError):
            await closed.create_payment(snapshot(), ATTEMPT)
        self.assertEqual(session.calls, [])
        self.assertIs(closed.money_capable, False)

    async def test_money_capable_requires_a_declared_contract(self):
        class UndeclaredTransport:
            async def request(self, method, path, *, json=None, headers, timeout):
                raise AssertionError("сеть не должна вызываться")

        declared = adapter(PlategaHttpTransport(session=FakeSession()))
        self.assertIs(declared.money_capable, True)
        with self.assertRaises(PermissionError):
            await adapter(UndeclaredTransport()).create_payment(snapshot(), ATTEMPT)

    async def test_buyer_identity_is_required_and_remembered(self):
        session = FakeSession(FakeResponse(200, json.dumps(created_body()).encode()))
        live = adapter(PlategaHttpTransport(session=session))
        self.assertIs(live.requires_buyer_name, True)
        self.assertIs(live.has_buyer_name(101), False)
        with self.assertRaises(PaymentVerificationError):
            await live.create_payment(snapshot(), ATTEMPT)
        self.assertEqual(session.calls, [])

        live.remember_buyer(101, "@verified_buyer")
        self.assertIs(live.has_buyer_name(101), True)
        await live.create_payment(snapshot(), ATTEMPT)
        payload = session.calls[0]["json"]
        self.assertEqual(payload["metadata"], {"userId": "101", "userName": "@verified_buyer"})

    def test_buyer_identity_is_bounded_and_never_accepts_junk(self):
        live = adapter(PlategaHttpTransport(session=FakeSession()))
        for bad in (None, "", " ", "x" * 257, "bad\nname", 101, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    live.remember_buyer(101, bad)
        for bad_user in (0, -1, True, "101", None):
            with self.subTest(user=bad_user):
                with self.assertRaises(ValueError):
                    live.remember_buyer(bad_user, "@buyer")

    async def test_missing_provider_expiry_falls_back_to_the_order_deadline(self):
        # Реальный ответ Platega приходит с пустым expiresIn: это не ошибка,
        # иначе рабочий платёж превращался бы в «неизвестный исход».
        for expires in ("", None):
            with self.subTest(expires=expires):
                body = created_body()
                if expires is None:
                    body.pop("expiresIn")
                else:
                    body["expiresIn"] = expires
                session = FakeSession(FakeResponse(200, json.dumps(body).encode()))
                checkout = await adapter(
                    PlategaHttpTransport(session=session),
                    buyer_names={101: "@verified_buyer"}).create_payment(snapshot(), ATTEMPT)
                self.assertEqual(checkout.checkout_expires_at, 1000)
                self.assertEqual(checkout.status, "pending")
                self.assertTrue(checkout.hosted_url.startswith("https://pay.platega.io/"))

        broken = created_body()
        broken["expiresIn"] = "soon"
        session = FakeSession(FakeResponse(200, json.dumps(broken).encode()))
        with self.assertRaises(PaymentCreationUnknown):
            await adapter(PlategaHttpTransport(session=session),
                          buyer_names={101: "@verified_buyer"}).create_payment(snapshot(), ATTEMPT)

    async def test_credentials_probe_reads_balances_without_creating_payments(self):
        # Реальный ответ провайдера — список балансов, а не объект: рабочие ключи
        # не должны выглядеть отклонёнными только из-за формы ответа.
        listed = FakeSession(FakeResponse(200, json.dumps(
            [{"amount": 0, "currency": "RUB"}, {"amount": 0, "currency": "USD"}]).encode()))
        self.assertIs(
            await adapter(PlategaHttpTransport(session=listed)).probe_credentials(), True)
        self.assertEqual(listed.calls[0]["url"], PLATEGA_API_BASE + "/balance/all")

        session = FakeSession(FakeResponse(200, json.dumps({"balances": []}).encode()))
        live = adapter(PlategaHttpTransport(session=session))
        self.assertIs(await live.probe_credentials(), True)
        call = session.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], PLATEGA_API_BASE + "/balance/all")
        self.assertNotIn(SECRET, call["url"])

        rejected = FakeSession(FakeResponse(401, b""))
        self.assertIs(
            await adapter(PlategaHttpTransport(session=rejected)).probe_credentials(), False)
        broken = FakeSession(FakeResponse(200, b"not json"))
        self.assertIs(
            await adapter(PlategaHttpTransport(session=broken)).probe_credentials(), False)
        offline = adapter(PlategaHttpTransport(session=FakeSession()),
                          policy=BillingRuntimePolicy())
        self.assertIs(await offline.probe_credentials(), False)

    async def test_transport_closes_only_the_session_it_owns(self):
        session = FakeSession()
        shared = PlategaHttpTransport(session=session)
        await shared.close()
        self.assertFalse(session.closed)

        owned = PlategaHttpTransport()
        self.assertIsNotNone(owned._session)
        await owned.close()


if __name__ == "__main__":
    unittest.main()
