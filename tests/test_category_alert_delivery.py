"""Viewer category alerts use current server rights and a bounded durable queue."""

import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.category_alerts import CategoryObservation
from bot.category_alert_store import CategoryAlertStore
from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationOutcome, NotificationWorker
from bot.poller import StreamPoller
from bot.twitch import TwitchClient, GAMES_URL, SEARCH_CATEGORIES_URL


class CategoryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        for user_id in (101, 202):
            await self.db.add_channel(user_id, "alpha")
            await self.db.set_live_state(
                user_id, "alpha", True, "stream-1", None, "Live",
                stream_started_at="2026-01-01T00:00:00Z",
                broadcaster_id="42", last_seen_live_at=1000,
            )
        self.store = CategoryAlertStore(self.db, poll_interval=30)
        self.queue = NotificationQueue(self.db)

    async def grant(self, user_id):
        return await self.db.issue_test_viewer_plus(
            user_id, f"category-{user_id}", starts_at=0,
            expires_at=9999999999, issued_by=425785231, now=0,
        )

    async def select(self, user_id, *, ids=()):
        return await self.store.save_preference(
            user_id, "alpha", enabled=True, category_ids=list(ids),
            expected_version=0, now=1000,
        )

    async def see(self, category_id, at, name=None):
        return await self.store.observe(CategoryObservation(
            "42", "stream-1", category_id, name or category_id, at, True,
        ))

    async def jobs(self, at=2000):
        return await self.queue.claim_due(at, limit=10, lease_seconds=60)

    async def test_free_and_opt_out_get_no_job_plus_selected_get_one(self):
        await self.grant(202)
        await self.select(202, ids=("200",))
        await self.see("100", 1000)
        await self.see("200", 1001)
        transition = await self.see("200", 1061)
        self.assertIsNotNone(transition)
        claimed = await self.jobs()
        self.assertEqual([(job.chat_id, job.kind) for job in claimed], [(202, "viewer_category_change")])
        self.assertEqual(claimed[0].category_transition_id, transition.transition_id)
        self.assertEqual(await self.store.enqueue_for_transition(transition.transition_id, now=1062), 0)

    async def test_disabled_notify_quiet_and_wrong_category_are_excluded(self):
        await self.grant(101)
        await self.select(101, ids=("300",))
        await self.grant(202)
        await self.select(202)
        await self.db.set_notify_enabled(202, "alpha", False)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        self.assertEqual(await self.jobs(), [])

    async def test_quiet_hours_and_excluded_title_suppress_enqueue(self):
        await self.grant(101)
        await self.select(101)
        await self.db.set_quiet_hours(101, 0, 1439, 0)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        self.assertEqual(await self.jobs(), [])
        await self.db.clear_quiet_hours(101)
        await self.db.save_viewer_filter(
            101, "alpha", expected_version=0, games=[], title_keywords=[],
            exclude_keywords=["rerun"], now=1062,
        )
        await self.db.conn.execute(
            "UPDATE tracked_channels SET last_title='Live rerun' WHERE chat_id=101"
        )
        await self.db.conn.commit()
        await self.see("300", 1062)
        await self.see("300", 1122)
        self.assertEqual(await self.jobs(), [])

    async def test_latest_category_coalesces_during_five_minute_cooldown(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        first = await self.see("200", 1061)
        job = (await self.jobs(1061))[0]
        await self.store.record_success(101, first.transition_id, now=1061)
        await self.queue.ack(job.id, job.attempt_count, now=1061)
        await self.see("300", 1100)
        await self.see("300", 1160)
        await self.see("400", 1200)
        latest = await self.see("400", 1260)
        self.assertEqual(await self.jobs(1360), [])
        due = await self.jobs(1361)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].category_transition_id, latest.transition_id)

    async def test_dispatch_rechecks_revoke_offline_and_known_success(self):
        grant_id = await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        job = (await self.jobs())[0]
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)))
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 30)
        with patch("bot.poller.time.time", return_value=1062):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.SENT)
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
        bot.send_message.assert_awaited_once()
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=1063, issued_by=425785231)
        with patch("bot.poller.time.time", return_value=1064):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)

    async def test_dispatch_rechecks_offline_notify_and_preference(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        job = (await self.jobs())[0]
        bot = SimpleNamespace(send_message=AsyncMock())
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 30)
        await self.db.set_notify_enabled(101, "alpha", False)
        with patch("bot.poller.time.time", return_value=1062):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
        await self.db.set_notify_enabled(101, "alpha", True)
        await self.store.save_preference(
            101, "alpha", enabled=False, category_ids=[],
            expected_version=1, now=1062,
        )
        with patch("bot.poller.time.time", return_value=1062):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
        await self.store.save_preference(
            101, "alpha", enabled=True, category_ids=[],
            expected_version=2, now=1062,
        )
        await self.store.observe(CategoryObservation(
            "42", "stream-1", None, None, 1062, False,
        ))
        with patch("bot.poller.time.time", return_value=1063):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
        bot.send_message.assert_not_awaited()

    async def test_unfollow_removes_preference_and_stales_queued_job(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        job = (await self.jobs())[0]
        await self.db.remove_channel(101, "alpha")
        self.assertFalse((await self.store.get_preference(101, "alpha")).enabled)
        bot = SimpleNamespace(send_message=AsyncMock())
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 30)
        with patch("bot.poller.time.time", return_value=1062):
            self.assertEqual(await poller.send_queued_job(job), NotificationOutcome.STALE)
        bot.send_message.assert_not_awaited()

    async def test_job_waits_to_300_seconds_and_sends_only_current_change(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        first = await self.see("200", 1061)
        job = (await self.jobs(1061))[0]
        await self.store.record_success(101, first.transition_id, now=1061)
        await self.queue.ack(job.id, job.attempt_count, now=1061)
        await self.see("300", 1100)
        await self.see("300", 1160)
        await self.see("300", 1250)
        await self.see("300", 1350)
        next_job = (await self.jobs(1361))[0]
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=8)))
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 30)
        from bot.notification_worker import NotificationRetryAfter
        with patch("bot.poller.time.time", return_value=1360):
            with self.assertRaises(NotificationRetryAfter) as raised:
                await poller.send_queued_job(next_job)
        self.assertEqual(raised.exception.retry_after, 1)
        bot.send_message.assert_not_awaited()
        with patch("bot.poller.time.time", return_value=1361):
            self.assertEqual(await poller.send_queued_job(next_job), NotificationOutcome.SENT)
        bot.send_message.assert_awaited_once()

    async def test_enqueue_failure_rolls_back_transition(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.db.conn.execute(
            "CREATE TRIGGER fail_category_job BEFORE INSERT ON notification_jobs "
            "WHEN NEW.kind='viewer_category_change' "
            "BEGIN SELECT RAISE(FAIL, 'injected enqueue failure'); END"
        )
        await self.db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.see("200", 1061)
        self.assertEqual(await self.store.list_transitions("42"), [])
        await self.db.conn.execute("DROP TRIGGER fail_category_job")
        await self.db.conn.commit()
        self.assertIsNotNone(await self.see("200", 1062))

    async def test_invalid_one_viewer_filter_does_not_block_other_viewers(self):
        for user_id in (101, 202):
            await self.grant(user_id)
            await self.select(user_id)
        await self.db.conn.execute(
            "UPDATE category_alert_preferences SET category_ids_json='invalid-json' "
            "WHERE telegram_user_id=101"
        )
        await self.db.conn.commit()
        await self.see("100", 1000)
        await self.see("200", 1001)
        self.assertIsNotNone(await self.see("200", 1061))
        self.assertEqual([job.chat_id for job in await self.jobs()], [202])

    async def test_ambiguous_category_send_is_terminal_not_retried(self):
        await self.grant(101)
        await self.select(101)
        await self.see("100", 1000)
        await self.see("200", 1001)
        await self.see("200", 1061)
        calls = AsyncMock(side_effect=TimeoutError("unknown Telegram result"))
        worker = NotificationWorker(
            self.queue, calls, max_concurrency=1,
            per_chat_interval=0, global_interval=0, group_chat_interval=0,
            lease_seconds=60, send_timeout=5, clock=lambda: 1062,
        )
        self.assertEqual(await worker.run_once(), 1)
        self.assertEqual(await worker.run_once(), 0)
        calls.assert_awaited_once()
        cursor = await self.db.conn.execute(
            "SELECT status,last_error_class FROM notification_jobs "
            "WHERE kind='viewer_category_change'"
        )
        self.assertEqual(await cursor.fetchone(), ("failed", "UnknownOutcome"))

    async def test_twitch_category_lookup_uses_stable_ids(self):
        twitch = object.__new__(TwitchClient)
        twitch._request = AsyncMock(side_effect=[
            {"data": [{"id": "100", "name": "Minecraft"}]},
            {"data": [{"id": "100", "name": "Minecraft"}]},
        ])
        self.assertEqual(await twitch.search_categories("mine", limit=8), [("100", "Minecraft")])
        self.assertEqual(await twitch.get_categories(["100"]), {"100": "Minecraft"})
        self.assertEqual(twitch._request.await_args_list[0].args[0], SEARCH_CATEGORIES_URL)
        self.assertEqual(twitch._request.await_args_list[1].args[0], GAMES_URL)


if __name__ == "__main__":
    unittest.main()
