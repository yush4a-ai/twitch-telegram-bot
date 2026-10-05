"""Воронка роста: шаги вложены друг в друга, доли считаются честно."""
import os
import tempfile
import unittest

from bot.database import Database

NOW = 1_700_000_000.0
PEOPLE = (9001, 9002, 9003, 9004)


class GrowthFunnelReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def attribute(self, user_id: int, source: str = "site") -> None:
        await self.db.conn.execute(
            "INSERT INTO growth_attributions(telegram_user_id, source_kind, source_code, "
            "first_seen_at) VALUES (?, ?, ?, ?)",
            (user_id, source, f"src_{source}", NOW))
        await self.db.conn.commit()

    async def known(self, user_id: int) -> None:
        await self.db.conn.execute(
            "INSERT OR IGNORE INTO known_private_users(user_id) VALUES (?)", (user_id,))
        await self.db.conn.commit()

    async def channel(self, user_id: int, login: str = "streamer") -> None:
        await self.db.conn.execute(
            "INSERT INTO tracked_channels(chat_id, twitch_login, added_at) VALUES (?, ?, ?)",
            (user_id, login, NOW))
        await self.db.conn.commit()

    async def grant(self, user_id: int, *, expires: float | None = None,
                    revoked: float | None = None, source: str = "manual") -> None:
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants(grant_id, request_key, subject_kind, subject_id, "
            "plan, source, starts_at, expires_at, revoked_at, issued_by, created_at) "
            "VALUES (?, ?, 'viewer', ?, 'viewer_plus', ?, ?, ?, ?, 1, ?)",
            (f"g-{user_id}", f"k-{user_id}", str(user_id), source,
             NOW - 10, NOW + 3600 if expires is None else expires, revoked, NOW),
        )
        await self.db.conn.commit()

    async def test_empty_database_has_no_invented_numbers(self):
        funnel = await self.db.growth_funnel_report(now=NOW)
        self.assertEqual([step["users"] for step in funnel["steps"]], [0, 0, 0, 0])
        self.assertIsNone(funnel["steps"][0]["share"])
        self.assertEqual(funnel["totals"], {"users": 0, "with_channel": 0, "with_plus": 0})

    async def test_each_step_is_a_subset_of_the_previous_one(self):
        for user_id in PEOPLE:
            await self.attribute(user_id)
        for user_id in PEOPLE[:3]:
            await self.known(user_id)
        for user_id in PEOPLE[:2]:
            await self.channel(user_id, login=f"s{user_id}")
        await self.grant(PEOPLE[0])

        funnel = await self.db.growth_funnel_report(now=NOW)
        self.assertEqual([step["users"] for step in funnel["steps"]], [4, 3, 2, 1])
        self.assertEqual(funnel["steps"][1]["share"], 75.0)
        self.assertEqual(funnel["steps"][2]["share"], 66.7)
        self.assertEqual(funnel["steps"][3]["share"], 50.0)

    async def test_expired_and_revoked_grants_are_not_counted(self):
        for user_id in PEOPLE[:3]:
            await self.attribute(user_id)
        await self.grant(PEOPLE[0], expires=NOW - 1)
        await self.grant(PEOPLE[1], revoked=NOW - 5)
        await self.grant(PEOPLE[2])
        funnel = await self.db.growth_funnel_report(now=NOW)
        self.assertEqual(funnel["steps"][3]["users"], 1)

    async def test_people_without_a_link_are_only_in_the_totals(self):
        await self.attribute(PEOPLE[0])
        await self.known(PEOPLE[1])
        await self.channel(PEOPLE[1], login="no-link")
        funnel = await self.db.growth_funnel_report(now=NOW)
        # Пришёл по ссылке один, но бота он не открывал — воронка честно показывает ноль.
        self.assertEqual(funnel["steps"][0]["users"], 1)
        self.assertEqual(funnel["steps"][1]["users"], 0)
        # А человек без ссылки виден в общей картине и со своим каналом.
        self.assertEqual(funnel["totals"]["users"], 1)
        self.assertEqual(funnel["totals"]["with_channel"], 1)

    async def test_test_grants_count_as_plus_for_the_funnel(self):
        await self.attribute(PEOPLE[0])
        await self.grant(PEOPLE[0], source="test")
        funnel = await self.db.growth_funnel_report(now=NOW)
        self.assertEqual(funnel["steps"][3]["users"], 1)


if __name__ == "__main__":
    unittest.main()
