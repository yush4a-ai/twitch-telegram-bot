import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp

from bot.admin_auth import AdminAccess
from bot.billing_provider import RefundOutcome
from bot.database import AccessConflict, AccessDenied
from bot.oauth import OAuthCallbackServer


KEY = "staging-test-key-with-at-least-32-chars-123"


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False),
        )

        async def snapshot():
            return {"environment": "local"}

        self.people = SimpleNamespace(
            search_people=AsyncMock(return_value=[{
                "user_id": 111, "username": "alex_live", "display_name": "Alex Ivanov",
                "last_active_at": 1000.0, "plan": "viewer_plus", "expires_at": 2000.0,
            }]),
            person_card=AsyncMock(return_value={"user_id": 111, "grants": [], "limits": {}}),
            person_history=AsyncMock(return_value=[{"action": "grant", "reason": "testing"}]),
        )
        self.directory = SimpleNamespace(
            access_overview=AsyncMock(return_value={"active_total": 1}),
            active_grants=AsyncMock(return_value=[{"grant_id": "g1"}]),
            history=AsyncMock(return_value=[{"grant_id": "g1", "action": "grant"}]),
        )

        self.server.set_admin_snapshot_provider(snapshot)
        self.server.set_admin_services(people=self.people, directory=self.directory)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def test_read_routes_require_session(self):
        for path in ("/admin/api/users", "/admin/api/users/111",
                     "/admin/api/users/111/history", "/admin/api/access?state=active",
                     "/admin/api/access?state=history"):
            with self.subTest(path=path):
                async with self.session.get(self.base + path) as response:
                    self.assertEqual(response.status, 401)
                    self.assertEqual(await response.json(), {"error": "unauthorized"})

    async def test_users_search_returns_people_and_normalizes_filter(self):
        await self.login()
        async with self.session.get(
            self.base + "/admin/api/users?q=alex&filter=nonsense&page=2"
        ) as response:
            self.assertEqual(response.status, 200)
            payload = await response.json()

        self.assertEqual(payload["people"][0]["user_id"], 111)
        self.assertEqual(payload["page"], 2)
        args = self.people.search_people.await_args
        self.assertEqual(args.args[0], "alex")
        self.assertEqual(args.kwargs["filter_kind"], "all")
        self.assertEqual(args.kwargs["offset"], 20)

    async def test_person_card_and_history_are_returned(self):
        await self.login()
        async with self.session.get(self.base + "/admin/api/users/111") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["user_id"], 111)
        async with self.session.get(self.base + "/admin/api/users/111/history") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["events"][0]["action"], "grant")

    async def test_unknown_person_returns_404(self):
        self.people.person_card = AsyncMock(return_value=None)
        await self.login()
        async with self.session.get(self.base + "/admin/api/users/999") as response:
            self.assertEqual(response.status, 404)

    async def test_access_state_selects_active_or_history(self):
        await self.login()
        async with self.session.get(self.base + "/admin/api/access?state=active") as response:
            payload = await response.json()
        self.assertIn("grants", payload)
        self.assertIn("overview", payload)
        async with self.session.get(self.base + "/admin/api/access?state=history") as response:
            payload = await response.json()
        self.assertEqual(payload["events"][0]["action"], "grant")
        async with self.session.get(self.base + "/admin/api/access?state=nonsense") as response:
            self.assertEqual(response.status, 400)

    async def test_missing_services_answer_503_without_leaking(self):
        server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False),
        )

        async def snapshot():
            return {"environment": "local"}

        server.set_admin_snapshot_provider(snapshot)
        await server.start()
        try:
            session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
            try:
                port = server._runner.addresses[0][1]
                base = f"http://127.0.0.1:{port}"
                async with session.post(base + "/admin/emergency/login",
                                        data={"access_key": KEY}, allow_redirects=False):
                    pass
                async with session.get(base + "/admin/api/users") as response:
                    self.assertEqual(response.status, 503)
            finally:
                await session.close()
        finally:
            await server.stop()


    async def test_services_attached_after_start_are_visible(self):
        server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False),
        )

        async def snapshot():
            return {"environment": "local"}

        server.set_admin_snapshot_provider(snapshot)
        await server.start()
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        try:
            port = server._runner.addresses[0][1]
            base = f"http://127.0.0.1:{port}"
            async with session.post(base + "/admin/emergency/login",
                                    data={"access_key": KEY}, allow_redirects=False):
                pass
            people = SimpleNamespace(
                search_people=AsyncMock(return_value=[]),
                person_card=AsyncMock(return_value=None),
                person_history=AsyncMock(return_value=[]),
            )
            # main() присоединяет сервисы уже после старта сервера.
            server.set_admin_services(people=people, directory=None)
            async with session.get(base + "/admin/api/users") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {"people": [], "page": 1, "limit": 20})
        finally:
            await session.close()
            await server.stop()


class AdminWriteApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=425785231),
        )

        async def snapshot():
            return {"environment": "local"}

        self.people = SimpleNamespace(
            search_people=AsyncMock(return_value=[]),
            person_card=AsyncMock(return_value=None),
            person_history=AsyncMock(return_value=[]),
            grant_manual_access=AsyncMock(return_value={
                "grant_id": "new", "plan": "viewer_plus", "source": "manual",
                "action": "grant", "request_key": "k1",
            }),
            extend_manual_access=AsyncMock(return_value={"grant_id": "next", "action": "extend"}),
            revoke_manual_access=AsyncMock(return_value={"grant_id": "g1", "action": "revoke"}),
        )
        self.directory = SimpleNamespace(
            access_overview=AsyncMock(return_value={}),
            active_grants=AsyncMock(return_value=[]),
            history=AsyncMock(return_value=[]),
        )
        self.server.set_admin_snapshot_provider(snapshot)
        self.server.set_admin_services(people=self.people, directory=self.directory)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def csrf(self):
        async with self.session.get(self.base + "/admin/api/snapshot") as response:
            return (await response.json())["csrf"]

    def payload(self, **overrides):
        body = {
            "request_key": "k1", "target_user_id": 111, "plan": "viewer_plus",
            "expires_at": 2_000_000_000.0, "reason": "testing",
        }
        body.update(overrides)
        return body

    async def test_write_requires_session_csrf_and_origin(self):
        await self.login()
        headers = {"X-Admin-CSRF": await self.csrf(), "Origin": self.base}
        async with self.session.post(self.base + "/admin/api/access/grant",
                                     json=self.payload(), headers=headers) as response:
            self.assertEqual(response.status, 201)
        self.people.grant_manual_access.assert_awaited_once()

        fresh = await self.csrf()
        async with self.session.post(self.base + "/admin/api/access/grant", json=self.payload(),
                                     headers={"X-Admin-CSRF": fresh, "Origin": "https://evil.test"}) as response:
            self.assertEqual(response.status, 403)
        async with self.session.post(self.base + "/admin/api/access/grant", json=self.payload(),
                                     headers={"Origin": self.base}) as response:
            self.assertEqual(response.status, 403)

    async def test_csrf_mark_survives_validation_error(self):
        await self.login()
        token = await self.csrf()
        headers = {"X-Admin-CSRF": token, "Origin": self.base}
        async with self.session.post(self.base + "/admin/api/access/grant",
                                     json={"unexpected": 1}, headers=headers) as response:
            self.assertEqual(response.status, 400)
        # Метка не должна сгорать на отказе валидации, иначе владелец застревает.
        async with self.session.post(self.base + "/admin/api/access/grant",
                                     json=self.payload(request_key="retry-1"),
                                     headers=headers) as response:
            self.assertEqual(response.status, 201)

    async def test_csrf_token_is_single_use(self):
        await self.login()
        token = await self.csrf()
        headers = {"X-Admin-CSRF": token, "Origin": self.base}
        async with self.session.post(self.base + "/admin/api/access/grant",
                                     json=self.payload(), headers=headers) as response:
            self.assertEqual(response.status, 201)
        async with self.session.post(self.base + "/admin/api/access/grant",
                                     json=self.payload(request_key="k2"), headers=headers) as response:
            self.assertEqual(response.status, 403)

    async def test_conflict_returns_409_without_leaking(self):
        self.people.extend_manual_access = AsyncMock(side_effect=AccessConflict("secret /data/bot.db"))
        await self.login()
        headers = {"X-Admin-CSRF": await self.csrf(), "Origin": self.base}
        body = {"request_key": "e1", "grant_id": "g1", "expected_expires_at": 1.0,
                "expires_at": 2.0, "reason": "testing"}
        async with self.session.post(self.base + "/admin/api/access/extend", json=body,
                                     headers=headers) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual(await response.json(), {"error": "conflict"})

    async def test_paid_grant_revoke_is_denied(self):
        self.people.revoke_manual_access = AsyncMock(side_effect=AccessDenied("paid"))
        await self.login()
        headers = {"X-Admin-CSRF": await self.csrf(), "Origin": self.base}
        body = {"request_key": "r1", "grant_id": "paid1", "expected_expires_at": 1.0,
                "reason": "testing"}
        async with self.session.post(self.base + "/admin/api/access/revoke", json=body,
                                     headers=headers) as response:
            self.assertEqual(response.status, 403)
            self.assertEqual(await response.json(), {"error": "denied"})

    async def test_invalid_body_is_rejected(self):
        await self.login()
        async with self.session.post(
            self.base + "/admin/api/access/grant", data="not json",
            headers={"X-Admin-CSRF": await self.csrf(), "Origin": self.base},
        ) as response:
            self.assertEqual(response.status, 415)
        async with self.session.post(
            self.base + "/admin/api/access/grant", json={"unexpected": 1},
            headers={"X-Admin-CSRF": await self.csrf(), "Origin": self.base},
        ) as response:
            self.assertEqual(response.status, 400)


