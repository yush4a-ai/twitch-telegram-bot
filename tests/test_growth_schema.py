import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

import aiosqlite

from bot.database import Database, SCHEMA


class GrowthSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_existing_databases_get_versioned_additive_tables(self):
        for old_schema in (False, True):
            with self.subTest(old_schema=old_schema), tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "bot.db")
                if old_schema:
                    with closing(sqlite3.connect(path)) as connection:
                        connection.executescript(SCHEMA)
                db = Database(path)
                await db.connect()
                try:
                    self.assertEqual(
                        await db.schema_versions(),
                        [
                            "r3_001_observations", "r3_002_notification_jobs",
                            "r3_003_live_update_revision",
                            "r4_001_streamer_access",
                            "r4_002_streamer_communities",
                            "r4_003_streamer_templates",
                            "r4_004_streamer_stats",
                        ],
                    )
                    cursor = await db.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                        "('stream_observations', 'stream_observation_memberships', 'notification_jobs')"
                    )
                    self.assertEqual(len(await cursor.fetchall()), 3)
                    cursor = await db.conn.execute(
                        "EXPLAIN QUERY PLAN SELECT id FROM notification_jobs "
                        "WHERE status='pending' AND due_at <= 10 ORDER BY due_at LIMIT 10"
                    )
                    self.assertIn("idx_notification_jobs_due", str(await cursor.fetchall()))
                finally:
                    await db.close()
                reopened = Database(path)
                await reopened.connect()
                try:
                    self.assertEqual(len(await reopened.schema_versions()), 7)
                finally:
                    await reopened.close()

    async def test_unique_keys_block_duplicate_observation_and_job(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(os.path.join(directory, "bot.db"))
            await db.connect()
            try:
                observation = ("login", "stream", 100.0, 10, "title", "game")
                await db.conn.execute(
                    "INSERT INTO stream_observations VALUES (?, ?, ?, ?, ?, ?)", observation
                )
                with self.assertRaises(aiosqlite.IntegrityError):
                    await db.conn.execute(
                        "INSERT INTO stream_observations VALUES (?, ?, ?, ?, ?, ?)", observation
                    )
                await db.conn.rollback()
                job = ("go_live", 1, "login", "stream", 1, 100.0, "pending", 0, None, 100.0, 100.0, None)
                await db.conn.execute(
                    "INSERT INTO notification_jobs "
                    "(kind, chat_id, twitch_login, logical_stream_id, payload_version, due_at, status, "
                    "attempt_count, lease_until, created_at, updated_at, last_error_class) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", job
                )
                with self.assertRaises(aiosqlite.IntegrityError):
                    await db.conn.execute(
                        "INSERT INTO notification_jobs "
                        "(kind, chat_id, twitch_login, logical_stream_id, payload_version, due_at, status, "
                        "attempt_count, lease_until, created_at, updated_at, last_error_class) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", job
                    )
            finally:
                await db.close()

    async def test_failed_growth_migration_rolls_back_its_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bot.db")
            db = Database(path)

            async def failing_growth_migration():
                await db.conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
                raise RuntimeError("injected migration failure")

            db._migrate_growth_schema = failing_growth_migration
            try:
                with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                    await db.connect()
            finally:
                await db.close()
            with closing(sqlite3.connect(path)) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("schema_migrations", tables)


if __name__ == "__main__":
    unittest.main()
