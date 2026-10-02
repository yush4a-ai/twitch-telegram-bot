"""Local QA scenarios exercise real storage/routes without external send/payment."""

import os
import unittest

from aiohttp.test_utils import TestClient, TestServer

from scripts import mini_app_browser_fixture as fixture
from tests.test_admin_telegram_auth import signed_webapp


class RedesignFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, scenario):
        builder = getattr(fixture, "build_fixture", None)
        self.assertTrue(callable(builder), "scenario builder is required")
        app, db = await builder(scenario)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return app, db, client

    async def test_fixture_supports_required_scenarios_and_never_calls_external_sender(self):
        cases = (
            ("free-empty", 0, False), ("free-six", 6, False),
            ("plus-two-hundred", 200, True), ("streamer-plus", 6, True),
            ("independent-viewer", 6, True), ("legacy-group", 6, False),
            ("channel-permissions", 6, False), ("payment-off", 6, False),
            ("legal-unready", 6, False),
        )
        for scenario, count, viewer in cases:
            with self.subTest(scenario=scenario):
                app, db, client = await self.build(scenario)
                state = app[fixture.FIXTURE_STATE_KEY]
                self.assertTrue(os.path.isfile(os.path.join(state.directory.name, "fixture.db")))
                self.assertEqual(len(await db.list_channels(501)), count)
                response = await client.post("/app/api/viewer/state", json={
                    "init_data": signed_webapp(501), "user_id": 999,
                })
                self.assertEqual(response.status, 200)
                payload = await response.json()
                self.assertEqual(len(payload["subscriptions"]), count)
                self.assertEqual(payload["viewer_plus_active"], viewer)
                self.assertEqual(state.bot.sent_calls, [])
                self.assertEqual(state.external_payment_calls, [])
                denied = await client.post("/app/api/subscription/test-checkout", json={
                    "init_data": signed_webapp(501), "product": "viewer_plus",
                })
                self.assertEqual(denied.status, 403)
                self.assertEqual((await (await db.conn.execute("SELECT count(*) FROM billing_orders")).fetchone())[0], 0)

    async def test_long_names_statuses_and_existing_group_are_seeded_separately(self):
        app, db, client = await self.build("legacy-group")
        state = app[fixture.FIXTURE_STATE_KEY]
        communities = await db.list_streamer_communities(501)
        self.assertEqual({row[2] for row in communities}, {"channel", "supergroup"})
        self.assertTrue(await db.list_channels(-1002))
        names = await state.twitch.get_display_names(["alpha", "beta"])
        self.assertGreater(len(names["beta"]), 70)
        response = await client.post("/app/api/viewer/state", json={"init_data": signed_webapp(501)})
        rows = (await response.json())["subscriptions"]
        self.assertEqual({r["status"] for r in rows}, {"live", "stale", "offline"})
        self.assertTrue(any(not r["notify_enabled"] for r in rows))

    async def test_unknown_scenario_rejected_without_persistent_database(self):
        builder = getattr(fixture, "build_fixture", None)
        self.assertTrue(callable(builder), "scenario builder is required")
        with self.assertRaises(ValueError):
            await builder("production")

    async def test_reminder_inflight_fixture_uses_delivery_fence_without_sending(self):
        app, _db, client = await self.build("reminder-inflight")
        state = await (await client.post("/app/api/viewer/state", json={
            "init_data": signed_webapp(501),
        })).json()
        reminder = next(row for row in state["subscriptions"] if row["login"] == "alpha")["reminder"]
        self.assertEqual(reminder["status"], "sending")
        for path, values in (("reminder", {"delay_minutes": 30}), ("reminder/cancel", {})):
            response = await client.post(f"/app/api/viewer/{path}", json={
                "init_data": signed_webapp(501), "login": "alpha", **values,
            })
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["error"], "reminder_in_flight")
        self.assertEqual(app[fixture.FIXTURE_STATE_KEY].bot.sent_calls, [])

    async def test_history_fixture_contains_only_own_terminal_delivery_facts(self):
        app, _db, client = await self.build("history")
        first = await (await client.post("/app/api/viewer/history", json={
            "init_data": signed_webapp(501), "limit": 20,
        })).json()
        second = await (await client.post("/app/api/viewer/history", json={
            "init_data": signed_webapp(501), "limit": 20, "before_id": first["next_before_id"],
        })).json()
        events = first["events"] + second["events"]
        self.assertEqual((len(first["events"]), len(second["events"])), (20, 5))
        self.assertIsNone(second["next_before_id"])
        self.assertEqual(len({event["id"] for event in events}), 25)
        self.assertEqual({event["outcome"] for event in events}, {"sent", "suppressed", "unknown"})
        self.assertEqual({event["login"] for event in events}, {"alpha"})
        foreign = await (await client.post("/app/api/viewer/history", json={
            "init_data": signed_webapp(202), "limit": 20,
        })).json()
        self.assertEqual([event["login"] for event in foreign["events"]], ["foreign"])
        self.assertEqual(app[fixture.FIXTURE_STATE_KEY].bot.sent_calls, [])

    async def test_fixture_routes_do_not_exist_in_product_installer(self):
        from aiohttp import web
        from bot.mini_app_web import install_mini_app_routes
        app, db, _client = await self.build("free-empty")
        live = web.Application()
        install_mini_app_routes(live, db, fixture.FIXTURE_BOT_TOKEN)
        self.assertFalse(any(resource.canonical.startswith("/_qa/") for resource in live.router.resources()))
