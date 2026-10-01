"""Viewer Plus rules remain grant-gated while quiet hours stay Free."""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, OWNER_ID, signed_webapp


class MiniAppViewerPlusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(202, "beta")
        self.twitch = SimpleNamespace(
            search_categories=AsyncMock(return_value=[("100", "Minecraft"), ("200", "Just Chatting")]),
            get_categories=AsyncMock(return_value={"100": "Minecraft", "200": "Just Chatting"}),
        )
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
            viewer_db=self.db, viewer_bot_token=BOT_TOKEN,
            mini_app_twitch=self.twitch,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    def request(self, path, actor_id=101, **fields):
        return self.session.post(
            self.base + path,
            json={"init_data": signed_webapp(actor_id), **fields},
        )

    async def test_selected_live_video_reports_capacity_photo_fallback(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "video-status", starts_at=now - 5, expires_at=now + 600,
            issued_by=OWNER_ID, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_state(
            101, "alpha", True, "live-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=now,
        )
        self.server.set_preview_observer(SimpleNamespace(
            photo_delivery_status=lambda login: "limited" if login == "alpha" else "unknown",
        ))
        async with self.request("/app/api/viewer/state") as response:
            self.assertEqual(response.status, 200)
            row = (await response.json())["subscriptions"][0]
            self.assertEqual(row["video_delivery_status"], "limited")

    async def test_video_status_uses_confirmed_media_kind_after_delivery_and_revoke(self):
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "video-delivered", starts_at=now - 5, expires_at=now + 600,
            issued_by=OWNER_ID, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_state(
            101, "alpha", True, "live-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=now,
        )
        await self.db.set_live_message_kind_if_current(
            101, "alpha", "live-1", 700, "animation", False,
        )
        self.server.set_preview_observer(SimpleNamespace(photo_delivery_status=lambda login: "preparing"))
        async with self.request("/app/api/viewer/state") as response:
            self.assertEqual((await response.json())["subscriptions"][0]["video_delivery_status"], "video")
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=OWNER_ID)
        async with self.request("/app/api/viewer/state") as response:
            self.assertEqual((await response.json())["subscriptions"][0]["video_delivery_status"], "returning_photo")

    async def test_reminder_route_resolves_current_stream_on_server_and_cancels(self):
        now = time.time()
        await self.db.set_live_state(
            101, "alpha", True, "stream-1", 700, "Title",
            broadcaster_id="1001", last_seen_live_at=now,
        )
        async with self.request(
            "/app/api/viewer/reminder", login="alpha", delay_minutes=15,
        ) as response:
            self.assertEqual(response.status, 403)
        await self.db.issue_test_viewer_plus(
            101, "reminder-route", starts_at=now - 5, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        async with self.request(
            "/app/api/viewer/reminder", login="alpha", delay_minutes=15,
            broadcaster_id="forged",
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.request(
            "/app/api/viewer/reminder", login="alpha", delay_minutes=15,
        ) as response:
            self.assertEqual(response.status, 200)
            saved = await response.json()
            self.assertEqual(saved["reminder"]["delay_minutes"], 15)
            self.assertEqual(saved["reminder"]["status"], "scheduled")
        async with self.request("/app/api/viewer/state") as response:
            row = (await response.json())["subscriptions"][0]
            self.assertEqual(row["reminder"]["version"], saved["reminder"]["version"])
        async with self.request("/app/api/viewer/reminder/cancel", actor_id=202,
                                login="alpha") as response:
            self.assertEqual(response.status, 404)
        async with self.request("/app/api/viewer/state") as response:
            self.assertEqual((await response.json())["subscriptions"][0]
                             ["reminder"]["status"], "scheduled")
        async with self.request("/app/api/viewer/reminder/cancel", login="alpha") as response:
            self.assertEqual(response.status, 200)
        async with self.request("/app/api/viewer/state") as response:
            row = (await response.json())["subscriptions"][0]
            self.assertEqual(row["reminder"]["status"], "cancelled")

    async def test_folder_routes_keep_ownership_and_saved_state_after_expiry(self):
        now = time.time()
        async with self.request("/app/api/viewer/folder/create", name="Игры") as response:
            self.assertEqual(response.status, 403)
        grant_id = await self.db.issue_test_viewer_plus(
            101, "folder-route", starts_at=now - 1, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        async with self.request("/app/api/viewer/folder/create", name="Игры") as response:
            self.assertEqual(response.status, 200)
            folder = (await response.json())["folder"]
        async with self.request("/app/api/viewer/folder/create", name="Разговоры") as response:
            self.assertEqual(response.status, 200)
            other = (await response.json())["folder"]
        async with self.request("/app/api/viewer/folder/rename", folder_id=other["id"],
                                name="Игры", expected_version=other["version"]) as response:
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["error"], "folder_name_taken")
        async with self.request("/app/api/viewer/folder/move", actor_id=202,
                                login="beta", folder_id=folder["id"],
                                expected_folder_id=None) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/viewer/folder/move", login="alpha",
                                folder_id=folder["id"],
                                expected_folder_id=None) as response:
            self.assertEqual(response.status, 200)
        async with self.request("/app/api/viewer/folder/rule", folder_id=folder["id"],
                                expected_version=folder["version"], games=["Minecraft"],
                                title_keywords=[], exclude_keywords=[]) as response:
            self.assertEqual(response.status, 200)
            folder = (await response.json())["folder"]
        async with self.request("/app/api/viewer/state") as response:
            payload = await response.json()
            self.assertEqual(payload["subscriptions"][0]["folder_id"], folder["id"])
            self.assertEqual(payload["folders"][0]["games"], ["Minecraft"])
        async with self.request(
            "/app/api/viewer/filter", login="alpha", expected_version=0,
            games=[], title_keywords=[], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 200)
        async with self.request("/app/api/viewer/filter/reset", login="alpha",
                                expected_version=1) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual((await self.db.get_effective_viewer_filter(101, "alpha"))
                         .games, ("Minecraft",))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(),
                                              issued_by=OWNER_ID)
        async with self.request("/app/api/viewer/state") as response:
            payload = await response.json()
            self.assertFalse(payload["viewer_plus_active"])
            self.assertEqual(payload["folders"][0]["name"], "Игры")
            self.assertEqual(payload["subscriptions"][0]["folder_id"], folder["id"])

    async def test_history_route_reads_only_own_confirmed_events(self):
        from bot.viewer_history import ViewerHistoryService

        now = time.time()
        async with self.request("/app/api/viewer/history", limit=20) as response:
            self.assertEqual(response.status, 403)
        grant_id = await self.db.issue_test_viewer_plus(
            101, "history-route", starts_at=now - 1, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        await self.db.issue_test_viewer_plus(
            202, "history-route-other", starts_at=now - 1,
            expires_at=now + 3600, issued_by=OWNER_ID, now=now,
        )
        await self.db.set_live_state(101, "alpha", True, "stream-1", 900,
                                     "Title", broadcaster_id="1001")
        self.assertTrue(await ViewerHistoryService(self.db).record_direct_live(
            101, "alpha", "stream-1", 900, now=now,
        ))
        async with self.request("/app/api/viewer/history", limit=20) as response:
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual([(event["login"], event["outcome"])
                              for event in payload["events"]], [("alpha", "sent")])
        async with self.request("/app/api/viewer/history", actor_id=202,
                                limit=20) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["events"], [])
        async with self.request("/app/api/viewer/history", limit=20,
                                telegram_user_id=202) as response:
            self.assertEqual(response.status, 400)
        async with self.request("/app/api/viewer/history", limit=20,
                                before_id="1") as response:
            self.assertEqual(response.status, 400)
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(),
                                              issued_by=OWNER_ID)
        async with self.request("/app/api/viewer/history", limit=20) as response:
            self.assertEqual(response.status, 403)

    async def test_filter_save_requires_current_viewer_plus_and_owned_subscription(self):
        rule = dict(login="alpha", expected_version=0, games=["Minecraft"],
                    title_keywords=["speedrun"], exclude_keywords=[])
        async with self.request("/app/api/viewer/filter", **rule) as response:
            self.assertEqual(response.status, 403)
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "t6-filter", starts_at=now - 10, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        async with self.request("/app/api/viewer/filter", **rule) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["version"], 1)
        async with self.request("/app/api/viewer/filter", **rule) as response:
            self.assertEqual(response.status, 409)
        async with self.request("/app/api/viewer/filter", 202, **rule) as response:
            self.assertEqual(response.status, 404)
        async with self.request("/app/api/viewer/state") as response:
            row = (await response.json())["subscriptions"][0]
            self.assertEqual(row["filter"]["games"], ["Minecraft"])
        self.assertIsNone(await self.db.get_effective_viewer_filter(101, "alpha", now=now + 3601))
        self.assertIsNotNone(await self.db.get_viewer_filter(101, "alpha"))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=now + 1, issued_by=OWNER_ID)
        async with self.request("/app/api/viewer/filter", **{**rule, "expected_version": 1}) as response:
            self.assertEqual(response.status, 403)

    async def test_quiet_hours_and_digest_work_for_free_on_new_and_legacy_routes(self):
        async with self.request("/app/api/viewer/digest", enabled=True) as response:
            self.assertEqual(response.status, 409)
        async with self.request(
            "/app/api/viewer/quiet-hours", start_minute=1320,
            end_minute=360, utc_offset_minutes=180,
        ) as response:
            self.assertEqual(response.status, 200)
        async with self.request("/app/api/viewer/digest", enabled=True) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.get_quiet_hours(101), (1320, 360, 180, True))
        async with self.request("/viewer/api/digest", enabled=False) as response:
            self.assertEqual(response.status, 200)
        async with self.request("/app/api/viewer/state") as response:
            state = await response.json()
            self.assertEqual(state["quiet_hours"], {
                "start_minute": 1320, "end_minute": 360,
                "utc_offset_minutes": 180, "digest_enabled": False,
            })
        async with self.request("/app/api/viewer/quiet-hours", clear=True) as response:
            self.assertEqual(response.status, 200)
        self.assertIsNone(await self.db.get_quiet_hours(101))
        async with self.request("/app/api/viewer/digest", enabled=True) as response:
            self.assertEqual(response.status, 409)

    async def test_invalid_filter_and_quiet_hours_inputs_do_not_write(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(101, "t6-invalid", starts_at=now - 1,
                                             expires_at=now + 3600, issued_by=OWNER_ID, now=now)
        async with self.request(
            "/app/api/viewer/filter", login="alpha", expected_version=0,
            games=["x"], title_keywords=[], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertIsNone(await self.db.get_viewer_filter(101, "alpha"))
        async with self.request(
            "/app/api/viewer/quiet-hours", start_minute=1440,
            end_minute=360, utc_offset_minutes=180,
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertIsNone(await self.db.get_quiet_hours(101))

    async def test_category_alert_is_separate_opt_in_and_server_resolves_names(self):
        now = time.time()
        async with self.request("/app/api/viewer/category-search", query="mine") as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/viewer/category-alert", login="alpha", enabled=True,
            category_ids=["100"], expected_version=0,
        ) as response:
            self.assertEqual(response.status, 403)
        grant = await self.db.issue_test_viewer_plus(
            101, "t8-category", starts_at=now - 1, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        async with self.request("/app/api/viewer/category-search", query="mine") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["results"][0], {"id": "100", "name": "Minecraft"})
        async with self.request(
            "/app/api/viewer/category-alert", login="alpha", enabled=True,
            category_ids=["100"], expected_version=0, category_names=["fake"],
        ) as response:
            self.assertEqual(response.status, 200, await response.text())
            self.assertEqual((await response.json())["category_names"], ["Minecraft"])
        async with self.request("/app/api/viewer/state") as response:
            row = (await response.json())["subscriptions"][0]
            self.assertEqual(row["category_alert"]["category_ids"], ["100"])
        await self.db.revoke_test_viewer_plus(grant, revoked_at=now + 1, issued_by=OWNER_ID)
        async with self.request(
            "/app/api/viewer/category-alert", login="alpha", enabled=False,
            category_ids=[], expected_version=1,
        ) as response:
            self.assertEqual(response.status, 403)
