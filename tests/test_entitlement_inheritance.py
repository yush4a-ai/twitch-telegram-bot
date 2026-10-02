"""Streamer access gives personal Viewer rights only to the frozen Telegram buyer."""

import tempfile
import asyncio
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import entitlements
from bot.capabilities import CapabilityService
from bot.database import Database
from bot.category_alerts import CategoryObservation
from bot.category_alert_store import CategoryAlertStore
from bot.viewer_folders import ViewerFolderService
from bot.viewer_history import ViewerHistoryService, record_job_outcome
from bot.viewer_reminders import ViewerReminderService
from bot.viewer_trial import ViewerTrialService
from bot.live_post import LivePostContent, LivePostMediaStatus, LivePostTarget, LivePostUpdater, TelegramAnimation


class EntitlementInheritanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(str(Path(self.directory.name) / "rights.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = time.time()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=self.now)
        await self.db.add_streamer_community(101, -1001, "Existing channel", "channel", now=self.now)
        for login in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
            await self.db.add_channel(101, login)
        await self.db.set_live_state(101, "alpha", True, "stream-1", 701, "Title",
                                     broadcaster_id="11", last_seen_live_at=self.now)

    async def streamer(self, key="streamer", *, start=None, end=None, beneficiary=101):
        return await self.db.issue_test_streamer_plus(
            "11", key, starts_at=self.now - 5 if start is None else start,
            expires_at=self.now + 100 if end is None else end, issued_by=999,
            beneficiary_telegram_user_id=beneficiary, now=self.now,
        )

    async def test_streamer_grants_viewer_only_to_frozen_buyer_and_independent_viewer_survives(self):
        grant = await self.streamer()
        flags = await CapabilityService(self.db).for_user(101, now=self.now)
        self.assertTrue(flags.viewer_plus_active)
        self.assertEqual((flags.viewer_channel_limit, flags.viewer_video_slots), (200, 5))
        self.assertTrue(flags.viewer_filters and flags.viewer_category_alerts)
        for stranger in (202, 999):
            self.assertFalse(await self.db.has_viewer_plus(stranger, now=self.now))
        # Current Twitch ownership must not move this person's inherited Viewer.
        await self.db.conn.execute("UPDATE streamer_identities SET telegram_user_id=202 WHERE broadcaster_id='11'")
        await self.db.conn.commit()
        self.assertTrue(await self.db.has_viewer_plus(101, now=self.now))
        self.assertFalse(await self.db.has_viewer_plus(202, now=self.now))
        viewer = await self.db.issue_test_viewer_plus(101, "viewer", starts_at=self.now - 2,
            expires_at=self.now + 500, issued_by=999, now=self.now)
        state = await entitlements.resolve_effective_viewer(self.db, 101, now=self.now)
        self.assertEqual({s.product_id for s in state.sources}, {"viewer_plus", "streamer_plus"})
        self.assertEqual(state.expires_at, self.now + 500)
        await self.db.revoke_test_streamer_plus(grant, revoked_at=self.now + 1, issued_by=999)
        self.assertTrue(await self.db.has_viewer_plus(101, now=self.now + 2))
        self.assertIsNotNone(await self.db.get_current_plus_grant(101, "viewer_plus", now=self.now + 2))
        await self.db.revoke_test_viewer_plus(viewer, revoked_at=self.now + 3, issued_by=999)
        self.assertFalse(await self.db.has_viewer_plus(101, now=self.now + 4))

    async def test_unbound_actor_foreign_buyer_future_and_gaps_do_not_give_wrong_rights(self):
        await self.db.issue_test_streamer_plus("11", "old-unbound", starts_at=self.now - 5,
            expires_at=self.now + 900, issued_by=101, now=self.now)
        self.assertFalse(await self.db.has_viewer_plus(101, now=self.now))
        with self.assertRaises(PermissionError):
            await self.streamer("foreign", beneficiary=202)
        current = await self.streamer("current", end=self.now + 100)
        future = await self.streamer("connected-future", start=self.now + 90, end=self.now + 200)
        await self.streamer("gap-future", start=self.now + 300, end=self.now + 500)
        state = await entitlements.resolve_effective_viewer(self.db, 101, now=self.now)
        self.assertEqual(state.expires_at, self.now + 200)
        self.assertEqual({s.grant_id for s in state.sources}, {current, future})
        self.assertFalse((await entitlements.resolve_effective_viewer(self.db, 101, now=self.now + 250)).active)

    async def test_inheritance_applies_inside_sql_transactions_and_dispatch(self):
        await self.streamer(end=self.now + 4000)
        folder_service = ViewerFolderService(self.db)
        folder = await folder_service.create(101, "Игры", now=self.now)
        await folder_service.move(101, "alpha", folder.id, expected_folder_id=None, now=self.now)
        await self.db.save_viewer_filter(101, "alpha", expected_version=0, games=[], title_keywords=[], exclude_keywords=[], now=self.now)
        self.assertTrue(await self.db.delete_viewer_filter(101, "alpha", expected_version=1, now=self.now))
        history = ViewerHistoryService(self.db)
        self.assertTrue(await history.record_direct_live(101, "alpha", "stream-1", 701, now=self.now))
        await record_job_outcome(self.db.conn, job_id=888, kind="viewer_reminder", user_id=101,
            login="alpha", stream_id="stream-1", category_transition_id=None, outcome="unknown", now=self.now)
        await self.db.conn.commit()
        self.assertEqual(len((await history.list_events(101, now=self.now)).events), 2)
        await ViewerReminderService(self.db).set_reminder(101, "11", "stream-1", 15, now=self.now)
        store = CategoryAlertStore(self.db, poll_interval=30)
        await store.save_preference(101, "alpha", enabled=True, category_ids=[], expected_version=0, now=self.now)
        await store.observe(CategoryObservation("11", "stream-1", "100", "Minecraft", self.now, True))
        await store.observe(CategoryObservation("11", "stream-1", "200", "Art", self.now + 1, True))
        transition = await store.observe(CategoryObservation("11", "stream-1", "200", "Art", self.now + 61, True))
        jobs = await (await self.db.conn.execute("SELECT chat_id FROM notification_jobs WHERE category_transition_id=?", (transition.transition_id,))).fetchall()
        self.assertEqual(jobs, [(101,)])
        with self.assertRaises(PermissionError):
            await ViewerTrialService(self.db).start(101, now=self.now)
        self.assertFalse((await ViewerTrialService(self.db).status(101, now=self.now)).used)

    async def test_inherited_limits_snapshot_video_sixth_and_expiry_photo(self):
        grant = await self.streamer()
        for index in range(194):
            self.assertEqual(await self.db.add_channel_with_limit(101, f"track{index:03}", 200), "created")
        self.assertEqual(await self.db.add_channel_with_limit(101, "overlimit", 200), "limit")
        snapshot, _states = await self.db.snapshot_tracked_state()
        self.assertEqual(sum(101 in chats for chats in snapshot.values()), 200)
        choices = [("11", "alpha"), ("12", "beta"), ("13", "gamma"), ("14", "delta"), ("15", "epsilon")]
        await self.db.replace_video_selection(101, choices, expected_version=0)
        with self.assertRaises(ValueError):
            await self.db.replace_video_selection(101, choices + [("16", "zeta")], expected_version=1)
        self.assertTrue((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        await self.db.revoke_test_streamer_plus(grant, revoked_at=self.now, issued_by=999)
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        self.assertEqual((await self.db.get_video_selection(101)).selected_logins, ("alpha", "beta", "gamma", "delta", "epsilon"))
        snapshot, _states = await self.db.snapshot_tracked_state()
        self.assertEqual(sum(101 in chats for chats in snapshot.values()), 50)

    async def test_two_connections_compare_and_swap_and_revoke_before_animation_edit(self):
        grant = await self.streamer()
        other = Database(str(Path(self.directory.name) / "rights.db"))
        await other.connect()
        self.addAsyncCleanup(other.close)
        results = await asyncio.gather(
            self.db.replace_video_selection(101, [("11", "alpha")], expected_version=0),
            other.replace_video_selection(101, [("11", "alpha"), ("12", "beta")], expected_version=0),
        )
        self.assertEqual(sum(result is not None for result in results), 1)
        bot = SimpleNamespace(edit_message_media=AsyncMock(return_value=SimpleNamespace(
            animation=SimpleNamespace(file_id="captured-before-revoke"))))
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "stream-1", 701)
        await other.revoke_test_streamer_plus(grant, revoked_at=self.now, issued_by=999)
        result = await updater.apply_animation(target=target,
            animation=TelegramAnimation("captured-before-revoke", 6),
            is_current_physical_stream=lambda: True,
            build_content=lambda: LivePostContent("Live", None))
        self.assertEqual(result.status, LivePostMediaStatus.SKIPPED_DISABLED)
        bot.edit_message_media.assert_not_awaited()

    async def test_inherited_animation_returns_to_photo_after_expiry_without_losing_selection(self):
        grant = await self.streamer()
        await self.db.replace_video_selection(101, [("11", "alpha")], expected_version=0)
        bot = SimpleNamespace(edit_message_media=AsyncMock(return_value=SimpleNamespace(
            animation=SimpleNamespace(file_id="animation"))), send_photo=AsyncMock(), delete_message=AsyncMock())
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "stream-1", 701)
        content = lambda: LivePostContent("Live", None)
        self.assertEqual((await updater.apply_animation(target=target,
            animation=TelegramAnimation("animation", 6),
            is_current_physical_stream=lambda: True, build_content=content)).status,
            LivePostMediaStatus.APPLIED)
        await self.db.conn.execute("UPDATE entitlement_grants SET expires_at=? WHERE grant_id=?",
                                   (self.now - 1, grant))
        await self.db.conn.commit()
        self.assertEqual((await updater.restore_photo(target=target,
            photo_url="https://example.test/alpha.jpg", build_content=content)).status,
            LivePostMediaStatus.APPLIED)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertEqual((state.message_id, state.message_kind), (701, "photo"))
        self.assertEqual((await self.db.get_video_selection(101)).selected_logins, ("alpha",))
        bot.send_photo.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_invalid_identity_clock_and_sql_expression_cannot_grant(self):
        await self.streamer()
        for bad_user in (True, -1001, 0, "101"):
            self.assertFalse(await self.db.has_viewer_plus(bad_user, now=self.now))
        for bad_clock in (True, float("nan"), float("inf")):
            self.assertFalse(await self.db.has_viewer_plus(101, now=bad_clock))
        for bad_expression in ("user; DROP TABLE entitlement_grants", "? OR 1=1", "1"):
            with self.assertRaises(ValueError):
                entitlements.effective_viewer_predicate(bad_expression, "?")
