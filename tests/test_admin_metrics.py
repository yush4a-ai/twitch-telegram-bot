import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.admin_metrics import AdminSnapshot
from bot.database import Database
from bot.preview_runtime import PreviewManager


class AdminLiveQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_rows_deduplicate_destinations_and_use_latest_sample(self):
        db = Database(":memory:")
        await db.connect()
        self.addAsyncCleanup(db.close)
        await db.conn.executemany(
            "INSERT INTO tracked_channels (chat_id, twitch_login, is_live, last_seen_live_at, last_stream_id) VALUES (?, ?, ?, ?, ?)",
            [(1, "alpha", 1, 100.0, "s1"), (2, "alpha", 1, 101.0, "s1"), (3, "beta", 0, 102.0, "s2")],
        )
        await db.conn.executemany(
            "INSERT INTO stream_samples (chat_id, twitch_login, stream_id, sampled_at, viewer_count, title, game_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(1, "alpha", "s1", 99.0, 20, "Title", "Game"), (2, "alpha", "s1", 101.0, 42, "Title", "Game")],
        )
        await db.conn.commit()
        rows = await db.get_admin_live_streams()
        self.assertEqual(rows, [{"login": "alpha", "destinations": 2, "viewers": 42, "observed_at": 101.0}])

    async def test_empty_live_query_is_empty(self):
        db = Database(":memory:")
        await db.connect()
        self.addAsyncCleanup(db.close)
        self.assertEqual(await db.get_admin_live_streams(), [])

    async def test_live_query_uses_chat_scoped_sample_index(self):
        db = Database(":memory:")
        await db.connect()
        self.addAsyncCleanup(db.close)
        await db.conn.execute(
            "INSERT INTO tracked_channels (chat_id, twitch_login, is_live, last_stream_id) VALUES (1, 'alpha', 1, 's1')"
        )
        statements = []
        await db.conn.set_trace_callback(statements.append)
        await db.get_admin_live_streams()
        await db.conn.set_trace_callback(None)
        actual_sql = next(sql for sql in statements if "FROM tracked_channels tc" in sql)
        cursor = await db.conn.execute("EXPLAIN QUERY PLAN " + actual_sql)
        details = " ".join(str(row[3]) for row in await cursor.fetchall())
        self.assertIn("idx_stream_samples_lookup", details)
        self.assertNotIn("SCAN ss USING INDEX idx_stream_samples_retention", details)


class AdminSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = Mock()
        self.db.get_bot_stats = AsyncMock(return_value={"private_users": 3, "groups": 2, "tracked_channels": 4, "unique_twitch_channels": 2, "live_now": 2})
        self.db.get_admin_live_streams = AsyncMock(return_value=[{"login": "alpha", "destinations": 2, "viewers": 42, "observed_at": 101.0}])
        self.db.health_snapshot = AsyncMock(return_value={"pending_deliveries": 1, "oldest_pending_age_seconds": 12.0, "deferred_reports": 0, "oldest_deferred_age_seconds": None, "db_file_bytes": 8192, "wal_file_bytes": 0})
        self.db.growth_funnel_snapshot = AsyncMock(return_value=[
            {"source": "site", "touched": 0, "activated": 0, "ever_test_plus": 0},
            {"source": "referral", "touched": 0, "activated": 0, "ever_test_plus": 0},
        ])
        self.db.growth_funnel_report = AsyncMock(return_value={
            "steps": [
                {"key": "attributed", "title": "Пришли", "users": 2, "share": 2.0},
                {"key": "opened", "title": "Открыли бота", "users": 1, "share": 50.0},
            ],
            "totals": {"users": 3, "with_channel": 1, "with_plus": 0},
        })
        self.db.count_active_since = AsyncMock(return_value=7)
        self.db.count_first_seen_since = AsyncMock(return_value=3)
        self.db.activity_by_day = AsyncMock(return_value=[{"date": 0.0, "users": 2}])
        self.poller = Mock(health_snapshot=Mock(return_value={"running": True, "stopping": False, "last_successful_cycle_age_seconds": 2.0, "stale_after_seconds": 180.0, "last_cycle_duration_seconds": 0.8, "last_cycle_error": None}))
        self.eventsub = Mock(health_snapshot=Mock(return_value={"running": True, "configured_logins": 1, "ready_logins": 1, "last_error": None}))
        self.tokens = Mock(health_snapshot=Mock(return_value={"auth_blocked_logins": 0}))
        self.preview = Mock(health_snapshot=Mock(return_value={"enabled": True, "manager_running": True, "active_sessions": 1, "active_jobs": 0, "last_success_age_seconds": 10.0, "latest_observation_age_seconds": 3.0, "last_error": None, "disabled_reason": None}))

    def build(self, *, polling=True, preview=True, directory=None):
        return AdminSnapshot(
            self.db, self.poller, self.eventsub, self.tokens,
            self.preview if preview else None,
            db_path=":memory:", telegram_polling_provider=lambda: polling,
            environment="staging", directory=directory,
        )

    async def test_separate_states_and_unknown_delivery(self):
        result = await self.build().collect()
        self.assertEqual(result["telegram"]["state"], "ok")
        self.assertEqual(result["telegram"]["delivery_verified"], "unknown")
        self.assertEqual(result["twitch"]["state"], "ok")
        self.assertEqual(result["preview"]["state"], "ok")
        self.assertEqual(result["preview"]["last_success_age_seconds"], 10.0)
        self.assertEqual(result["audience"]["private_users"], 3)
        self.assertEqual(result["live"][0]["login"], "alpha")
        self.assertEqual(result["queues"]["pending_deliveries"], 1)
        self.assertNotIn("db_path", str(result))
        self.assertIsNone(result["resources"]["db_volume_free_bytes"])

    async def test_degraded_twitch_names_channels_and_logins(self):
        self.eventsub.health_snapshot.return_value.update(
            {"configured_logins": 6, "ready_logins": 5, "last_error": "TwitchAuthError"})
        self.tokens.health_snapshot.return_value = {"auth_blocked_logins": 1}
        self.tokens.blocked_logins = Mock(return_value=["dobriy_yura"])

        result = await self.build().collect()

        self.assertEqual(result["twitch"]["state"], "degraded")
        self.assertEqual(result["twitch"]["auth_blocked_names"], ["dobriy_yura"])
        detail = next(item["detail"] for item in result["attention"] if item["kind"] == "eventsub")
        self.assertIn("Подписано 5 из 6 каналов", detail)
        self.assertIn("dobriy_yura", detail)

    async def test_degraded_and_disabled_states(self):
        self.poller.health_snapshot.return_value["last_successful_cycle_age_seconds"] = 300.0
        self.eventsub.health_snapshot.return_value["last_error"] = "NetworkError"
        self.preview.health_snapshot.return_value["enabled"] = False
        result = await self.build(polling=False).collect()
        self.assertEqual(result["telegram"]["state"], "degraded")
        self.assertEqual(result["twitch"]["state"], "degraded")
        self.assertEqual(result["preview"]["state"], "disabled")
        self.assertEqual(result["errors"]["eventsub"], "NetworkError")

    async def test_unsafe_error_text_is_not_exposed(self):
        self.eventsub.health_snapshot.return_value["last_error"] = "secret /data/bot.db"
        result = await self.build().collect()
        self.assertIsNone(result["errors"]["eventsub"])
        self.assertNotIn("secret", str(result))

    async def test_token_health_failure_cannot_report_twitch_ok(self):
        self.tokens.health_snapshot.side_effect = RuntimeError("sensitive")
        result = await self.build().collect()
        self.assertEqual(result["twitch"]["state"], "unknown")
        self.assertIsNone(result["twitch"]["auth_blocked_logins"])

    async def test_preview_without_observation_or_success_is_unknown(self):
        self.preview.health_snapshot.return_value["last_success_age_seconds"] = None
        self.preview.health_snapshot.return_value["latest_observation_age_seconds"] = None
        self.preview.health_snapshot.return_value["active_sessions"] = 0
        result = await self.build().collect()
        self.assertEqual(result["preview"]["state"], "unknown")

    async def test_preview_age_uses_manager_monotonic_clock(self):
        manager = PreviewManager(
            Mock(), Mock(), Mock(), enabled=True,
            initial_delay_seconds=0, interval_seconds=60,
            max_concurrent_jobs=1, job_timeout_seconds=30,
            poll_interval_seconds=60, build_content=Mock(),
            clock=lambda: 505.0,
        )
        manager._running = True
        manager._last_success_at = 500.0
        self.preview = manager
        result = await self.build().collect()
        self.assertEqual(result["preview"]["last_success_age_seconds"], 5.0)

    async def test_preview_monitor_shows_queue_limits_and_failures(self):
        """Владелец должен видеть, сколько каналов ждёт очереди и каков потолок."""
        self.preview.health_snapshot.return_value.update({
            "deferred_sessions": 3,
            "max_active_sessions": 8,
            "max_concurrent_jobs": 4,
            "consecutive_provider_failures": 2,
        })

        result = await self.build().collect()

        preview = result["preview"]
        self.assertEqual(preview["deferred_sessions"], 3)
        self.assertEqual(preview["max_active_sessions"], 8)
        self.assertEqual(preview["max_concurrent_jobs"], 4)
        self.assertEqual(preview["consecutive_provider_failures"], 2)
        self.assertIn("disk", preview)

    async def test_absent_preview_and_db_failure_do_not_hide_runtime(self):
        self.db.get_bot_stats.side_effect = RuntimeError("secret /data/bot.db")
        self.db.get_admin_live_streams.side_effect = RuntimeError("secret /data/bot.db")
        self.db.health_snapshot.side_effect = RuntimeError("secret /data/bot.db")
        result = await self.build(preview=False).collect()
        self.assertEqual(result["telegram"]["state"], "ok")
        self.assertEqual(result["preview"]["state"], "unknown")
        self.assertIsNone(result["audience"])
        self.assertIsNone(result["live"])
        self.assertIsNone(result["queues"])
        self.assertEqual(result["errors"]["database"], "unavailable")
        self.assertNotIn("secret", str(result))

    async def test_growth_failure_is_isolated_and_sanitized(self):
        self.db.growth_funnel_snapshot = AsyncMock(return_value=[
            {"source": "site", "touched": 2, "activated": 1, "ever_test_plus": 0}
        ])
        result = await self.build().collect()
        self.assertEqual(result["growth"][0]["touched"], 2)
        self.db.growth_funnel_snapshot.side_effect = RuntimeError("secret /data/bot.db")
        result = await self.build().collect()
        self.assertIsNone(result["growth"])
        self.assertEqual(result["telegram"]["state"], "ok")
        self.assertEqual(result["errors"]["database"], "unavailable")
        self.assertNotIn("secret", str(result))


    # Блоки каталога: изолированный сбор и «требует внимания»
    def directory(self, **overrides):
        directory = Mock()
        directory.access_overview = AsyncMock(return_value={
            "active_total": 4, "viewer": 3, "streamer": 1,
            "by_source": {"test": 2, "paid": 2}, "expiring_7d": 1,
        })
        directory.active_grants = AsyncMock(return_value=[{"grant_id": "g1"}])
        directory.history = AsyncMock(return_value=[{"grant_id": "g1", "action": "grant"}])
        directory.backup_status = AsyncMock(return_value={
            "last_backup_at": 1.0, "last_backup_name": "auto-x.db",
            "retention": 5, "restore_verified": False,
        })
        directory.deliveries_24h = AsyncMock(
            return_value={"notifications": 3, "reports": 1, "total": 4}
        )
        for key, value in overrides.items():
            setattr(directory, key, value)
        return directory

    async def test_missing_directory_leaves_new_blocks_none(self):
        result = await self.build().collect()

        self.assertIsNone(result["access"])
        self.assertIsNone(result["backup"])
        self.assertIsNone(result["deliveries"])
        self.assertEqual(result["attention"], [])

    async def test_directory_values_land_in_snapshot(self):
        result = await self.build(directory=self.directory()).collect()

        self.assertEqual(result["access"]["active_total"], 4)
        self.assertEqual(result["access"]["active_rows"], [{"grant_id": "g1"}])
        self.assertEqual(result["access"]["history"], [{"grant_id": "g1", "action": "grant"}])
        self.assertEqual(result["backup"]["last_backup_name"], "auto-x.db")
        self.assertEqual(result["deliveries"]["total"], 4)
        self.assertIsNone(result["errors"]["directory"])

    async def test_directory_failure_is_isolated(self):
        directory = self.directory(
            access_overview=AsyncMock(side_effect=RuntimeError("secret /data/bot.db"))
        )
        result = await self.build(directory=directory).collect()

        self.assertIsNone(result["access"])
        self.assertEqual(result["errors"]["directory"], "unavailable")
        self.assertEqual(result["telegram"]["state"], "ok")
        self.assertEqual(result["backup"]["retention"], 5)
        self.assertNotIn("secret", str(result))

    async def test_activity_block_reports_today_and_week(self):
        result = await self.build().collect()

        self.assertEqual(result["activity"]["active_today"], 7)
        self.assertEqual(result["activity"]["new_7d"], 3)
        self.assertEqual(result["activity"]["by_day"], [{"date": 0.0, "users": 2}])
        self.assertIsNone(result["errors"]["activity"])
        self.assertEqual(result["attention"], [])

    async def test_activity_failure_is_isolated_and_visible(self):
        self.db.count_active_since.side_effect = RuntimeError("secret /data/bot.db")

        result = await self.build().collect()

        self.assertIsNone(result["activity"])
        self.assertEqual(result["errors"]["activity"], "unavailable")
        self.assertIn("activity", [item["kind"] for item in result["attention"]])
        self.assertNotIn("secret", str(result))
        self.assertEqual(result["telegram"]["state"], "ok")

    async def test_activity_block_is_none_without_database_methods(self):
        # SimpleNamespace, а не Mock: Mock создаёт атрибут при обращении.
        self.db = SimpleNamespace(
            get_bot_stats=AsyncMock(return_value={"private_users": 1}),
            get_admin_live_streams=AsyncMock(return_value=[]),
            health_snapshot=AsyncMock(return_value={}),
            growth_funnel_snapshot=AsyncMock(return_value=[]),
            growth_funnel_report=AsyncMock(return_value={"steps": [], "totals": {}}),
        )

        result = await self.build().collect()

        self.assertIsNone(result["activity"])
        self.assertIsNone(result["errors"]["activity"])

    async def test_directory_failure_is_visible_in_attention(self):
        directory = self.directory(
            access_overview=AsyncMock(side_effect=RuntimeError("secret /data/bot.db"))
        )

        result = await self.build(directory=directory).collect()

        kinds = [item["kind"] for item in result["attention"]]
        self.assertIn("directory", kinds)
        self.assertEqual(result["errors"]["directory"], "unavailable")
        self.assertNotIn("secret", str(result))

    async def test_directory_blocks_share_one_time_budget(self):
        async def overview(now):
            await asyncio.sleep(1.0)
            return {"active_total": 0}

        async def rows(*args):
            await asyncio.sleep(1.0)
            return []

        async def backup():
            await asyncio.sleep(1.0)
            return {"last_backup_at": None, "last_backup_name": None,
                    "retention": 5, "restore_verified": False, "copies": 0}

        async def deliveries(now):
            await asyncio.sleep(1.0)
            return {"notifications": 0, "reports": 0, "total": 0}

        directory = Mock()
        directory.access_overview = AsyncMock(side_effect=overview)
        directory.active_grants = AsyncMock(side_effect=rows)
        directory.history = AsyncMock(side_effect=rows)
        directory.backup_status = AsyncMock(side_effect=backup)
        directory.deliveries_24h = AsyncMock(side_effect=deliveries)

        started = time.monotonic()
        result = await self.build(directory=directory).collect()
        elapsed = time.monotonic() - started

        # Пять блоков по 1 с должны уложиться в общий бюджет, а не идти последовательно.
        self.assertLess(elapsed, 2.5)
        self.assertEqual(result["access"]["active_total"], 0)
        self.assertEqual(result["backup"]["copies"], 0)
        self.assertIsNone(result["errors"]["directory"])

    async def test_attention_lists_queues_and_errors_by_impact(self):
        self.db.health_snapshot.return_value = {
            "pending_deliveries": 9, "oldest_pending_age_seconds": 600.0,
            "deferred_reports": 0, "oldest_deferred_age_seconds": None,
            "db_file_bytes": 8192, "wal_file_bytes": 0,
            "failed_jobs": 3, "due_jobs": 5, "oldest_due_age_seconds": 600.0,
        }
        self.eventsub.health_snapshot.return_value["last_error"] = "NetworkError"

        result = await self.build().collect()

        kinds = [item["kind"] for item in result["attention"]]
        self.assertEqual(kinds[0], "queue_failed")
        self.assertIn("queue_delay", kinds)
        self.assertLessEqual(len(kinds), 3)
        self.assertTrue(all(item["title"] and item["detail"] for item in result["attention"]))

    async def test_attention_empty_when_everything_healthy(self):
        result = await self.build().collect()

        self.assertEqual(result["attention"], [])


if __name__ == "__main__":
    unittest.main()
