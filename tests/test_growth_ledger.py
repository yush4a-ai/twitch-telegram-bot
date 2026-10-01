import unittest
from unittest.mock import patch

from bot.database import Database


class GrowthLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_referral_codes_are_opaque_stable_and_collision_safe(self):
        with patch("bot.database.secrets.token_urlsafe", side_effect=[
            "AbC012_-xyz9", "AbC012_-xyz9", "New012_-xyz9",
        ]):
            first = await self.db.get_or_create_growth_referral_code(101, now=100)
            second = await self.db.get_or_create_growth_referral_code(202, now=101)
        self.assertEqual(first, "AbC012_-xyz9")
        self.assertEqual(second, "New012_-xyz9")
        self.assertEqual(await self.db.get_or_create_growth_referral_code(101, now=102), first)
        self.assertNotIn("101", first)
        with self.assertRaises(ValueError):
            await self.db.get_or_create_growth_referral_code(-1)

    async def test_first_valid_touch_only_and_no_self_or_existing_user(self):
        code = await self.db.get_or_create_growth_referral_code(101, now=100)
        self.assertFalse(await self.db.record_growth_touch(101, "ref_" + code, now=110))
        self.assertFalse(await self.db.record_growth_touch(202, "ref_" + "Z" * 12, now=110))
        self.assertFalse(await self.db.record_growth_touch(202, "src_unknown", now=110))
        self.assertTrue(await self.db.record_growth_touch(202, "ref_" + code, now=111))
        self.assertFalse(await self.db.record_growth_touch(202, "src_site", now=112))
        self.assertFalse(await self.db.record_growth_touch(202, "ref_" + code, now=113))
        await self.db.add_channel(303, "alpha")
        self.assertFalse(await self.db.record_growth_touch(303, "src_site", now=114))
        self.assertTrue(await self.db.record_growth_touch(404, "src_site", now=115))
        rows = await (await self.db.conn.execute(
            "SELECT telegram_user_id,source_kind,source_code,referrer_user_id "
            "FROM growth_attributions ORDER BY telegram_user_id"
        )).fetchall()
        self.assertEqual(rows, [
            (202, "referral", code, 101), (404, "site", "site", None),
        ])
        with self.assertRaises(ValueError):
            await self.db.record_growth_touch(0, "src_site")

    async def test_growth_migration_has_one_version(self):
        versions = await (await self.db.conn.execute(
            "SELECT version FROM schema_migrations WHERE version='r8_001_growth_attribution'"
        )).fetchall()
        self.assertEqual(versions, [("r8_001_growth_attribution",)])
        self.assertEqual((await (await self.db.conn.execute("PRAGMA integrity_check")).fetchone())[0], "ok")


if __name__ == "__main__":
    unittest.main()
