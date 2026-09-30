import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from bot.database import Database, SCHEMA


class StreamerAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "bot.db")
        self.db = Database(self.path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_verified_link_is_one_to_one_and_legacy_token_is_not_identity(self):
        await self.db.save_user_token("alpha", "11", "access", "refresh", 500)
        self.assertIsNone(await self.db.get_streamer_identity(101))
        self.assertFalse(await self.db.has_streamer_plus(101, now=100))

        self.assertTrue(await self.db.link_streamer_identity(101, "11", "Alpha", verified_at=100))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertFalse(await self.db.link_streamer_identity(202, "11", "alpha", verified_at=110))
        self.assertFalse(await self.db.link_streamer_identity(101, "22", "beta", verified_at=110))
        self.assertTrue(await self.db.link_streamer_identity(101, "11", "ALPHA", verified_at=120))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))

    async def test_grant_idempotency_expiry_revoke_and_audit(self):
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        grant = await self.db.issue_test_streamer_plus(
            "11", "test-order-1", starts_at=100, expires_at=200, issued_by=425785231, now=90,
        )
        self.assertEqual(
            grant,
            await self.db.issue_test_streamer_plus(
                "11", "test-order-1", starts_at=100, expires_at=200,
                issued_by=425785231, now=95,
            ),
        )
        with self.assertRaises(ValueError):
            await self.db.issue_test_streamer_plus(
                "11", "test-order-1", starts_at=100, expires_at=201,
                issued_by=425785231, now=95,
            )
        self.assertFalse(await self.db.has_streamer_plus(101, now=99.99))
        self.assertTrue(await self.db.has_streamer_plus(101, now=100))
        self.assertTrue(await self.db.has_streamer_plus(101, now=199.99))
        self.assertFalse(await self.db.has_streamer_plus(101, now=200))
        self.assertFalse(await self.db.has_streamer_plus(202, now=150))
        self.assertTrue(await self.db.revoke_test_streamer_plus(grant, revoked_at=150, issued_by=425785231))
        self.assertFalse(await self.db.revoke_test_streamer_plus(grant, revoked_at=151, issued_by=425785231))
        self.assertFalse(await self.db.has_streamer_plus(101, now=151))
        cursor = await self.db.conn.execute("SELECT action FROM entitlement_events ORDER BY id")
        self.assertEqual([row[0] for row in await cursor.fetchall()], ["grant", "revoke"])

    async def test_invalid_grant_and_unknown_link_do_not_write(self):
        with self.assertRaises(ValueError):
            await self.db.issue_test_streamer_plus(
                "11", "key", starts_at=100, expires_at=200, issued_by=425785231,
            )
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        for key, start, expiry in (("", 100, 200), ("key", 200, 200), ("key", 201, 200)):
            with self.subTest(key=key, start=start, expiry=expiry), self.assertRaises(ValueError):
                await self.db.issue_test_streamer_plus(
                    "11", key, starts_at=start, expires_at=expiry, issued_by=425785231,
                )
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 0)


class StreamerSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_additive_migration_on_old_db_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "old.db")
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(SCHEMA)
            for _ in range(2):
                db = Database(path)
                await db.connect()
                try:
                    self.assertIn("r4_001_streamer_access", await db.schema_versions())
                    cursor = await db.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                        "('streamer_identities', 'entitlement_grants', 'entitlement_events')"
                    )
                    self.assertEqual(len(await cursor.fetchall()), 3)
                finally:
                    await db.close()

    async def test_failed_streamer_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bot.db")
            db = Database(path)

            async def fail():
                await db.conn.execute("CREATE TABLE test_partial_streamer (id INTEGER)")
                raise RuntimeError("injected streamer failure")

            db._migrate_streamer_schema = fail
            try:
                with self.assertRaisesRegex(RuntimeError, "injected streamer failure"):
                    await db.connect()
            finally:
                await db.close()
            with closing(sqlite3.connect(path)) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("test_partial_streamer", names)


if __name__ == "__main__":
    unittest.main()
