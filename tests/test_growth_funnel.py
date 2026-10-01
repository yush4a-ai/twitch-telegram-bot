import unittest
from pathlib import Path

from bot.database import Database


class GrowthFunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def _grant(self, user_id, number, *, kind="viewer", source="test", created=400, revoked=None):
        subject_id = str(user_id) if kind == "viewer" else f"b{user_id}"
        plan = "viewer_plus" if kind == "viewer" else "streamer_plus"
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants "
            "(grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,revoked_at,issued_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"g{number}", f"r{number}", kind, subject_id, plan, source,
             created, created + 1, revoked, 999, created),
        )
        await self.db.conn.commit()

    async def test_fixed_empty_buckets_contain_no_identifiers(self):
        self.assertEqual(await self.db.growth_funnel_snapshot(), [
            {"source": "site", "touched": 0, "activated": 0, "ever_test_plus": 0},
            {"source": "referral", "touched": 0, "activated": 0, "ever_test_plus": 0},
        ])

    async def test_conversion_counts_unique_activated_people_after_activation(self):
        self.assertTrue(await self.db.record_growth_touch(101, "src_site", now=100))
        self.assertTrue(await self.db.record_growth_touch(102, "src_site", now=100))
        self.assertTrue(await self.db.record_growth_touch(103, "src_site", now=100))
        code = await self.db.get_or_create_growth_referral_code(900, now=100)
        self.assertTrue(await self.db.record_growth_touch(201, f"ref_{code}", now=100))
        await self.db.conn.execute(
            "UPDATE growth_attributions SET activated_at=200 "
            "WHERE telegram_user_id IN (101,102,201)"
        )
        await self.db.conn.execute(
            "INSERT INTO streamer_identities "
            "(broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('b201',201,'streamer',150)"
        )
        await self.db.conn.commit()
        await self._grant(101, 1, created=250, revoked=260)
        await self._grant(101, 2, created=300)
        await self._grant(102, 3, created=150)
        await self._grant(102, 4, created=250, source="mock_payment")
        await self._grant(103, 5, created=250)
        await self._grant(201, 6, kind="streamer", created=250, revoked=300)
        rows = await self.db.growth_funnel_snapshot()
        self.assertEqual(rows, [
            {"source": "site", "touched": 3, "activated": 2, "ever_test_plus": 1},
            {"source": "referral", "touched": 1, "activated": 1, "ever_test_plus": 1},
        ])
        self.assertNotIn("101", str(rows))
        self.assertNotIn(code, str(rows))

    async def test_streamer_conversion_needs_current_verified_identity(self):
        self.assertTrue(await self.db.record_growth_touch(301, "src_site", now=100))
        await self.db.conn.execute(
            "UPDATE growth_attributions SET activated_at=200 WHERE telegram_user_id=301"
        )
        await self.db.conn.execute(
            "INSERT INTO streamer_identities "
            "(broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('b301',302,'other',150)"
        )
        await self.db.conn.commit()
        await self._grant(301, 7, kind="streamer", created=250)
        self.assertEqual((await self.db.growth_funnel_snapshot())[0]["ever_test_plus"], 0)


class GrowthAdminMarkupTests(unittest.TestCase):
    def test_owner_card_uses_text_only_rendering(self):
        root = Path(__file__).resolve().parents[1] / "bot" / "admin_ui"
        html = (root / "index.html").read_text(encoding="utf-8")
        js = (root / "panel.js").read_text(encoding="utf-8")
        self.assertIn('id="growth-list"', html)
        self.assertIn("renderGrowth(data.growth)", js)
        self.assertNotIn("innerHTML", js)


if __name__ == "__main__":
    unittest.main()
