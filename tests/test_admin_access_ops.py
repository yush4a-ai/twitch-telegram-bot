import os
import tempfile
import time
import unittest

from bot.database import AccessConflict, AccessDenied, Database

DAY = 86_400.0


class ManualAccessOpsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = time.time()
        self.actor = 425785231

    async def link_streamer(self, user_id=111, broadcaster_id="bc1", login="alex_stream"):
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES (?,?,?,?)", (broadcaster_id, user_id, login, self.now),
        )
        await self.db.conn.commit()

    async def grant(self, user_id=111, plan="viewer_plus", **overrides):
        payload = {
            "expires_at": self.now + 30 * DAY,
            "reason": "testing",
            "reason_note": None,
            "comment": None,
            "issued_by": self.actor,
            "request_key": overrides.pop("request_key", f"key-{user_id}-{plan}"),
            "now": self.now,
        }
        payload.update(overrides)
        return await self.db.grant_manual_access(user_id, plan, **payload)

    async def events(self):
        cursor = await self.db.conn.execute(
            "SELECT action,reason,grant_id,previous_grant_id,previous_expires_at,new_expires_at,"
            "request_key FROM entitlement_events ORDER BY id"
        )
        return await cursor.fetchall()

    async def test_grant_writes_manual_source_and_journal_reason(self):
        result = await self.grant(comment="выдали вручную")

        cursor = await self.db.conn.execute(
            "SELECT subject_kind,subject_id,source,plan,expires_at,beneficiary_telegram_user_id "
            "FROM entitlement_grants WHERE grant_id=?", (result["grant_id"],),
        )
        row = await cursor.fetchone()
        self.assertEqual(row[0], "viewer")
        self.assertEqual(row[1], "111")
        self.assertEqual(row[2], "manual")
        self.assertEqual(row[4], self.now + 30 * DAY)
        self.assertEqual(row[5], 111)

        events = await self.events()
        self.assertEqual(events[0][0], "grant")
        self.assertEqual(events[0][1], "testing")
        self.assertEqual(events[0][6], result["request_key"])

    async def test_grant_is_idempotent_by_request_key(self):
        first = await self.grant(request_key="same-key")
        second = await self.grant(request_key="same-key")

        self.assertEqual(first["grant_id"], second["grant_id"])
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_grant_refuses_same_key_with_other_terms(self):
        await self.grant(request_key="same-key")
        with self.assertRaises(AccessConflict):
            await self.grant(request_key="same-key", expires_at=self.now + 5 * DAY)

    async def test_grant_refuses_past_or_too_far_expiry(self):
        with self.assertRaises(ValueError):
            await self.grant(request_key="past", expires_at=self.now - DAY)
        with self.assertRaises(ValueError):
            await self.grant(request_key="far", expires_at=self.now + 400 * DAY)

    async def test_grant_refuses_unknown_reason(self):
        with self.assertRaises(ValueError):
            await self.grant(request_key="bad-reason", reason="because")

    async def test_grant_refuses_streamer_without_linked_twitch(self):
        with self.assertRaises(AccessDenied) as denied:
            await self.grant(plan="streamer_plus", request_key="no-twitch")
        # Панель показывает причину владельцу, а не безликое «запрещено».
        self.assertEqual(denied.exception.code, "twitch_required")

    async def test_streamer_grant_uses_linked_broadcaster(self):
        await self.link_streamer()

        result = await self.grant(plan="streamer_plus", request_key="streamer")

        cursor = await self.db.conn.execute(
            "SELECT subject_kind,subject_id,beneficiary_telegram_user_id FROM entitlement_grants "
            "WHERE grant_id=?", (result["grant_id"],),
        )
        self.assertEqual(await cursor.fetchone(), ("streamer", "bc1", 111))

    async def test_extend_creates_new_grant_and_logs_previous(self):
        first = await self.grant()
        new_expiry = self.now + 60 * DAY

        extended = await self.db.extend_manual_access(
            first["grant_id"], expected_expires_at=self.now + 30 * DAY,
            expires_at=new_expiry, reason="partnership", reason_note=None, comment="продлили",
            issued_by=self.actor, request_key="extend-1", now=self.now,
        )

        self.assertNotEqual(extended["grant_id"], first["grant_id"])
        cursor = await self.db.conn.execute(
            "SELECT revoked_at FROM entitlement_grants WHERE grant_id=?", (first["grant_id"],),
        )
        self.assertEqual((await cursor.fetchone())[0], self.now)

        events = await self.events()
        self.assertEqual([row[0] for row in events], ["grant", "extend"])
        self.assertEqual(events[1][3], first["grant_id"])
        self.assertEqual(events[1][4], self.now + 30 * DAY)
        self.assertEqual(events[1][5], new_expiry)
        self.assertEqual(events[1][1], "partnership")

    async def test_extend_refuses_shorter_expiry(self):
        first = await self.grant()
        with self.assertRaises(ValueError):
            await self.db.extend_manual_access(
                first["grant_id"], expected_expires_at=self.now + 30 * DAY,
                expires_at=self.now + 10 * DAY, reason="testing", reason_note=None,
                comment=None, issued_by=self.actor, request_key="extend-short", now=self.now,
            )

    async def test_extend_conflict_on_stale_expiry(self):
        first = await self.grant()
        with self.assertRaises(AccessConflict):
            await self.db.extend_manual_access(
                first["grant_id"], expected_expires_at=self.now + 5 * DAY,
                expires_at=self.now + 60 * DAY, reason="testing", reason_note=None,
                comment=None, issued_by=self.actor, request_key="extend-stale", now=self.now,
            )

    async def test_extend_is_idempotent_by_request_key(self):
        first = await self.grant()
        kwargs = {
            "expected_expires_at": self.now + 30 * DAY,
            "expires_at": self.now + 60 * DAY,
            "reason": "testing", "reason_note": None, "comment": None,
            "issued_by": self.actor, "now": self.now,
        }
        one = await self.db.extend_manual_access(first["grant_id"], request_key="extend-same", **kwargs)
        two = await self.db.extend_manual_access(first["grant_id"], request_key="extend-same", **kwargs)

        self.assertEqual(one["grant_id"], two["grant_id"])
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants WHERE source='manual'")
        self.assertEqual((await cursor.fetchone())[0], 2)

    async def test_failed_journal_write_rolls_back_grant(self):
        original = self.db.conn.execute

        async def failing(sql, *args, **kwargs):
            if "INSERT INTO entitlement_events" in sql:
                raise RuntimeError("journal unavailable")
            return await original(sql, *args, **kwargs)

        self.db.conn.execute = failing
        try:
            with self.assertRaises(RuntimeError):
                await self.grant(request_key="atomic-grant")
        finally:
            self.db.conn.execute = original

        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_failed_journal_write_rolls_back_extend(self):
        first = await self.grant()
        original = self.db.conn.execute

        async def failing(sql, *args, **kwargs):
            if "INSERT INTO entitlement_events" in sql and "'extend'" in sql:
                raise RuntimeError("journal unavailable")
            return await original(sql, *args, **kwargs)

        self.db.conn.execute = failing
        try:
            with self.assertRaises(RuntimeError):
                await self.db.extend_manual_access(
                    first["grant_id"], expected_expires_at=self.now + 30 * DAY,
                    expires_at=self.now + 60 * DAY, reason="testing", reason_note=None,
                    comment=None, issued_by=self.actor, request_key="atomic-extend",
                    now=self.now,
                )
        finally:
            self.db.conn.execute = original

        cursor = await self.db.conn.execute(
            "SELECT revoked_at FROM entitlement_grants WHERE grant_id=?", (first["grant_id"],)
        )
        self.assertIsNone((await cursor.fetchone())[0])
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 1)

    async def test_failed_journal_write_rolls_back_revoke(self):
        first = await self.grant()
        original = self.db.conn.execute

        async def failing(sql, *args, **kwargs):
            if "INSERT INTO entitlement_events" in sql and "'revoke'" in sql:
                raise RuntimeError("journal unavailable")
            return await original(sql, *args, **kwargs)

        self.db.conn.execute = failing
        try:
            with self.assertRaises(RuntimeError):
                await self.db.revoke_manual_access(
                    first["grant_id"], expected_expires_at=self.now + 30 * DAY,
                    reason="testing", reason_note=None, comment=None,
                    issued_by=self.actor, request_key="atomic-revoke", now=self.now,
                )
        finally:
            self.db.conn.execute = original

        cursor = await self.db.conn.execute(
            "SELECT revoked_at FROM entitlement_grants WHERE grant_id=?", (first["grant_id"],)
        )
        self.assertIsNone((await cursor.fetchone())[0])

    async def test_grant_key_used_by_another_person_is_a_conflict(self):
        await self.grant(request_key="shared-key")

        with self.assertRaises(AccessConflict):
            await self.db.grant_manual_access(
                222, "viewer_plus", expires_at=self.now + 30 * DAY, reason="testing",
                reason_note=None, comment=None, issued_by=self.actor,
                request_key="shared-key", now=self.now,
            )

    async def test_revoke_repeat_keeps_plan_and_start(self):
        first = await self.grant()
        kwargs = {
            "expected_expires_at": self.now + 30 * DAY, "reason": "testing",
            "reason_note": None, "comment": None, "issued_by": self.actor, "now": self.now,
        }
        one = await self.db.revoke_manual_access(first["grant_id"], request_key="revoke-plan", **kwargs)
        two = await self.db.revoke_manual_access(first["grant_id"], request_key="revoke-plan", **kwargs)

        self.assertEqual(two["plan"], one["plan"])
        self.assertEqual(two["starts_at"], one["starts_at"])

    async def test_revoke_marks_manual_grant_and_logs_reason(self):
        first = await self.grant()

        result = await self.db.revoke_manual_access(
            first["grant_id"], expected_expires_at=self.now + 30 * DAY, reason="compensation",
            reason_note=None, comment="ошибочная выдача", issued_by=self.actor,
            request_key="revoke-1", now=self.now,
        )

        self.assertEqual(result["action"], "revoke")
        cursor = await self.db.conn.execute(
            "SELECT revoked_at FROM entitlement_grants WHERE grant_id=?", (first["grant_id"],),
        )
        self.assertEqual((await cursor.fetchone())[0], self.now)
        events = await self.events()
        self.assertEqual(events[-1][0], "revoke")
        self.assertEqual(events[-1][1], "compensation")

    async def test_revoke_is_idempotent_by_request_key(self):
        first = await self.grant()
        kwargs = {
            "expected_expires_at": self.now + 30 * DAY, "reason": "testing",
            "reason_note": None, "comment": None, "issued_by": self.actor, "now": self.now,
        }
        one = await self.db.revoke_manual_access(first["grant_id"], request_key="revoke-same", **kwargs)
        two = await self.db.revoke_manual_access(first["grant_id"], request_key="revoke-same", **kwargs)

        self.assertEqual(one["grant_id"], two["grant_id"])
        events = await self.events()
        self.assertEqual([row[0] for row in events], ["grant", "revoke"])

    async def test_revoke_refuses_paid_grant(self):
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants (grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,issued_by,created_at,beneficiary_telegram_user_id) "
            "VALUES ('paid1','paid-key','viewer','111','viewer_plus','paid',?,?,42,?,111)",
            (self.now - DAY, self.now + 30 * DAY, self.now),
        )
        await self.db.conn.commit()

        with self.assertRaises(AccessDenied):
            await self.db.revoke_manual_access(
                "paid1", expected_expires_at=self.now + 30 * DAY, reason="testing",
                reason_note=None, comment=None, issued_by=self.actor,
                request_key="revoke-paid", now=self.now,
            )

        cursor = await self.db.conn.execute(
            "SELECT revoked_at FROM entitlement_grants WHERE grant_id='paid1'"
        )
        self.assertIsNone((await cursor.fetchone())[0])


if __name__ == "__main__":
    unittest.main()
