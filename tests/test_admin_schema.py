import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from bot.database import Database


class AdminSchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "bot.db")
        self.db = Database(self.path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def columns(self, table):
        cursor = await self.db.conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in await cursor.fetchall()}

    async def test_known_people_are_seeded_into_profiles(self):
        path = os.path.join(self.tmp.name, "seeded.db")
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                "CREATE TABLE known_private_users (user_id INTEGER PRIMARY KEY);"
                "INSERT INTO known_private_users (user_id) VALUES (111),(222);"
            )
        db = Database(path)
        await db.connect()
        try:
            cursor = await db.conn.execute(
                "SELECT user_id,last_active_at FROM telegram_user_profiles ORDER BY user_id"
            )
            rows = await cursor.fetchall()
            self.assertEqual([row[0] for row in rows], [111, 222])
            # 0 = «неизвестно», чтобы не искажать «активны сегодня».
            self.assertEqual([row[1] for row in rows], [0, 0])

            # Повторный вход не дублирует и не затирает живой профиль.
            await db.remember_profile(111, username="alex", display_name="Alex",
                                      language_code="ru", now=1_700_000_000.0)
            await db.touch_activity(111, now=1_700_000_000.0)
            await db.close()
            db = Database(path)
            await db.connect()
            cursor = await db.conn.execute(
                "SELECT COUNT(*), MAX(last_active_at) FROM telegram_user_profiles"
            )
            count, active = await cursor.fetchone()
            self.assertEqual(count, 2)
            self.assertEqual(active, 1_700_000_000.0)
            cursor = await db.conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version='admin_002_known_people'"
            )
            self.assertIsNotNone(await cursor.fetchone())
        finally:
            await db.close()

    async def test_profiles_table_and_journal_columns_exist(self):
        profiles = await self.columns("telegram_user_profiles")
        self.assertLessEqual(
            {"user_id", "username", "display_name", "language_code",
             "first_seen_at", "profile_seen_at", "last_active_at"},
            profiles,
        )
        events = await self.columns("entitlement_events")
        self.assertLessEqual(
            {"reason", "reason_note", "comment", "previous_grant_id",
             "previous_expires_at", "new_expires_at", "request_key"},
            events,
        )

    async def test_migration_is_idempotent_and_keeps_rows(self):
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants (grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,issued_by,created_at) "
            "VALUES ('g1','k1','viewer','111','viewer_plus','test',1,100,42,1)"
        )
        await self.db.conn.execute(
            "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at) "
            "VALUES ('g1','grant',42,1)"
        )
        await self.db.conn.commit()
        await self.db.close()

        self.db = Database(self.path)
        await self.db.connect()

        cursor = await self.db.conn.execute(
            "SELECT grant_id,action,actor_telegram_id,happened_at FROM entitlement_events"
        )
        self.assertEqual(await cursor.fetchall(), [("g1", "grant", 42, 1)])
        self.assertIn("admin_001_profiles", await self.db.schema_versions())

    async def test_extend_action_is_allowed_after_migration(self):
        await self.db.conn.execute(
            "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at) "
            "VALUES ('g1','extend',42,5)"
        )
        await self.db.conn.commit()

    async def test_reason_columns_default_to_null(self):
        await self.db.conn.execute(
            "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at) "
            "VALUES ('g2','grant',42,2)"
        )
        await self.db.conn.commit()

        cursor = await self.db.conn.execute(
            "SELECT reason,reason_note,comment FROM entitlement_events WHERE grant_id='g2'"
        )
        self.assertEqual(await cursor.fetchone(), (None, None, None))

    async def test_rebuild_keeps_existing_reason_columns(self):
        path = os.path.join(self.tmp.name, "legacy.db")
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(
                "CREATE TABLE entitlement_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, grant_id TEXT NOT NULL, "
                "action TEXT NOT NULL CHECK(action IN ('grant','revoke')), "
                "actor_telegram_id INTEGER NOT NULL, happened_at REAL NOT NULL, "
                "reason TEXT, comment TEXT);"
                "INSERT INTO entitlement_events "
                "(grant_id,action,actor_telegram_id,happened_at,reason,comment) "
                "VALUES ('g1','grant',42,1,'partnership','важное');"
            )
        db = Database(path)
        await db.connect()
        try:
            cursor = await db.conn.execute(
                "SELECT reason,comment FROM entitlement_events WHERE grant_id='g1'"
            )
            self.assertEqual(await cursor.fetchone(), ("partnership", "важное"))
        finally:
            await db.close()

    async def test_event_ids_survive_table_rebuild(self):
        for index in range(3):
            await self.db.conn.execute(
                "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at) "
                "VALUES (?, 'grant', 42, ?)", (f"g{index}", float(index)),
            )
        await self.db.conn.commit()
        cursor = await self.db.conn.execute("SELECT id FROM entitlement_events ORDER BY id")
        before = [row[0] for row in await cursor.fetchall()]
        await self.db.close()

        self.db = Database(self.path)
        await self.db.connect()
        cursor = await self.db.conn.execute("SELECT id FROM entitlement_events ORDER BY id")
        self.assertEqual([row[0] for row in await cursor.fetchall()], before)


if __name__ == "__main__":
    unittest.main()
