"""Stable category changes from verified stream observations, without delivery."""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.category_alerts import CategoryObservation
from bot.category_alert_store import CategoryAlertStore
from bot.database import Database
from bot.poller import StreamPoller
from bot.twitch import StreamInfo, TwitchClient


class CategoryAlertTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "bot.db")
        self.db = Database(self.path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.store = CategoryAlertStore(self.db, poll_interval=30)

    async def see(self, category_id, at, *, name=None, stream="stream-1", broadcaster="42", live=True):
        return await self.store.observe(CategoryObservation(
            broadcaster_id=broadcaster, logical_stream_id=stream,
            category_id=category_id, category_name=name or category_id,
            observed_at=at, is_live=live,
        ))

    async def test_stable_after_two_observations_and_sixty_seconds_only_once(self):
        self.assertIsNone(await self.see("100", 0, name="A"))
        self.assertIsNone(await self.see("200", 1, name="B"))
        self.assertIsNone(await self.see("200", 60, name="B"))
        transition = await self.see("200", 61, name="B")
        self.assertIsNotNone(transition)
        self.assertEqual((transition.from_category_id, transition.to_category_id), ("100", "200"))
        self.assertEqual(transition.sequence, 1)
        self.assertIsNone(await self.see("200", 62, name="B"))
        self.assertIsNone(await self.see("100", 63, name="A"))
        second = await self.see("100", 123, name="A")
        self.assertIsNotNone(second)
        self.assertEqual(second.sequence, 2)
        self.assertNotEqual(second.transition_id, transition.transition_id)

    async def test_flap_title_only_invalid_id_and_offline_do_not_emit(self):
        await self.see("100", 0, name="A")
        await self.see("200", 1, name="B")
        self.assertIsNone(await self.see("100", 2, name="A"))
        self.assertIsNone(await self.see("100", 3, name="Renamed title"))
        self.assertIsNone(await self.see("", 4, name="B"))
        self.assertIsNone(await self.see(None, 5, name="B"))
        self.assertIsNone(await self.see("200", 6, name="B", live=False))
        self.assertIsNone(await self.see("200", 7, name="B"))
        self.assertIsNone(await self.see("100", 8, name="A"))
        self.assertEqual(await self.store.list_transitions("42"), [])

    async def test_duplicates_out_of_order_and_large_gap_reset_baseline(self):
        await self.see("100", 0)
        await self.see("200", 1)
        self.assertIsNone(await self.see("200", 1))
        self.assertIsNone(await self.see("300", 0.5))
        self.assertIsNone(await self.see("200", 123))
        self.assertIsNone(await self.see("300", 124))
        self.assertIsNone(await self.see("300", 183))
        changed = await self.see("300", 184)
        self.assertIsNotNone(changed)
        self.assertEqual(changed.from_category_id, "200")

    async def test_restart_new_session_and_persistent_unique_transition(self):
        await self.see("100", 0)
        await self.see("200", 1)
        await self.db.close()
        self.db = Database(self.path)
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.store = CategoryAlertStore(self.db, poll_interval=30)
        first = await self.see("200", 61)
        self.assertIsNotNone(first)
        self.assertIsNone(await self.see("200", 61))
        self.assertIsNone(await self.see("200", 70, stream="stream-2"))
        self.assertIsNone(await self.see("300", 71, stream="stream-2"))
        second = await self.see("300", 131, stream="stream-2")
        self.assertIsNotNone(second)
        self.assertEqual(second.sequence, 1)
        self.assertNotEqual(second.transition_id, first.transition_id)
        self.assertEqual(len(await self.store.list_transitions("42")), 2)

    async def test_transition_write_failure_rolls_back_candidate_and_sequence(self):
        await self.see("100", 0)
        await self.see("200", 1)
        await self.db.conn.execute(
            "CREATE TRIGGER fail_category_transition BEFORE INSERT ON category_transitions "
            "BEGIN SELECT RAISE(FAIL, 'injected category failure'); END"
        )
        await self.db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.see("200", 61)
        await self.db.conn.execute("DROP TRIGGER fail_category_transition")
        await self.db.conn.commit()
        transition = await self.see("200", 62)
        self.assertIsNotNone(transition)
        self.assertEqual(transition.sequence, 1)
        self.assertEqual(len(await self.store.list_transitions("42")), 1)
        cursor = await self.db.conn.execute("PRAGMA integrity_check")
        self.assertEqual((await cursor.fetchone())[0], "ok")

    async def test_poll_interval_extends_gap_threshold(self):
        self.store = CategoryAlertStore(self.db, poll_interval=60)
        await self.see("100", 0)
        await self.see("200", 1)
        self.assertIsNone(await self.see("200", 182))
        self.assertIsNone(await self.see("300", 183))
        self.assertIsNotNone(await self.see("300", 243))

    async def test_successful_shared_poller_observation_records_one_transition(self):
        for chat_id in (101, 202):
            await self.db.add_channel(chat_id, "alpha")
            await self.db.set_notify_enabled(chat_id, "alpha", False)
        stream = StreamInfo(
            "alpha", "stream-1", "Live", "A", 42, "2026-01-01T00:00:00Z",
            broadcaster_id="42", game_id="100",
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": stream}))
        poller = StreamPoller(SimpleNamespace(), self.db, twitch, 30)
        for sampled_at, game_id, game_name in ((1000.0, "100", "A"), (1001.0, "200", "B"), (1061.0, "200", "B")):
            stream.game_id = game_id
            stream.game_name = game_name
            with patch("bot.poller.time.time", return_value=sampled_at):
                await poller._check_streams()
        transitions = await self.store.list_transitions("42")
        self.assertEqual(len(transitions), 1)
        self.assertEqual((transitions[0].from_category_id, transitions[0].to_category_id), ("100", "200"))

    async def test_twitch_stream_response_retains_category_id(self):
        twitch = object.__new__(TwitchClient)
        twitch._request = AsyncMock(return_value={"data": [{
            "user_login": "alpha", "id": "stream-1", "title": "Live",
            "game_id": "100", "game_name": "A", "viewer_count": 42,
            "started_at": "2026-01-01T00:00:00Z", "user_id": "42",
        }]})
        result = await twitch.get_live_streams(["alpha"])
        self.assertEqual(result["alpha"].game_id, "100")

    async def test_offline_poller_cycle_clears_candidate_before_reconnect(self):
        await self.db.add_channel(101, "alpha")
        await self.db.set_notify_enabled(101, "alpha", False)
        stream = StreamInfo(
            "alpha", "stream-1", "Live", "A", 42, "2026-01-01T00:00:00Z",
            broadcaster_id="42", game_id="100",
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={"alpha": stream}))
        poller = StreamPoller(SimpleNamespace(), self.db, twitch, 30)
        with patch("bot.poller.time.time", return_value=1000.0):
            await poller._check_streams()
        stream.game_id, stream.game_name = "200", "B"
        with patch("bot.poller.time.time", return_value=1001.0):
            await poller._check_streams()
        twitch.get_live_streams.return_value = {}
        with patch("bot.poller.time.time", return_value=1030.0):
            await poller._check_streams()
        cursor = await self.db.conn.execute(
            "SELECT candidate_id,is_live FROM category_alert_state WHERE broadcaster_id='42'"
        )
        self.assertEqual(await cursor.fetchone(), (None, 0))

    async def test_reconnect_same_logical_stream_keeps_unique_sequence(self):
        await self.see("100", 0)
        await self.see("200", 1)
        first = await self.see("200", 61)
        self.assertIsNotNone(first)
        self.assertIsNone(await self.see("200", 62, live=False))
        self.assertIsNone(await self.see("200", 63))
        self.assertIsNone(await self.see("300", 64))
        second = await self.see("300", 124)
        self.assertIsNotNone(second)
        self.assertEqual(second.sequence, 2)
        self.assertNotEqual(first.transition_id, second.transition_id)

    async def test_two_connections_confirm_same_transition_once(self):
        await self.see("100", 0)
        await self.see("200", 1)
        second_db = Database(self.path)
        await second_db.connect()
        self.addAsyncCleanup(second_db.close)
        second_store = CategoryAlertStore(second_db, poll_interval=30)
        observation = CategoryObservation(
            broadcaster_id="42", logical_stream_id="stream-1",
            category_id="200", category_name="B", observed_at=61,
            is_live=True,
        )
        results = await asyncio.gather(
            self.store.observe(observation), second_store.observe(observation),
        )
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(await self.store.list_transitions("42")), 1)

    async def test_backup_restore_preserves_pending_candidate(self):
        await self.see("100", 0)
        await self.see("200", 1)
        await self.db.close()
        restored_path = os.path.join(self.directory.name, "restored.db")
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(restored_path)) as target:
            source.backup(target)
            self.assertEqual(target.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        restored = Database(restored_path)
        await restored.connect()
        self.addAsyncCleanup(restored.close)
        result = await CategoryAlertStore(restored, poll_interval=30).observe(CategoryObservation(
            broadcaster_id="42", logical_stream_id="stream-1",
            category_id="200", category_name="B", observed_at=61,
            is_live=True,
        ))
        self.assertIsNotNone(result)
        self.assertEqual(result.sequence, 1)

    async def test_failed_category_migration_rolls_back_new_table(self):
        path = os.path.join(self.directory.name, "failing.db")
        failing = Database(path)

        async def fail():
            await failing.conn.execute("CREATE TABLE injected_category (id INTEGER)")
            raise RuntimeError("injected category migration failure")

        failing._migrate_category_alert_schema = fail
        with self.assertRaisesRegex(RuntimeError, "injected category migration failure"):
            await failing.connect()
        await failing.close()
        with closing(sqlite3.connect(path)) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertNotIn("injected_category", tables)
