import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database import Database
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


class SharedStreamObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def _count(self, table):
        cursor = await self.db.conn.execute(f"SELECT COUNT(*) FROM {table}")
        return (await cursor.fetchone())[0]

    async def test_one_payload_many_memberships_and_skipped_poll(self):
        await self.db.record_stream_observation("alpha", "s1", 10.0, 10, "T1", "G1", [1, 2])
        await self.db.record_stream_observation("alpha", "s1", 20.0, 20, "T2", "G2", [1])
        await self.db.record_stream_observation("alpha", "s1", 30.0, 30, "T3", "G3", [1, 2, 3])
        self.assertEqual(await self._count("stream_observations"), 3)
        self.assertEqual(await self._count("stream_observation_memberships"), 6)
        self.assertEqual(await self._count("stream_samples"), 0)
        self.assertEqual(
            await self.db.get_stream_samples(2, "alpha", "s1"),
            [(10.0, 10, "T1", "G1"), (30.0, 30, "T3", "G3")],
        )
        self.assertEqual(
            await self.db.get_stream_samples(3, "alpha", "s1"),
            [(30.0, 30, "T3", "G3")],
        )

    async def test_legacy_stream_mode_is_global_to_stream(self):
        await self.db.add_stream_sample(1, "alpha", "old", 10.0, 10, "Old", "G")
        await self.db.record_stream_observation("alpha", "old", 20.0, 20, "New", "G", [1, 2])
        self.assertEqual(await self._count("stream_observations"), 0)
        self.assertEqual(await self.db.get_stream_samples(1, "alpha", "old"),
                         [(10.0, 10, "Old", "G"), (20.0, 20, "New", "G")])
        self.assertEqual(await self.db.get_stream_samples(2, "alpha", "old"),
                         [(20.0, 20, "New", "G")])
        await self.db.record_stream_observation("alpha", "other", 20.0, 30, "Other", "G", [2])
        self.assertEqual(await self._count("stream_observations"), 1)

    async def test_failed_membership_batch_rolls_back_shared_payload(self):
        await self.db.conn.execute(
            "CREATE TRIGGER fail_membership BEFORE INSERT ON stream_observation_memberships "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        await self.db.conn.commit()
        with self.assertRaisesRegex(Exception, "injected"):
            await self.db.record_stream_observation("alpha", "s1", 10.0, 10, "T", "G", [1])
        self.assertEqual(await self._count("stream_observations"), 0)

    async def test_shared_sample_lookup_uses_membership_and_observation_keys(self):
        statements = []
        await self.db.conn.set_trace_callback(statements.append)
        await self.db.get_stream_samples(1, "alpha", "s1")
        await self.db.conn.set_trace_callback(None)
        query = next(sql for sql in statements if "FROM stream_observation_memberships m" in sql)
        cursor = await self.db.conn.execute("EXPLAIN QUERY PLAN " + query)
        details = " ".join(str(row[3]) for row in await cursor.fetchall())
        self.assertEqual(details.count("USING PRIMARY KEY"), 2)

    async def test_latest_readers_use_exact_membership(self):
        for chat_id in (1, 2):
            await self.db.add_channel(chat_id, "alpha")
            await self.db.set_live_state(chat_id, "alpha", True, "s1", title="Live",
                                         last_seen_live_at=30.0)
        await self.db.record_stream_observation("alpha", "s1", 10.0, 10, "Live", "First", [1, 2])
        await self.db.record_stream_observation("alpha", "s1", 20.0, 20, "Live", "Second", [1])
        self.assertEqual(await self.db.list_live_channels(2), [("alpha", "Live", 10, "First")])
        self.assertEqual(await self.db.get_live_post_details(2, "alpha"), ("Live", "First"))
        self.assertEqual(await self.db.get_admin_live_streams(),
                         [{"login": "alpha", "destinations": 2, "viewers": 20, "observed_at": 30.0}])

    async def test_retention_keeps_active_and_fresh_history_per_destination(self):
        for chat_id in (1, 2, 3):
            await self.db.add_channel(chat_id, "alpha")
        await self.db.set_live_state(1, "alpha", True, "s1", last_seen_live_at=10.0)
        await self.db.add_stream_history(2, "alpha", "s1", 110.0, 10, 10, 5, 0)
        await self.db.record_stream_observation("alpha", "s1", 10.0, 10, "T", "G", [1, 2, 3])
        await self.db.purge_old_report_data(100.0)
        self.assertEqual(await self._count("stream_observations"), 1)
        self.assertEqual(await self.db.get_stream_samples(1, "alpha", "s1"), [(10.0, 10, "T", "G")])
        self.assertEqual(await self.db.get_stream_samples(2, "alpha", "s1"), [(10.0, 10, "T", "G")])
        self.assertEqual(await self.db.get_stream_samples(3, "alpha", "s1"), [])
        await self.db.set_live_state(1, "alpha", False, "s1", last_seen_live_at=10.0)
        await self.db.conn.execute("UPDATE tracked_channels SET stats_sent = 1 WHERE chat_id = 1")
        await self.db.conn.commit()
        await self.db.purge_old_report_data(110.001)
        self.assertEqual(await self._count("stream_observations"), 0)

    async def test_poller_writes_one_observation_for_two_destinations(self):
        for chat_id in (1, 2):
            await self.db.add_channel(chat_id, "alpha")
            await self.db.set_notify_enabled(chat_id, "alpha", False)
        stream = StreamInfo("alpha", "s1", "Live", "Game", 42, "2026-01-01T00:00:00Z")
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": stream}))
        poller = StreamPoller(SimpleNamespace(), self.db, twitch, 60)
        with patch("bot.poller.time.time", return_value=1000.0):
            await poller._check_streams()
        self.assertEqual(await self._count("stream_observations"), 1)
        self.assertEqual(await self._count("stream_observation_memberships"), 2)
        self.assertEqual(await self._count("stream_samples"), 0)

    async def test_poller_flushes_completed_samples_if_later_destination_fails(self):
        for chat_id in (1, 2):
            await self.db.add_channel(chat_id, "alpha")
            await self.db.set_notify_enabled(chat_id, "alpha", False)
        stream = StreamInfo("alpha", "s1", "Live", "Game", 42, "2026-01-01T00:00:00Z")
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": stream}))
        poller = StreamPoller(SimpleNamespace(), self.db, twitch, 60)
        poller._maybe_snapshot_followers = AsyncMock(side_effect=[None, RuntimeError("later destination")])
        with patch("bot.poller.time.time", return_value=1000.0):
            with self.assertRaisesRegex(RuntimeError, "later destination"):
                await poller._check_streams()
        self.assertEqual(await self._count("stream_observations"), 1)
        self.assertEqual(await self._count("stream_observation_memberships"), 2)


if __name__ == "__main__":
    unittest.main()
