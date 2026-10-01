"""Named Streamer Plus variants stay private and require explicit application."""

import time
import unittest

from bot.database import Database
from bot.streamer_presets import PresetLimit, PresetNameTaken, StreamerPresetService


class StreamerPresetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = time.time()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=self.now)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=self.now)
        await self.db.add_streamer_community(101, -1001, "Own", "supergroup", now=self.now)
        await self.db.add_streamer_community(202, -2002, "Other", "supergroup", now=self.now)
        self.grant = await self.db.issue_test_streamer_plus(
            "11", "preset-test", starts_at=self.now - 5,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now,
        )
        self.service = StreamerPresetService(self.db)

    async def create(self, name="Обычный"):
        return await self.service.create(
            101, name, headline="<Мой эфир>", body="Смотрим вместе",
            buttons=[{"label": "Сайт", "url": "https://example.com/watch"}],
            now=self.now,
        )

    async def test_saved_variant_is_private_inert_and_versioned_on_apply(self):
        preset = await self.create()
        self.assertEqual(await self.db.get_streamer_template(101, -1001), None)
        self.assertEqual(len(await self.service.list_for_user(101)), 1)
        self.assertEqual(await self.service.list_for_user(202), ())
        with self.assertRaises(PermissionError):
            await self.service.apply(202, preset.id, -1001, expected_version=0,
                                     now=self.now)
        with self.assertRaises(PermissionError):
            await self.service.apply(101, preset.id, -2002, expected_version=0,
                                     now=self.now)
        version = await self.service.apply(101, preset.id, -1001,
                                           expected_version=0, now=self.now)
        self.assertEqual(version, 1)
        saved = await self.db.get_streamer_template(101, -1001)
        self.assertEqual(saved[1], "<Мой эфир>")
        with self.assertRaises(ValueError):
            await self.service.apply(101, preset.id, -1001,
                                     expected_version=0, now=self.now)
        self.assertEqual((await self.db.get_streamer_template(101, -1001))[0], 1)

    async def test_validation_name_collision_cap_and_expiry(self):
        with self.assertRaises(ValueError):
            await self.service.create(101, "Bad", headline="<b>", body="ok",
                                      buttons=[{"label": "bad", "url": "http://localhost/"}],
                                      now=self.now)
        with self.assertRaises(ValueError):
            await self.service.create(101, "\x00", headline="Title", body="",
                                      buttons=[], now=self.now)
        first = await self.create("Первый")
        with self.assertRaises(PresetNameTaken):
            await self.create("первый")
        for index in range(11):
            await self.create(f"Вариант {index}")
        with self.assertRaises(PresetLimit):
            await self.create("Лишний")
        await self.db.revoke_test_streamer_plus(
            self.grant, issued_by=425785231, revoked_at=self.now,
        )
        self.assertEqual(len(await self.service.list_for_user(101)), 12)
        with self.assertRaises(PermissionError):
            await self.service.apply(101, first.id, -1001,
                                     expected_version=0, now=self.now)
        with self.assertRaises(PermissionError):
            await self.create("После окончания")
        self.assertTrue(await self.service.delete(101, first.id))
        self.assertFalse(await self.service.delete(202, first.id))

    async def test_period_comparison_counts_only_confirmed_own_posts(self):
        await self.db.conn.executemany(
            "INSERT INTO streamer_post_events "
            "(broadcaster_id,chat_id,twitch_login,stream_id,message_id,published_at) "
            "VALUES (?,?,?,?,?,?)",
            [("11", -1001, "alpha", "s1", 1, self.now - 86400),
             ("11", -1001, "alpha", "s2", 2, self.now - 8 * 86400),
             ("22", -2002, "beta", "s3", 3, self.now - 86400),
             ("11", -1001, "alpha", "future", 4, self.now + 100)],
        )
        await self.db.conn.commit()
        self.assertEqual(await self.service.compare_posts(101, now=self.now),
                         {"period_days": 7, "current_posts": 1, "previous_posts": 1})
        with self.assertRaises(PermissionError):
            await self.service.compare_posts(202, now=self.now)


if __name__ == "__main__":
    unittest.main()
