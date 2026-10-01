"""Personal plan limit is shared by bot, Mini App and dispatch snapshots."""

import asyncio
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing

import aiohttp

from bot.database import Database
from bot.handlers.streams import _add_validated_tracking
from bot.notification_queue import NotificationJob
from bot.notification_worker import NotificationOutcome
from bot.oauth import OAuthCallbackServer
from bot.poller import StreamPoller
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp
from tests.test_mini_app_viewer import FakeTwitch


class ViewerPlusLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.twitch = FakeTwitch()
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

    async def test_bot_and_app_share_50_200_and_group_stays_50(self):
        self.twitch.existing.add("extra")
        for index in range(50):
            await self.db.add_channel(101, f"track{index:03}")
        result, _ = await _add_validated_tracking(101, "extra", self.db, self.twitch)
        self.assertEqual(result, "limit")
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "limit-test", starts_at=now - 10, expires_at=now + 3600,
            issued_by=425785231, now=now,
        )
        result, _ = await _add_validated_tracking(101, "extra", self.db, self.twitch)
        self.assertEqual(result, "created")
        self.assertEqual(await self.db.add_channel_with_limit(-101, "group50", 50), "created")
        for index in range(49):
            await self.db.add_channel(-101, f"group{index:03}")
        self.assertEqual(await self.db.add_channel_with_limit(-101, "group51", 200), "limit")
        for index in range(51, 200):
            self.assertEqual(await self.db.add_channel_with_limit(101, f"track{index:03}", 200), "created")
        self.twitch.existing.add("last")
        async with self.session.post(
            self.base + "/app/api/viewer/follow",
            json={"init_data": signed_webapp(101), "login": "last"},
        ) as response:
            self.assertEqual(response.status, 409)
        self.assertEqual(await self.db.count_channels(101), 200)

    async def test_downgrade_pauses_later_rows_without_changing_manual_mute(self):
        now = time.time()
        grant = await self.db.issue_test_viewer_plus(
            101, "downgrade-test", starts_at=now - 10, expires_at=now + 3600,
            issued_by=425785231, now=now,
        )
        for index in range(51):
            await self.db.add_channel_with_limit(101, f"track{index:03}", 200)
        await self.db.set_notify_enabled(101, "track000", False)
        await self.db.revoke_test_viewer_plus(grant, revoked_at=now, issued_by=425785231)
        rows = await self.db.list_personal_channel_status(101)
        self.assertEqual(len(rows), 51)
        self.assertFalse(rows[0][1])
        self.assertTrue(rows[-1][1])
        self.assertTrue(rows[-1][4])  # paused_by_plan, distinct from manual mute
        by_login, states = await self.db.snapshot_tracked_state()
        self.assertNotIn(101, by_login.get("track050", []))
        self.assertIn(101, by_login["track049"])
        self.assertFalse(states[(101, "track000")][9])
        await self.db.issue_test_viewer_plus(
            101, "renewed-limit-test", starts_at=time.time() - 1,
            expires_at=time.time() + 3600, issued_by=425785231,
        )
        restored, states = await self.db.snapshot_tracked_state()
        self.assertIn(101, restored["track050"])
        self.assertFalse(states[(101, "track000")][9])

    async def test_parallel_add_does_not_cross_personal_limit(self):
        for index in range(49):
            await self.db.add_channel(101, f"track{index:03}")
        results = await asyncio.gather(
            self.db.add_channel_with_limit(101, "alpha", 200),
            self.db.add_channel_with_limit(101, "beta", 200),
        )
        self.assertEqual(sorted(results), ["created", "limit"])
        self.assertEqual(await self.db.count_channels(101), 50)

    async def test_creation_order_precedes_login_when_free_limit_is_reached(self):
        for index in range(49):
            await self.db.add_channel(303, f"track{index:03}")
        await self.db.add_channel(303, "zulu")
        await self.db.add_channel(303, "alpha")
        rows = {row[0]: row for row in await self.db.list_personal_channel_status(303)}
        self.assertFalse(rows["zulu"][4])
        self.assertTrue(rows["alpha"][4])

    async def test_two_database_connections_share_the_atomic_limit(self):
        for index in range(49):
            await self.db.add_channel(303, f"track{index:03}")
        other = Database(self.db._path)
        await other.connect()
        try:
            results = await asyncio.gather(
                self.db.add_channel_with_limit(303, "alpha", 200),
                other.add_channel_with_limit(303, "beta", 200),
            )
            self.assertEqual(sorted(results), ["created", "limit"])
            self.assertEqual(await self.db.count_channels(303), 50)
        finally:
            await other.close()

    async def test_viewer_can_choose_any_active_50_without_unfollow(self):
        for index in range(52):
            await self.db.add_channel(101, f"track{index:03}")
        async with self.session.post(
            self.base + "/app/api/viewer/plan-activate",
            json={"init_data": signed_webapp(101), "login": "track051"},
        ) as response:
            self.assertEqual(response.status, 200)
        rows = {row[0]: row for row in await self.db.list_personal_channel_status(101)}
        self.assertFalse(rows["track051"][4])
        self.assertTrue(rows["track049"][4])
        self.assertEqual(await self.db.count_channels(101), 52)
        by_login, _ = await self.db.snapshot_tracked_state()
        self.assertIn(101, by_login["track051"])
        self.assertNotIn(101, by_login.get("track049", []))

    async def test_legacy_rows_get_deterministic_added_order_on_migration(self):
        for index in range(52):
            await self.db.add_channel(303, f"legacy{index:03}")
        await self.db.close()
        with closing(sqlite3.connect(self.db._path)) as connection:
            connection.execute("ALTER TABLE tracked_channels DROP COLUMN added_at")
            connection.execute("DROP TABLE viewer_plan_priority")
            connection.execute("DROP TABLE viewer_video_selections")
            connection.execute("DROP TABLE viewer_video_selection_state")
            connection.execute(
                "DELETE FROM schema_migrations WHERE version='mini_001_viewer_preferences'"
            )
            connection.commit()
        self.db = Database(self.db._path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        rows = {row[0]: row for row in await self.db.list_personal_channel_status(303)}
        self.assertFalse(rows["legacy049"][4])
        self.assertTrue(rows["legacy050"][4])
        self.assertTrue(rows["legacy051"][4])
        self.assertIn("mini_001_viewer_preferences", await self.db.schema_versions())

    async def test_queued_paid_overflow_cannot_send_after_downgrade(self):
        for index in range(51):
            await self.db.add_channel(101, f"track{index:03}")
        poller = object.__new__(StreamPoller)
        poller._db = self.db
        job = NotificationJob(
            id=1, kind="go_live", chat_id=101, twitch_login="track050",
            logical_stream_id="live-1", payload_version=1, due_at=1,
            attempt_count=1, lease_until=100,
        )
        self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
