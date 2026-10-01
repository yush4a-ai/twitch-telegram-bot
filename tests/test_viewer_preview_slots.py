"""Saved video selection counts offline rows and is version fenced."""

import asyncio
import os
import tempfile
import time
import unittest

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp
from tests.test_mini_app_viewer import FakeTwitch


class IdTwitch(FakeTwitch):
    async def get_user_id(self, login):
        return str(1000 + int(login[4:])) if login in self.existing else None


class ViewerPreviewSlotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.twitch = IdTwitch()
        self.twitch.existing.update(f"user{i}" for i in range(7))
        for index in range(7):
            await self.db.add_channel(101, f"user{index}")
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
            mini_app_twitch=self.twitch,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def save(self, logins, version, actor=101):
        async with self.session.post(
            self.base + "/app/api/viewer/video-selection",
            json={"init_data": signed_webapp(actor), "selected_logins": logins,
                  "expected_version": version},
        ) as response:
            return response.status, await response.json()

    async def grant(self):
        now = time.time()
        return await self.db.issue_test_viewer_plus(
            101, "video-test", starts_at=now - 10, expires_at=now + 3600,
            issued_by=425785231, now=now,
        )

    async def test_free_rejected_five_offline_count_sixth_rejected_and_swap_atomic(self):
        self.assertEqual((await self.save(["user0"], 0))[0], 403)
        await self.grant()
        status, result = await self.save([f"user{i}" for i in range(5)], 0)
        self.assertEqual(status, 200)
        self.assertEqual(result["selected_ids"], [str(1000 + i) for i in range(5)])
        self.assertEqual(result["limit"], 5)
        self.assertEqual((await self.save([f"user{i}" for i in range(6)], 1))[0], 409)
        self.assertEqual((await self.save(["user0", "user1", "user2", "user3", "user5"], 1))[0], 200)
        self.assertEqual((await self.save(["user0", "user1"], 1))[0], 409)
        with self.assertRaises(ValueError):
            await self.db.replace_video_selection(
                101, [("1000", "user0"), ("9000", "foreign")],
                expected_version=2,
            )
        selected = await self.db.get_video_selection(101)
        self.assertEqual(selected.version, 2)
        self.assertEqual(selected.selected_ids, ("1000", "1001", "1002", "1003", "1005"))

    async def test_ownership_notify_pause_unfollow_and_expiry(self):
        await self.grant()
        self.assertEqual((await self.save(["user0"], 0, actor=202))[0], 403)
        now = time.time()
        await self.db.issue_test_viewer_plus(
            202, "foreign-video-test", starts_at=now - 10, expires_at=now + 3600,
            issued_by=425785231, now=now,
        )
        self.assertEqual((await self.save(["user0"], 0, actor=202))[0], 400)
        self.assertEqual((await self.save(["other"], 0))[0], 400)
        await self.db.set_notify_enabled(101, "user0", False)
        self.assertEqual((await self.save(["user0"], 0))[0], 200)
        self.assertEqual((await self.db.get_video_selection(101)).selected_ids, ("1000",))
        self.assertEqual((await self.db.get_video_selection(101)).effective_ids, ())
        async with self.session.post(
            self.base + "/app/api/viewer/unfollow",
            json={"init_data": signed_webapp(101), "login": "user0"},
        ) as response:
            self.assertEqual(response.status, 200)
            removed = await response.json()
            self.assertEqual(removed["video_selection"]["version"], 2)
            self.assertEqual(removed["video_selection"]["selected_logins"], [])
        self.assertEqual((await self.db.get_video_selection(101)).selected_ids, ())
        self.assertEqual((await self.save(["user1"], 2))[0], 200)
        await self.db.conn.execute(
            "UPDATE entitlement_grants SET revoked_at=? WHERE subject_kind='viewer'",
            (time.time(),),
        )
        await self.db.conn.commit()
        selection = await self.db.get_video_selection(101)
        self.assertEqual(selection.selected_ids, ("1001",))
        self.assertEqual(selection.effective_ids, ())
        await self.db.issue_test_viewer_plus(
            101, "video-restored", starts_at=time.time() - 1,
            expires_at=time.time() + 3600, issued_by=425785231,
        )
        self.assertEqual((await self.db.get_video_selection(101)).effective_ids, ("1001",))

    async def test_legacy_preview_flag_does_not_create_a_personal_selection(self):
        await self.db.set_preview_enabled(101, "user0", True)
        saved = await self.db.get_video_selection(101)
        self.assertEqual(saved.selected_ids, ())
        self.assertEqual(saved.effective_ids, ())
        async with self.session.post(
            self.base + "/app/api/viewer/state",
            json={"init_data": signed_webapp(101)},
        ) as response:
            state = await response.json()
            self.assertFalse(next(row for row in state["subscriptions"] if row["login"] == "user0")["video_selected"])

    async def test_concurrent_stale_writes_only_one_wins(self):
        await self.grant()
        self.assertEqual((await self.save(["user0", "user1", "user2", "user3"], 0))[0], 200)
        results = await asyncio.gather(
            self.save(["user0", "user1", "user2", "user3", "user4"], 1),
            self.save(["user0", "user1", "user2", "user3", "user5"], 1),
        )
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(len((await self.db.get_video_selection(101)).selected_ids), 5)

    async def test_two_sqlite_connections_fence_same_version(self):
        await self.grant()
        self.assertEqual((await self.save(["user0", "user1", "user2", "user3"], 0))[0], 200)
        other = Database(self.db._path)
        await other.connect()
        try:
            results = await asyncio.gather(
                self.db.replace_video_selection(
                    101, [(str(1000 + i), f"user{i}") for i in (0, 1, 2, 3, 4)],
                    expected_version=1,
                ),
                other.replace_video_selection(
                    101, [(str(1000 + i), f"user{i}") for i in (0, 1, 2, 3, 5)],
                    expected_version=1,
                ),
            )
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(len((await self.db.get_video_selection(101)).selected_ids), 5)
        finally:
            await other.close()
