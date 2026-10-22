"""Стример может отвязать Twitch и подключить другой канал.

Раньше отвязки не было: `link_streamer_identity` отказывал, если у человека уже
есть привязка, а убрать её было нечем. Если канал заблокировали, человек
навсегда оставался с недоступной привязкой. Теперь привязку можно снять, а
оплаченный Streamer Plus переезжает на новый канал того же человека.
"""
import time
import unittest

from bot.database import Database


class StreamerUnlinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    async def test_unlink_removes_the_binding(self):
        await self.db.link_streamer_identity(101, "111", "first_channel", verified_at=100)

        removed = await self.db.unlink_streamer_identity(101, now=200)

        self.assertTrue(removed)
        self.assertIsNone(await self.db.get_streamer_identity(101))

    async def test_unlink_without_a_binding_is_a_calm_refusal(self):
        self.assertFalse(await self.db.unlink_streamer_identity(101, now=200))

    async def test_after_unlink_a_different_channel_can_be_linked(self):
        await self.db.link_streamer_identity(101, "111", "first_channel", verified_at=100)
        # Пока привязка есть, второй канал привязать нельзя.
        self.assertFalse(
            await self.db.link_streamer_identity(101, "222", "second_channel", verified_at=150)
        )
        await self.db.unlink_streamer_identity(101, now=200)

        linked = await self.db.link_streamer_identity(101, "222", "second_channel", verified_at=210)

        self.assertTrue(linked)
        self.assertEqual(await self.db.get_streamer_identity(101), ("222", "second_channel"))

    async def test_a_paid_streamer_plus_moves_to_the_new_channel(self):
        now = time.time()
        await self.db.link_streamer_identity(101, "111", "first_channel", verified_at=now - 100)
        grant = await self.db.issue_test_streamer_plus(
            "111", "paid-first", starts_at=now - 50, expires_at=now + 30 * 86400,
            issued_by=999, now=now - 100, beneficiary_telegram_user_id=101,
        )
        await self.db.unlink_streamer_identity(101, now=now)

        await self.db.link_streamer_identity(101, "222", "second_channel", verified_at=now + 5)

        moved = await (await self.db.conn.execute(
            "SELECT subject_id, starts_at, expires_at FROM entitlement_grants WHERE grant_id=?",
            (grant,),
        )).fetchone()
        self.assertEqual(moved, ("222", now - 50, now + 30 * 86400))
        self.assertTrue(await self.db.has_streamer_plus(101, now=now + 10))

    async def test_a_grant_of_another_person_is_not_touched(self):
        now = time.time()
        await self.db.link_streamer_identity(202, "333", "other_channel", verified_at=now)
        grant = await self.db.issue_test_streamer_plus(
            "333", "other-paid", starts_at=now - 10, expires_at=now + 86400,
            issued_by=999, now=now, beneficiary_telegram_user_id=202,
        )
        await self.db.unlink_streamer_identity(202, now=now)

        await self.db.link_streamer_identity(101, "222", "second_channel", verified_at=now + 5)

        untouched = await (await self.db.conn.execute(
            "SELECT subject_id FROM entitlement_grants WHERE grant_id=?", (grant,),
        )).fetchone()
        self.assertEqual(untouched, ("333",))

    async def test_an_expired_grant_does_not_follow_the_person(self):
        now = time.time()
        await self.db.link_streamer_identity(101, "111", "first_channel", verified_at=now - 100)
        grant = await self.db.issue_test_streamer_plus(
            "111", "old-paid", starts_at=now - 90 * 86400, expires_at=now - 60 * 86400,
            issued_by=999, now=now - 100, beneficiary_telegram_user_id=101,
        )
        await self.db.unlink_streamer_identity(101, now=now)

        await self.db.link_streamer_identity(101, "222", "second_channel", verified_at=now + 5)

        expired = await (await self.db.conn.execute(
            "SELECT subject_id FROM entitlement_grants WHERE grant_id=?", (grant,),
        )).fetchone()
        self.assertEqual(expired, ("111",))


if __name__ == "__main__":
    unittest.main()
