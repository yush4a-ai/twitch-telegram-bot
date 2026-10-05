"""Метрики аудитории: считаем только то, что есть в базе, и без выдуманных чисел."""
import tempfile
import unittest
from pathlib import Path

from bot.audience_metrics import DAY_SECONDS, collect_audience, funnel, msk_day_start
from bot.database import Database


class AudienceMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(str(Path(self.directory.name) / "metrics.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = 1_800_000_000.0

    async def profile(self, user_id: int, *, first_seen: float, last_active: float) -> None:
        await self.db.conn.execute(
            "INSERT INTO telegram_user_profiles (user_id, username, display_name, language_code,"
            " first_seen_at, profile_seen_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, f"user{user_id}", "Тест", "ru", first_seen, first_seen, last_active),
        )
        await self.db.conn.commit()

    async def test_counts_people_by_first_and_last_seen(self):
        await self.profile(101, first_seen=self.now - 10 * DAY_SECONDS, last_active=self.now - 3600)
        await self.profile(202, first_seen=self.now - 2 * DAY_SECONDS, last_active=self.now - 2 * DAY_SECONDS)
        await self.profile(303, first_seen=self.now - 40 * DAY_SECONDS, last_active=self.now - 20 * DAY_SECONDS)
        await self.profile(404, first_seen=self.now - 60 * DAY_SECONDS, last_active=self.now - 60 * DAY_SECONDS)

        metrics = await collect_audience(self.db, now=self.now)

        self.assertEqual(metrics.known_people, 4)
        self.assertEqual(metrics.new_7d, 1)      # только 202
        self.assertEqual(metrics.new_30d, 2)     # 202 и 101
        self.assertEqual(metrics.active_7d, 2)   # 101 активен сейчас, 202 заходил два дня назад
        self.assertEqual(metrics.active_today, 1)
        self.assertEqual(metrics.returned, 2)    # 101 и 303 заходили позже первого дня
        self.assertEqual(metrics.stayed_week, 2)

    async def test_funnel_counts_steps_and_shares(self):
        await self.profile(101, first_seen=self.now - 10 * DAY_SECONDS, last_active=self.now - 3600)
        await self.profile(202, first_seen=self.now - 2 * DAY_SECONDS, last_active=self.now - 2 * DAY_SECONDS)
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(202, "beta")
        # Эфир по каналу человека: это измеримый шаг воронки.
        await self.db.add_stream_history(
            101, "alpha", "stream-1", self.now - 60, 3600, 10, 5, 0,
            started_at=self.now - 3660, title="Тест",
        )
        # Подтверждённая доставка: история событий зрителя пишется при Plus.
        await self.db.conn.execute(
            "INSERT INTO viewer_event_history (telegram_user_id, event_key, kind, twitch_login,"
            " logical_stream_id, category_name, outcome, happened_at) VALUES (?,?,?,?,?,?,?,?)",
            (101, "go_live:alpha:stream-1", "go_live", "alpha", "stream-1", None, "sent",
             self.now - 60),
        )
        await self.db.conn.commit()

        metrics = await collect_audience(self.db, now=self.now)
        self.assertEqual(metrics.with_streamers, 2)
        self.assertEqual(metrics.reached_live, 1)
        self.assertEqual(metrics.with_delivery, 1)

        steps = {row["title"]: row for row in funnel(metrics)}
        self.assertEqual(steps["Открыли бота"]["value"], 2)
        self.assertEqual(steps["Добавили стримера"]["value"], 2)
        self.assertEqual(steps["Дождались эфира"]["value"], 1)
        self.assertIsNone(steps["Открыли бота"]["share_of_previous"])
        self.assertEqual(steps["Дождались эфира"]["share_of_previous"], 0.5)

    async def test_empty_database_reports_zero_without_inventing_share(self):
        metrics = await collect_audience(self.db, now=self.now)
        self.assertEqual(metrics.known_people, 0)
        self.assertIsNone(funnel(metrics)[0]["share_of_previous"])
        self.assertIsNone(funnel(metrics)[1]["share_of_previous"])

    def test_today_start_is_moscow_midnight(self):
        start = msk_day_start(self.now)
        self.assertLessEqual(start, self.now)
        self.assertLess(self.now - start, DAY_SECONDS)


if __name__ == "__main__":
    unittest.main()
