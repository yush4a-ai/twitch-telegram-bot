import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from bot.database import Database
from scripts.staging_viewer_plus import apply_grant, apply_revoke, main


class ViewerPlusCommandTests(unittest.TestCase):
    def test_production_runtime_is_rejected_before_db_open(self):
        with patch.dict("os.environ", {
            "RAILWAY_ENVIRONMENT_NAME": "production", "DB_PATH": "/data/bot.db",
        }, clear=True), redirect_stderr(io.StringIO()):
            self.assertEqual(main([
                "grant", "--telegram-user-id", "101", "--request-key", "nope",
                "--starts-at", "100", "--expires-at", "200",
            ]), 2)


class ViewerPlusCommandDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_grant_and_revoke_only_test_viewer_access(self):
        db = Database(":memory:")
        await db.connect()
        try:
            grant = await apply_grant(
                db, telegram_user_id=101, request_key="viewer-cli",
                starts_at=100, expires_at=200, now=100,
            )
            self.assertTrue(await db.has_viewer_plus(101, now=150))
            self.assertFalse(await db.has_streamer_plus(101, now=150))
            self.assertTrue(await apply_revoke(db, grant_id=grant, now=160))
            self.assertFalse(await db.has_viewer_plus(101, now=161))
        finally:
            await db.close()
