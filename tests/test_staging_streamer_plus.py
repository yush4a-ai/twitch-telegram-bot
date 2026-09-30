import os
import tempfile
import unittest

from bot.database import Database
from scripts.staging_streamer_plus import apply_grant, apply_revoke, verify_staging_runtime


TARGET_ENV = {
    "RAILWAY_ENVIRONMENT_NAME": "staging",
    "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
    "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
    "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
    "RAILWAY_VOLUME_MOUNT_PATH": "/data",
    "DB_PATH": "/data/bot.db",
    "OWNER_CHAT_ID": "425785231",
}


class StagingStreamerGrantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_runtime_rejects_production_wrong_ids_or_wrong_volume(self):
        self.assertEqual(verify_staging_runtime(TARGET_ENV), 425785231)
        for patch in (
            {"RAILWAY_ENVIRONMENT_NAME": "production"},
            {"RAILWAY_PROJECT_ID": "other"},
            {"RAILWAY_ENVIRONMENT_ID": "other"},
            {"RAILWAY_SERVICE_ID": "other"},
            {"RAILWAY_VOLUME_MOUNT_PATH": "/other"},
            {"DB_PATH": "/data/other.db"},
            {"OWNER_CHAT_ID": ""},
            {"OWNER_CHAT_ID": "12345"},
        ):
            with self.subTest(patch=patch), self.assertRaises(PermissionError):
                verify_staging_runtime({**TARGET_ENV, **patch})

    async def test_grant_retry_revoke_and_expiry_on_test_identity(self):
        actor = verify_staging_runtime(TARGET_ENV)
        grant_id = await apply_grant(
            self.db, broadcaster_id="11", request_key="pilot-1",
            starts_at=100, expires_at=200, issued_by=actor, now=90,
        )
        self.assertEqual(
            await apply_grant(
                self.db, broadcaster_id="11", request_key="pilot-1",
                starts_at=100, expires_at=200, issued_by=actor, now=91,
            ), grant_id,
        )
        self.assertTrue(await self.db.has_streamer_plus(101, now=150))
        self.assertFalse(await self.db.has_streamer_plus(101, now=200))
        self.assertTrue(await apply_revoke(self.db, grant_id=grant_id, issued_by=actor, now=151))
        self.assertFalse(await apply_revoke(self.db, grant_id=grant_id, issued_by=actor, now=152))
        self.assertFalse(await self.db.has_streamer_plus(101, now=151))

    async def test_unknown_broadcaster_cannot_receive_grant(self):
        with self.assertRaises(ValueError):
            await apply_grant(
                self.db, broadcaster_id="22", request_key="unknown",
                starts_at=100, expires_at=200, issued_by=425785231, now=90,
            )


if __name__ == "__main__":
    unittest.main()
