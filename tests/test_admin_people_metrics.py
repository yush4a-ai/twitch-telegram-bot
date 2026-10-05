"""Панель владельца показывает людей: новые, вернувшиеся и путь до уведомления.

Проверка идёт на настоящей базе, а не на заглушках: важно, что числа берутся из
фактических записей и что воронка доходит до первого доставленного уведомления.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from bot.admin_metrics import AdminSnapshot
from bot.audience_metrics import DAY_SECONDS
from bot.database import Database


class AdminPeopleMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = str(Path(self.directory.name) / "panel.db")
        self.db = Database(path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.path = path
        self.now = 1_800_000_000.0

    async def profile(self, user_id: int, *, first_seen: float, last_active: float) -> None:
        await self.db.conn.execute(
            "INSERT INTO telegram_user_profiles (user_id, username, display_name, language_code,"
            " first_seen_at, profile_seen_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, f"user{user_id}", "Тест", "ru", first_seen, first_seen, last_active),
        )
        await self.db.conn.commit()

    def snapshot(self) -> AdminSnapshot:
        health = Mock(health_snapshot=Mock(return_value={}))
        return AdminSnapshot(
            self.db, health, health, health, None,
            db_path=self.path, telegram_polling_provider=lambda: True,
            environment="staging", directory=None,
        )

    async def test_panel_receives_people_metrics_and_funnel(self):
        await self.profile(101, first_seen=self.now - 10 * DAY_SECONDS, last_active=self.now - 60)
        await self.profile(202, first_seen=self.now - 2 * DAY_SECONDS, last_active=self.now - 2 * DAY_SECONDS)
        await self.db.add_channel(101, "alpha")
        await self.db.add_stream_history(
            101, "alpha", "stream-1", self.now - 60, 3600, 10, 5, 0,
            started_at=self.now - 3660, title="Тест",
        )
        await self.db.conn.execute(
            "INSERT INTO viewer_event_history (telegram_user_id, event_key, kind, twitch_login,"
            " logical_stream_id, category_name, outcome, happened_at) VALUES (?,?,?,?,?,?,?,?)",
            (101, "go_live:alpha:stream-1", "go_live", "alpha", "stream-1", None, "sent",
             self.now - 60),
        )
        await self.db.conn.commit()

        result = await self.snapshot().collect()
        people = result["people"]

        self.assertIsNotNone(people)
        self.assertEqual(people["known_people"], 2)
        self.assertEqual(people["returned"], 1)
        self.assertEqual(people["reached_live"], 1)
        self.assertEqual(people["with_delivery"], 1)
        titles = [step["title"] for step in people["funnel"]]
        self.assertEqual(titles, [
            "Открыли бота", "Добавили стримера", "Дождались эфира", "Остались на неделю",
        ])
        values = {step["title"]: step["value"] for step in people["funnel"]}
        self.assertEqual(values["Дождались эфира"], 1)

    async def test_empty_database_does_not_invent_numbers(self):
        result = await self.snapshot().collect()
        people = result["people"]
        self.assertEqual(people["known_people"], 0)
        self.assertIsNone(people["funnel"][0]["share_of_previous"])


if __name__ == "__main__":
    unittest.main()