class _FakeBillingDatabase:
    """Минимальная база для панели: она только читает заказ по grant_id."""

    def __init__(self, row):
        self._row = row
        self.queries: list[tuple[str, tuple]] = []
        database = self

        class Cursor:
            async def fetchone(self_inner):
                return database._row

        class Conn:
            async def execute(self_inner, sql, params=()):
                database.queries.append((sql, params))
                return Cursor()

        self.conn = Conn()


class _RefundService:
    """Заглушка денежного сервиса: записывает вызов и возвращает исход."""

    provider_id = "telegram_stars"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[tuple] = []

    async def request_payment_refund(self, actor_id, order_id, request_key, *, now):
        self.calls.append((actor_id, order_id, request_key, now))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class AdminRefundApiTests(unittest.IsolatedAsyncioTestCase):
    """Возврат оплаты: единственный способ снять оплаченное право."""

    async def asyncSetUp(self):
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=425785231),
        )

        async def snapshot():
            return {"environment": "local"}

        self.people = SimpleNamespace(
            search_people=AsyncMock(return_value=[]),
            person_card=AsyncMock(return_value=None),
            person_history=AsyncMock(return_value=[]),
        )
        self.refund = _RefundService(RefundOutcome("accepted", "tx-1"))
        self.database = _FakeBillingDatabase(("telegram_stars", "paid", "confirmed"))
        self.server.set_admin_snapshot_provider(snapshot)
        self.services = {"telegram_stars": self.refund}
        self.server.set_admin_services(
            people=self.people, directory=None, database=self.database, billing=self.services)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def csrf(self):
        async with self.session.get(self.base + "/admin/api/snapshot") as response:
            return (await response.json())["csrf"]

    async def refund_request(self, **overrides):
        body = {"request_key": "refund-1", "order_id": "a" * 32}
        body.update(overrides)
        headers = {"X-Admin-CSRF": await self.csrf(), "Origin": self.base}
        return await self.session.post(
            self.base + "/admin/api/access/refund", json=body, headers=headers)

    async def test_refund_calls_the_service_that_took_the_payment(self):
        await self.login()
        async with await self.refund_request() as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(),
                             {"state": "accepted", "order_id": "a" * 32})

        self.assertEqual(len(self.refund.calls), 1)
        actor, order_id, request_key, _now = self.refund.calls[0]
        self.assertEqual((actor, order_id, request_key), (425785231, "a" * 32, "refund-1"))
        self.assertIsInstance(_now, float)
        self.assertEqual(
            self.database.queries[0][1], ("a" * 32,),
            "заказ ищется по идентификатору из запроса, а не по данным клиента",
        )

    async def test_refund_requires_session_csrf_and_exact_fields(self):
        await self.login()
        headers = {"Origin": self.base}
        async with self.session.post(
            self.base + "/admin/api/access/refund",
            json={"request_key": "refund-1", "order_id": "a" * 32}, headers=headers,
        ) as response:
            self.assertEqual(response.status, 403)

        async with self.session.post(
            self.base + "/admin/api/access/refund",
            json={"request_key": "refund-1", "order_id": "bad"},
            headers={"X-Admin-CSRF": await self.csrf(), "Origin": self.base},
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.session.post(
            self.base + "/admin/api/access/refund",
            json={"request_key": "refund-1", "order_id": "a" * 32, "amount": 150},
            headers={"X-Admin-CSRF": await self.csrf(), "Origin": self.base},
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.refund.calls, [])

    async def test_refund_refuses_when_there_is_nothing_to_refund(self):
        await self.login()
        for row in (("telegram_stars", "paid", "refunded"),
                    ("telegram_stars", "pending", "pending"),
                    ("telegram_stars", "cancelled", "canceled")):
            self.database._row = row
            async with await self.refund_request() as response:
                self.assertEqual(response.status, 409)
                self.assertEqual(await response.json(), {"error": "nothing_to_refund"})
        self.database._row = None
        async with await self.refund_request() as response:
            self.assertEqual(response.status, 404)
            self.assertEqual(await response.json(), {"error": "order_not_found"})
        self.assertEqual(self.refund.calls, [])

    async def test_refund_reports_unknown_outcome_without_lying(self):
        await self.login()
        self.refund.outcome = RuntimeError("provider timeout")
        async with await self.refund_request() as response:
            self.assertEqual(response.status, 503)
            self.assertEqual(await response.json(), {"error": "refund_unknown"})

        self.refund.outcome = RefundOutcome("accepted", "tx-1")
        self.database._row = ("platega", "paid", "confirmed")
        async with await self.refund_request() as response:
            self.assertEqual(response.status, 503)
            self.assertEqual(await response.json(), {"error": "unavailable"})
        self.assertEqual(len(self.refund.calls), 1)

    async def test_refund_is_unavailable_without_billing_services(self):
        server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=425785231),
        )

        async def snapshot():
            return {"environment": "local"}

        server.set_admin_snapshot_provider(snapshot)
        server.set_admin_services(people=self.people, directory=None, database=self.database)
        await server.start()
        try:
            base = f"http://127.0.0.1:{server._runner.addresses[0][1]}"
            async with self.session.post(
                base + "/admin/emergency/login", data={"access_key": KEY}, allow_redirects=False,
            ):
                pass
            async with self.session.get(base + "/admin/api/snapshot") as response:
                token = (await response.json())["csrf"]
            async with self.session.post(
                base + "/admin/api/access/refund",
                json={"request_key": "refund-1", "order_id": "a" * 32},
                headers={"X-Admin-CSRF": token, "Origin": base},
            ) as response:
                self.assertEqual(response.status, 503)
                self.assertEqual(await response.json(), {"error": "unavailable"})
        finally:
            await server.stop()
        self.assertEqual(self.refund.calls, [])


if __name__ == "__main__":
    unittest.main()
