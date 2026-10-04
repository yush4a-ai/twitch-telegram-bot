import tempfile
import time
import unittest
from pathlib import Path

from bot.admin_directory import AdminDirectory
from bot.database import Database
from bot.plan_catalog import (
    FREE_VIEWER_CHANNEL_LIMIT,
    VIEWER_PLUS_CHANNEL_LIMIT,
    VIEWER_PLUS_VIDEO_SLOTS,
)

# Реальное «сейчас»: часть проверок внутри Database использует собственные часы.
NOW = time.time()
DAY = 86_400.0


class AdminDirectoryTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backup_dir = Path(self.tmp.name) / "backups"
        self.directory = AdminDirectory(self.db, backup_dir=self.backup_dir, retention=5)

    async def grant(
        self, grant_id, *, subject_kind="viewer", subject_id="111", plan="viewer_plus",
        source="test", starts_at=NOW - DAY, expires_at=NOW + DAY, revoked_at=None,
        issued_by=42, beneficiary=None,
    ):
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants (grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,revoked_at,issued_by,created_at,beneficiary_telegram_user_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (grant_id, f"key-{grant_id}", subject_kind, subject_id, plan, source,
             starts_at, expires_at, revoked_at, issued_by, starts_at, beneficiary),
        )
        await self.db.conn.commit()
        return grant_id

    async def event(self, grant_id, action, *, actor=42, happened_at=NOW):
        await self.db.conn.execute(
            "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at) "
            "VALUES (?,?,?,?)",
            (grant_id, action, actor, happened_at),
        )
        await self.db.conn.commit()


class AdminDirectoryAccessTests(AdminDirectoryTestCase):
    async def test_active_grants_exclude_revoked_and_expired(self):
        await self.grant("active", subject_id="111")
        await self.grant("revoked", subject_id="222", revoked_at=NOW - 10)
        await self.grant("expired", subject_id="333", starts_at=NOW - 2 * DAY, expires_at=NOW - 10)

        overview = await self.directory.access_overview(NOW)

        self.assertEqual(overview["active_total"], 1)

    async def test_streamer_grant_counts_viewer_once(self):
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('bc1',555,'alpha',?)", (NOW,),
        )
        await self.db.conn.commit()
        await self.db.issue_test_streamer_plus(
            "bc1", "streamer-key", starts_at=NOW - DAY, expires_at=NOW + DAY,
            issued_by=42, now=NOW, beneficiary_telegram_user_id=555,
        )

        overview = await self.directory.access_overview(NOW)

        self.assertEqual(overview["streamer"], 1)
        self.assertEqual(overview["viewer"], 1)
        self.assertEqual(overview["active_total"], 1)

    async def test_by_source_splits_test_paid_mock(self):
        await self.grant("t", subject_id="111", source="test")
        await self.grant("p", subject_id="222", source="paid")
        await self.grant("m", subject_id="333", source="mock")

        overview = await self.directory.access_overview(NOW)

        self.assertEqual(overview["by_source"], {"mock": 1, "paid": 1, "test": 1})

    async def test_expiring_7d_counts_only_future_within_window(self):
        await self.grant("soon", subject_id="111", expires_at=NOW + 3 * DAY)
        await self.grant("late", subject_id="222", expires_at=NOW + 10 * DAY)
        await self.grant("revoked", subject_id="333", expires_at=NOW + DAY, revoked_at=NOW - 1)

        overview = await self.directory.access_overview(NOW)

        self.assertEqual(overview["expiring_7d"], 1)

    async def test_history_returns_events_newest_first(self):
        await self.grant("g1", subject_id="111")
        await self.event("g1", "grant", happened_at=NOW)
        await self.event("g1", "revoke", happened_at=NOW + 10)

        rows = await self.directory.history(limit=10, offset=0)

        self.assertEqual([row["action"] for row in rows], ["revoke", "grant"])
        self.assertEqual(rows[0]["plan"], "viewer_plus")
        self.assertEqual(rows[0]["actor_telegram_id"], 42)

    async def test_active_grants_sorted_by_expiry_and_paged(self):
        await self.grant("late", subject_id="111", expires_at=NOW + 5 * DAY)
        await self.grant("soon", subject_id="222", expires_at=NOW + DAY)
        await self.grant("mid", subject_id="333", expires_at=NOW + 3 * DAY)

        rows = await self.directory.active_grants(NOW, limit=2, offset=0)

        self.assertEqual([row["grant_id"] for row in rows], ["soon", "mid"])
        self.assertEqual(rows[0]["source"], "test")

    async def test_empty_database_returns_zeroes_and_empty_lists(self):
        overview = await self.directory.access_overview(NOW)

        self.assertEqual(overview["active_total"], 0)
        self.assertEqual(overview["by_source"], {})
        self.assertEqual(await self.directory.active_grants(NOW, limit=10, offset=0), [])
        self.assertEqual(await self.directory.history(limit=10, offset=0), [])


