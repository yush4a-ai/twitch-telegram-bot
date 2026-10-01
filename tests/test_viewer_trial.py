"""One voluntary seven-day Viewer Plus test grant per verified Telegram ID."""

import asyncio
import os
import tempfile
import time
import unittest

from bot.database import Database
from bot.viewer_trial import DAY, TrialAlreadyUsed, ViewerTrialService


class ViewerTrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "trial.db")
        self.db = Database(self.path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.service = ViewerTrialService(self.db)
        self.now = time.time()

    async def test_explicit_start_is_once_only_and_expires_to_free(self):
        before = await self.service.status(101, now=self.now)
        self.assertFalse(before.used)
        self.assertFalse(await self.db.has_viewer_plus(101, now=self.now))
        started = await self.service.start(101, now=self.now)
        self.assertTrue(started.started_now)
        self.assertEqual(started.expires_at, self.now + 7 * DAY)
        self.assertTrue(await self.db.has_viewer_plus(101, now=self.now + 7 * DAY - 1))
        again = await self.service.start(101, now=self.now + DAY)
        self.assertFalse(again.started_now)
        self.assertEqual(again.expires_at, started.expires_at)
        self.assertFalse(await self.db.has_viewer_plus(101, now=started.expires_at))
        with self.assertRaises(TrialAlreadyUsed):
            await self.service.start(101, now=started.expires_at + 1)
        self.assertTrue((await self.service.status(101, now=started.expires_at + 1)).used)
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key='trial:v1:101'"
        )
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_current_other_plus_does_not_consume_trial(self):
        grant_id = await self.db.issue_test_viewer_plus(
            101, "separate-plus", starts_at=self.now - 1,
            expires_at=self.now + 60, issued_by=425785231, now=self.now,
        )
        with self.assertRaises(PermissionError):
            await self.service.start(101, now=self.now)
        self.assertFalse((await self.service.status(101, now=self.now)).used)
        await self.db.revoke_test_viewer_plus(
            grant_id, revoked_at=self.now + 1, issued_by=425785231,
        )
        started = await self.service.start(101, now=self.now + 2)
        self.assertTrue(started.started_now)

    async def test_two_connections_race_creates_one_grant(self):
        second = Database(self.path)
        await second.connect()
        self.addAsyncCleanup(second.close)
        first, other = await asyncio.gather(
            self.service.start(202, now=self.now),
            ViewerTrialService(second).start(202, now=self.now),
        )
        self.assertEqual(first.expires_at, other.expires_at)
        self.assertEqual(sorted([first.started_now, other.started_now]), [False, True])
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM entitlement_grants WHERE request_key='trial:v1:202'"
        )
        self.assertEqual((await cursor.fetchone())[0], 1)


if __name__ == "__main__":
    unittest.main()
