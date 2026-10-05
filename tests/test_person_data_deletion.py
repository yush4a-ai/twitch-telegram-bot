"""Удаление и выгрузка персональных данных человека по его запросу."""
import tempfile
import time
import unittest
from pathlib import Path

from bot.database import Database

OTHER = 202


class PersonDataDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(str(Path(self.directory.name) / "person.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = time.time()

    async def seed(self, user_id: int) -> None:
        """Личные данные человека: профиль, канал, пояс, тихие часы, избранное, доступ."""
        await self.db.remember_profile(
            user_id, username=f"user{user_id}", display_name="Тест",
            language_code="ru", now=self.now,
        )
        await self.db.add_channel(user_id, f"channel{user_id}")
        await self.db.set_utc_offset(user_id, 180)
        await self.db.set_quiet_hours(user_id, start_minute=1380, end_minute=420, utc_offset_minutes=180)
        await self.db.set_viewer_favorite(user_id, f"channel{user_id}", True)
        await self.db.grant_manual_access(
            user_id, "viewer_plus", expires_at=self.now + 86400, reason="testing",
            issued_by=1, request_key=f"key-{user_id}", now=self.now,
        )

    async def leftovers(self, user_id: int) -> dict[str, int]:
        """Что ещё осталось от человека в личных таблицах."""
        pairs = [
            *Database._PERSON_BY_USER_ID,
            *Database._PERSON_BY_CHAT_ID,
            *Database._PERSON_BY_TELEGRAM_ID,
        ]
        found: dict[str, int] = {}
        for table, column in pairs:
            if not await self.db._table_exists(table):
                continue
            cursor = await self.db.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,)
            )
            count = (await cursor.fetchone())[0]
            if count:
                found[f"{table}.{column}"] = count
        return found

    async def test_delete_removes_the_person_and_keeps_everyone_else(self):
        await self.seed(101)
        await self.seed(OTHER)
        self.assertTrue(await self.leftovers(101))
        removed = await self.db.delete_person_data(101)
        self.assertTrue(removed)
        self.assertEqual(await self.leftovers(101), {})
        self.assertTrue(await self.leftovers(OTHER), "данные другого человека должны остаться")
        self.assertEqual(await self.db.list_channels(OTHER), [f"channel{OTHER}"])

    async def test_export_returns_only_that_person(self):
        await self.seed(101)
        await self.seed(OTHER)
        data = await self.db.export_person_data(101)
        self.assertEqual(data["telegram_id"], 101)
        self.assertEqual(data["profile"][0]["user_id"], 101)
        self.assertEqual([row["twitch_login"] for row in data["channels"]], ["channel101"])
        self.assertEqual([row["plan"] for row in data["access"]], ["viewer_plus"])
        self.assertEqual(data["quiet_hours"][0]["start_minute"], 1380)
        self.assertEqual(data["timezone"][0]["utc_offset_minutes"], 180)

    async def test_repeated_delete_is_safe(self):
        await self.seed(101)
        await self.db.delete_person_data(101)
        self.assertEqual(await self.db.delete_person_data(101), {})

    async def test_invalid_id_is_rejected(self):
        for bad in (0, "101", None, 10.5):
            with self.assertRaises(ValueError):
                await self.db.delete_person_data(bad)
            with self.assertRaises(ValueError):
                await self.db.export_person_data(bad)

    async def test_group_chat_id_is_allowed(self):
        # Отрицательный ID — это группа или канал: удаление данных группы законно.
        group = -1001234567890
        await self.db.add_channel(group, "groupchannel")
        removed = await self.db.delete_person_data(group)
        self.assertEqual(removed.get("tracked_channels.chat_id"), 1)
        self.assertEqual(await self.db.list_channels(group), [])

    async def test_paid_grant_survives_as_legal_record(self):
        await self.seed(101)
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants (grant_id, request_key, subject_kind, subject_id,"
            " plan, source, starts_at, expires_at, issued_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("paid-1", "paid-key", "viewer", "101", "viewer_plus", "paid",
             self.now, self.now + 86400, 1, self.now),
        )
        await self.db.conn.commit()
        await self.db.delete_person_data(101)
        cursor = await self.db.conn.execute(
            "SELECT source FROM entitlement_grants WHERE subject_id = '101' ORDER BY source"
        )
        self.assertEqual([row[0] for row in await cursor.fetchall()], ["paid"])