class AdminDirectoryOperationsTests(AdminDirectoryTestCase):
    async def test_backup_status_without_directory_is_honest(self):
        status = await self.directory.backup_status()

        self.assertIsNone(status["last_backup_at"])
        self.assertIsNone(status["last_backup_name"])
        self.assertFalse(status["restore_verified"])
        self.assertEqual(status["retention"], 5)

    async def test_backup_status_reports_newest_copy(self):
        self.backup_dir.mkdir(parents=True)
        older = self.backup_dir / "auto-20261003T000000Z.db"
        newer = self.backup_dir / "auto-20261004T000000Z.db"
        older.write_bytes(b"x")
        newer.write_bytes(b"x")

        status = await self.directory.backup_status()

        self.assertEqual(status["last_backup_name"], newer.name)
        self.assertIsNotNone(status["last_backup_at"])
        self.assertFalse(status["restore_verified"])

    async def test_deliveries_24h_counts_done_notifications_and_reports(self):
        await self.db.conn.executemany(
            "INSERT INTO notification_jobs (kind,chat_id,twitch_login,logical_stream_id,payload_version,"
            "due_at,status,attempt_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("go_live", 1, "alpha", "s1", 1, NOW - 10, "done", 1, NOW - 100, NOW - 100),
                ("go_live", 1, "beta", "s2", 1, NOW - 10, "failed", 1, NOW - 100, NOW - 100),
            ],
        )
        await self.db.conn.executemany(
            "INSERT INTO report_deliveries (source_chat_id,twitch_login,stream_id,recipient_chat_id,"
            "report_format,text_payload,text_sent,html_sent,terminal_failed,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, "alpha", "s1", 1, "brief", "t", 1, 0, 0, NOW - 100, NOW - 100),
                (1, "beta", "s2", 1, "brief", "t", 0, 0, 1, NOW - 100, NOW - 100),
            ],
        )
        await self.db.conn.commit()

        deliveries = await self.directory.deliveries_24h(NOW)

        self.assertEqual(deliveries["notifications"], 1)
        self.assertEqual(deliveries["reports"], 1)
        self.assertEqual(deliveries["total"], 2)

    async def test_deliveries_24h_excludes_older_than_window(self):
        await self.db.conn.execute(
            "INSERT INTO notification_jobs (kind,chat_id,twitch_login,logical_stream_id,payload_version,"
            "due_at,status,attempt_count,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("go_live", 1, "alpha", "s1", 1, NOW - 10, "done", 1, NOW - 3 * DAY, NOW - 2 * DAY),
        )
        await self.db.conn.commit()

        deliveries = await self.directory.deliveries_24h(NOW)

        self.assertEqual(deliveries["total"], 0)

    async def test_channel_usage_respects_viewer_plus_limit(self):
        await self.db.conn.executemany(
            "INSERT INTO tracked_channels (chat_id,twitch_login,added_at) VALUES (?,?,?)",
            [(111, "alpha", 1), (111, "beta", 2), (111, "gamma", 3)],
        )
        await self.db.conn.commit()

        free = await self.directory.channel_usage(111)
        self.assertEqual(free, {"used": 3, "limit": FREE_VIEWER_CHANNEL_LIMIT})

        await self.grant("plus", subject_kind="viewer", subject_id="111")

        plus = await self.directory.channel_usage(111)
        self.assertEqual(plus, {"used": 3, "limit": VIEWER_PLUS_CHANNEL_LIMIT})

    async def test_video_usage_uses_plus_limit(self):
        await self.db.conn.executemany(
            "INSERT INTO viewer_video_selections (telegram_user_id,broadcaster_id,twitch_login,position) "
            "VALUES (?,?,?,?)",
            [(111, "bc1", "alpha", 1), (111, "bc2", "beta", 2)],
        )
        await self.db.conn.commit()

        usage = await self.directory.video_usage(111)

        self.assertEqual(usage, {"used": 2, "limit": VIEWER_PLUS_VIDEO_SLOTS})


if __name__ == "__main__":
    unittest.main()
