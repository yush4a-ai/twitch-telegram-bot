"""Two independent Plus products and owner-only non-monetary staging checkout."""

import os
import tempfile
import time
import unittest
import uuid

import aiohttp
from aiohttp import web

from bot.database import Database
from bot.mini_app_web import install_mini_app_routes
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class MiniAppSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=100)
        app = web.Application()
        install_mini_app_routes(
            app, self.db, BOT_TOKEN,
            billing_test_enabled=True, billing_test_user_ids=frozenset({101, 202}),
        )
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)

    def request(self, path, actor=101, **fields):
        return self.session.post(
            self.base + path,
            json={"init_data": signed_webapp(actor), **fields},
        )

    async def test_voluntary_trial_is_allowlisted_once_and_never_auto_starts(self):
        async with self.request("/app/api/subscription/state", actor=202) as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertTrue(state["viewer"]["test_trial_available"])
            self.assertFalse(state["viewer"]["active"])
            self.assertFalse(state["money_charged"])
        self.assertFalse(await self.db.has_viewer_plus(202))
        async with self.request("/app/api/subscription/test-trial", actor=303) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/subscription/test-trial", actor=202,
                                paid=True) as response:
            self.assertEqual(response.status, 400)
        async with self.request("/app/api/subscription/test-trial", actor=202) as response:
            self.assertEqual(response.status, 200)
            started = await response.json()
            self.assertTrue(started["started_now"])
        async with self.request("/app/api/subscription/test-trial", actor=202) as response:
            self.assertEqual(response.status, 200)
            repeated = await response.json()
            self.assertFalse(repeated["started_now"])
            self.assertEqual(repeated["expires_at"], started["expires_at"])
        async with self.request("/app/api/subscription/state", actor=202) as response:
            state = await response.json()
            self.assertTrue(state["viewer"]["active"])
            self.assertTrue(state["viewer"]["test_trial_used"])
            self.assertFalse(state["viewer"]["test_trial_available"])
            self.assertFalse(state["money_charged"])
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key='trial:v1:202'"
        )
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_revoked_trial_is_not_active_when_other_plus_is_active(self):
        now = time.time()
        async with self.request("/app/api/subscription/test-trial", actor=202) as response:
            self.assertEqual(response.status, 200)
        cursor = await self.db.conn.execute(
            "SELECT grant_id FROM viewer_test_trials WHERE telegram_user_id=202"
        )
        trial_grant_id = (await cursor.fetchone())[0]
        await self.db.revoke_test_viewer_plus(
            trial_grant_id, revoked_at=now + 1, issued_by=425785231,
        )
        await self.db.issue_test_viewer_plus(
            202, "separate-after-trial", starts_at=now - 1,
            expires_at=now + 600, issued_by=425785231, now=now,
        )
        async with self.request("/app/api/subscription/state", actor=202) as response:
            self.assertEqual(response.status, 200)
            viewer = (await response.json())["viewer"]
            self.assertTrue(viewer["active"])
            self.assertTrue(viewer["test_trial_used"])
            self.assertFalse(viewer["test_trial_active"])

    async def test_free_viewer_streamer_both_and_own_history(self):
        async with self.request("/app/api/subscription/state", actor=202) as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertFalse(state["viewer"]["active"])
            self.assertFalse(state["streamer"]["linked"])
            self.assertEqual(state["history"], [])
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "t11-viewer", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        async with self.request("/app/api/subscription/state") as response:
            state = await response.json()
            self.assertTrue(state["viewer"]["active"])
            self.assertFalse(state["streamer"]["active"])
            self.assertEqual(state["viewer"]["source"], "test")
        await self.db.issue_test_streamer_plus(
            "11", "t11-streamer", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        async with self.request("/app/api/subscription/state") as response:
            state = await response.json()
            self.assertTrue(state["viewer"]["active"])
            self.assertTrue(state["streamer"]["active"])
            self.assertEqual(state["streamer"]["twitch_login"], "alpha")
        async with self.request("/app/api/subscription/state", actor=202) as response:
            other = await response.json()
            self.assertFalse(other["viewer"]["active"])
            self.assertFalse(other["streamer"]["active"])

    async def test_test_order_needs_server_capture_and_does_not_trust_paid_callback(self):
        async with self.request(
            "/app/api/subscription/test-checkout", actor=202,
            product="viewer_plus", price=0,
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.request(
            "/app/api/subscription/test-checkout", actor=202,
            product="viewer_plus",
        ) as response:
            self.assertEqual(response.status, 200)
            order_id = (await response.json())["order_id"]
        async with self.request("/app/api/subscription/state", actor=202) as response:
            state = await response.json()
            self.assertFalse(state["viewer"]["active"])
            self.assertEqual(state["history"][0]["status"], "pending")
        async with self.request(
            "/app/api/subscription/callback", actor=202,
            order_id=order_id, paid=True,
        ) as response:
            self.assertEqual(response.status, 404)
        self.assertFalse(await self.db.has_viewer_plus(202))
        async with self.request(
            "/app/api/subscription/test-confirm", actor=202,
            order_id=order_id, paid=True,
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.request(
            "/app/api/subscription/test-confirm", actor=202,
            order_id=order_id,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["status"], "paid")
        async with self.request(
            "/app/api/subscription/test-confirm", actor=202,
            order_id=order_id,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(await self.db.has_viewer_plus(202))
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key=?",
            ("mock-order:" + order_id,),
        )
        self.assertEqual((await cursor.fetchone())[0], 1)
        async with self.request("/app/api/subscription/state", actor=202) as response:
            state = await response.json()
            self.assertEqual(state["viewer"]["source"], "mock")
            self.assertEqual(state["history"][0]["status"], "paid")

    async def test_wrong_user_cancel_refund_and_products_remain_separate(self):
        async with self.request(
            "/app/api/subscription/test-checkout", actor=303,
            product="viewer_plus",
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/subscription/test-checkout", actor=202,
            product="streamer_plus",
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/subscription/test-checkout", product="viewer_plus",
        ) as response:
            viewer_order = (await response.json())["order_id"]
        async with self.request(
            "/app/api/subscription/test-cancel", actor=202,
            order_id=viewer_order,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/subscription/test-confirm", actor=202,
            order_id=viewer_order,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/subscription/test-cancel", order_id=viewer_order,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["status"], "cancelled")
        async with self.request(
            "/app/api/subscription/test-confirm", order_id=viewer_order,
        ) as response:
            self.assertEqual(response.status, 409)
        async with self.request(
            "/app/api/subscription/test-checkout", product="viewer_plus",
        ) as response:
            active_viewer = (await response.json())["order_id"]
        async with self.request(
            "/app/api/subscription/test-checkout", product="streamer_plus",
        ) as response:
            active_streamer = (await response.json())["order_id"]
        for order_id in (active_viewer, active_streamer):
            async with self.request(
                "/app/api/subscription/test-confirm", order_id=order_id,
            ) as response:
                self.assertEqual(response.status, 200)
        async with self.request(
            "/app/api/subscription/test-refund", actor=202,
            order_id=active_viewer,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/subscription/test-refund", order_id=active_viewer,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertIsNone(await self.db.get_current_plus_grant(101, "viewer_plus"))
        self.assertTrue(await self.db.has_viewer_plus(101))
        self.assertTrue(await self.db.has_streamer_plus(101))
        async with self.request("/app/api/subscription/state") as response:
            state = await response.json()
            self.assertEqual(state["history"][0]["status"], "paid")
            self.assertEqual(state["history"][1]["status"], "refunded")
            self.assertTrue(state["viewer"]["active"])
            self.assertEqual({item["product_id"] for item in state["viewer"]["sources"]}, {"streamer_plus"})
        async with self.request("/app/api/subscription/test-refund", order_id=active_streamer) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse(await self.db.has_viewer_plus(101))
        self.assertFalse(await self.db.has_streamer_plus(101))

    async def test_expired_grant_does_not_look_active(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(
            202, "t11-expired", starts_at=now - 300,
            expires_at=now - 30, issued_by=425785231, now=now - 301,
        )
        async with self.request("/app/api/subscription/state", actor=202) as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertFalse(state["viewer"]["active"])
            self.assertIsNone(state["viewer"]["expires_at"])

    async def test_expired_pending_order_is_displayed_without_grant(self):
        now = time.time()
        await self.db.create_billing_order(
            uuid.uuid4().hex, "t11-expired-pending", 202, 600,
            now=now - 1000, plan="viewer_plus",
        )
        async with self.request("/app/api/subscription/state", actor=202) as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertEqual(state["history"][0]["status"], "expired")
            self.assertFalse(state["viewer"]["active"])

    async def test_invalid_init_data_cannot_open_state_order_or_trial(self):
        for endpoint in ("state", "test-checkout", "test-trial"):
            async with self.session.post(
                self.base + "/app/api/subscription/" + endpoint,
                json={"init_data": "invalid", "product": "viewer_plus"} if endpoint != "state" else {"init_data": "invalid"},
            ) as response:
                self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
