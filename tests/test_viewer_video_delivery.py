"""Legacy preview flags must never bypass the two current media entitlements."""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter

from bot.database import Database
from bot.handlers.streams import cb_toggle_preview
from bot.live_post import (
    LivePostContent, LivePostMediaStatus, LivePostTarget, LivePostUpdater,
    TelegramAnimation,
)
from bot.poller import StreamPoller
from bot.twitch import StreamInfo


class ViewerVideoDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "media.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.add_channel(101, "alpha")
        await self.db.set_live_state(
            101, "alpha", True, "live-1", 700, "Title",
            stream_started_at="2026-10-01T00:00:00Z", broadcaster_id="1001",
            message_kind="photo",
        )

    async def test_legacy_private_toggle_does_not_grant_video_or_slot(self):
        await self.db.set_preview_enabled(101, "alpha", True)
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        callback = SimpleNamespace(
            data="togglepreview:101:alpha", from_user=SimpleNamespace(id=101),
            bot=SimpleNamespace(get_chat_member=AsyncMock()),
            message=SimpleNamespace(chat=SimpleNamespace(id=101), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        await cb_toggle_preview(callback, self.db)
        callback.message.edit_text.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)

    async def test_selected_private_video_needs_current_grant_and_matching_live_id(self):
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "media-test", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        self.assertTrue((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        await self.db.set_live_state(
            101, "alpha", True, "live-2", 701, "Title",
            stream_started_at="2026-10-01T01:00:00Z", broadcaster_id="other",
            message_kind="photo",
        )
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        await self.db.set_live_state(
            101, "alpha", True, "live-2", 701, "Title",
            stream_started_at="2026-10-01T01:00:00Z", broadcaster_id="1001",
            message_kind="photo",
        )
        self.assertTrue((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        await self.db.conn.execute(
            "UPDATE entitlement_grants SET expires_at=? WHERE grant_id=?", (now - 1, grant_id),
        )
        await self.db.conn.commit()
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)
        await self.db.revoke_test_viewer_plus(
            grant_id, revoked_at=time.time(), issued_by=425785231,
        )
        self.assertFalse((await self.db.get_preview_destination_state(101, "alpha")).preview_enabled)

    async def test_group_admin_without_streamer_plus_cannot_enable_legacy_animation(self):
        await self.db.add_channel(-1001, "alpha")
        callback = SimpleNamespace(
            data="togglepreview:-1001:alpha", from_user=SimpleNamespace(id=101),
            bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator"))),
            message=SimpleNamespace(chat=SimpleNamespace(id=101), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        await cb_toggle_preview(callback, self.db)
        self.assertFalse(await self.db.get_preview_enabled(-1001, "alpha"))
        callback.message.edit_text.assert_not_awaited()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])
        await self.db.set_preview_enabled(-1001, "alpha", True)
        self.assertFalse((await self.db.get_preview_destination_state(-1001, "alpha")).preview_enabled)

    async def test_animation_edit_rechecks_current_selection_and_grant(self):
        await self.db.set_preview_enabled(101, "alpha", True)
        bot = SimpleNamespace(edit_message_media=AsyncMock(return_value=SimpleNamespace(
            animation=SimpleNamespace(file_id="cached"),
        )))
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "live-1", 700)

        async def send():
            return await updater.apply_animation(
                target=target, animation=TelegramAnimation("cached", 6),
                is_current_physical_stream=lambda: True,
                build_content=lambda: LivePostContent("Live", None),
            )

        self.assertEqual((await send()).status, LivePostMediaStatus.SKIPPED_DISABLED)
        bot.edit_message_media.assert_not_awaited()
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "edit-test", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        self.assertEqual((await send()).status, LivePostMediaStatus.APPLIED)
        bot.edit_message_media.assert_awaited_once()
        await self.db.revoke_test_viewer_plus(
            grant_id, revoked_at=time.time(), issued_by=425785231,
        )
        self.assertEqual((await send()).status, LivePostMediaStatus.SKIPPED_DISABLED)
        bot.edit_message_media.assert_awaited_once()

    async def test_sixth_legacy_callback_cannot_extend_selected_slots(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "five-slots", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        choices = [("1001", "alpha")]
        for index, login in enumerate(("beta", "gamma", "delta", "epsilon", "zeta"), start=2):
            await self.db.add_channel(101, login)
            if index <= 5:
                choices.append((str(1000 + index), login))
        await self.db.replace_video_selection(101, choices, expected_version=0)
        callback = SimpleNamespace(
            data="togglepreview:101:zeta", from_user=SimpleNamespace(id=101),
            bot=SimpleNamespace(get_chat_member=AsyncMock()),
            message=SimpleNamespace(chat=SimpleNamespace(id=101), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        await cb_toggle_preview(callback, self.db)
        selection = await self.db.get_video_selection(101)
        self.assertEqual(len(selection.selected_ids), 5)
        self.assertNotIn("zeta", selection.selected_logins)
        self.assertFalse(await self.db.get_preview_enabled(101, "zeta"))
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_revoke_restores_existing_animation_to_photo_in_place(self):
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "photo-return", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        bot = SimpleNamespace(edit_message_media=AsyncMock(), send_photo=AsyncMock(), delete_message=AsyncMock())
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "live-1", 700)
        await self.db.revoke_test_viewer_plus(
            grant_id, revoked_at=time.time(), issued_by=425785231,
        )
        result = await updater.restore_photo(
            target=target, photo_url="https://example.test/alpha.jpg",
            build_content=lambda: LivePostContent("Live", None),
        )
        self.assertEqual(result.status, LivePostMediaStatus.APPLIED)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertEqual((state.message_id, state.message_kind, state.media_transition_pending), (700, "photo", False))
        bot.edit_message_media.assert_awaited_once()
        bot.send_photo.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_thumbnail_refresh_routes_animation_to_photo_after_refund(self):
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "poller-return", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        bot = SimpleNamespace(edit_message_media=AsyncMock(), send_message=AsyncMock(), delete_message=AsyncMock())
        updater = LivePostUpdater(bot, self.db)
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 60, live_post_updater=updater)
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=425785231)
        result = await poller._refresh_thumbnail(
            101, 700, "alpha", "live-1", "Title", 10, "Game", None,
            "https://example.test/alpha.jpg",
        )
        self.assertEqual(result.status, LivePostMediaStatus.APPLIED)
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")
        bot.edit_message_media.assert_awaited_once()

    async def test_photo_return_keeps_message_on_caption_error_and_unknown_result(self):
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(), send_message=AsyncMock(), delete_message=AsyncMock(),
        )
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "live-1", 700)
        blocked = await updater.restore_photo(
            target=target, photo_url="https://example.test/alpha.jpg",
            build_content=lambda: LivePostContent("😀" * 513, None),
        )
        self.assertEqual(blocked.status, LivePostMediaStatus.CAPTION_TOO_LONG)
        bot.edit_message_media.assert_not_awaited()
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "animation")
        bot.edit_message_media.side_effect = TelegramNetworkError(SimpleNamespace(), "offline")
        uncertain = await updater.restore_photo(
            target=target, photo_url="https://example.test/alpha.jpg",
            build_content=lambda: LivePostContent("Live", None),
        )
        self.assertEqual(uncertain.status, LivePostMediaStatus.RETRY_LATER)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertTrue(state.media_transition_pending)
        self.assertEqual(state.message_kind, "animation")
        bot.edit_message_media.side_effect = TelegramBadRequest(SimpleNamespace(), "message is not modified")
        recovered = await updater.restore_photo(
            target=target, photo_url="https://example.test/alpha.jpg",
            build_content=lambda: LivePostContent("Live", None),
        )
        self.assertEqual(recovered.status, LivePostMediaStatus.APPLIED)
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_queued_photo_return_propagates_telegram_retry_after(self):
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(side_effect=TelegramRetryAfter(
                SimpleNamespace(), "retry", retry_after=17,
            )),
            send_message=AsyncMock(), delete_message=AsyncMock(),
        )
        poller = StreamPoller(
            bot, self.db, SimpleNamespace(), 60,
            live_post_updater=LivePostUpdater(bot, self.db),
        )
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        with self.assertRaises(TelegramRetryAfter) as caught:
            await poller._refresh_thumbnail(
                101, 700, "alpha", "live-1", "Title", 10, "Game", None,
                "https://example.test/alpha.jpg", propagate_retry_after=True,
            )
        self.assertEqual(caught.exception.retry_after, 17)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertEqual(state.message_kind, "animation")
        self.assertTrue(state.media_transition_pending)
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_capacity_fallback_restores_photo_even_while_plus_is_active(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "capacity-photo", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        bot = SimpleNamespace(edit_message_media=AsyncMock(), send_message=AsyncMock(), delete_message=AsyncMock())
        poller = StreamPoller(
            bot, self.db, SimpleNamespace(), 60,
            live_post_updater=LivePostUpdater(bot, self.db),
            preview_observer=SimpleNamespace(photo_fallback_needed=lambda login: login == "alpha"),
        )
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        result = await poller._refresh_thumbnail(
            101, 700, "alpha", "live-1", "Title", 10, "Game", None,
            "https://example.test/alpha.jpg",
        )
        self.assertEqual(result.status, LivePostMediaStatus.APPLIED)
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")
        bot.edit_message_media.assert_awaited_once()

    async def test_unknown_animation_result_then_revocation_still_restores_photo(self):
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "photo", False)
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "unknown-edit", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(side_effect=TelegramNetworkError(SimpleNamespace(), "offline")),
            send_message=AsyncMock(), delete_message=AsyncMock(),
        )
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "live-1", 700)
        unknown = await updater.apply_animation(
            target=target, animation=TelegramAnimation("cached", 6),
            is_current_physical_stream=lambda: True,
            build_content=lambda: LivePostContent("Live", None),
        )
        self.assertEqual(unknown.status, LivePostMediaStatus.RETRY_LATER)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertEqual((state.message_kind, state.media_transition_target_kind), ("photo", "animation"))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=425785231)
        bot.edit_message_media.side_effect = None
        result = await updater.restore_photo(
            target=target, photo_url="https://example.test/alpha.jpg",
            build_content=lambda: LivePostContent("Live", None),
        )
        self.assertEqual(result.status, LivePostMediaStatus.APPLIED)
        state = await self.db.get_live_post_state(101, "alpha")
        self.assertEqual((state.message_kind, state.media_transition_pending), ("photo", False))
        self.assertEqual(bot.edit_message_media.await_count, 2)
        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_not_awaited()

    async def test_poller_recovers_unknown_animation_after_revocation(self):
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "photo", False)
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "poller-unknown", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(side_effect=TelegramNetworkError(SimpleNamespace(), "offline")),
            edit_message_caption=AsyncMock(), edit_message_text=AsyncMock(),
            send_message=AsyncMock(), delete_message=AsyncMock(),
        )
        updater = LivePostUpdater(bot, self.db)
        target = LivePostTarget(101, "alpha", "live-1", 700)
        await updater.apply_animation(
            target=target, animation=TelegramAnimation("cached", 6),
            is_current_physical_stream=lambda: True,
            build_content=lambda: LivePostContent("Live", None),
        )
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=425785231)
        bot.edit_message_media.side_effect = None
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 60, live_post_updater=updater)
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        result = await poller._refresh_thumbnail(
            101, 700, "alpha", "live-1", "Title", 10, "Game", None,
            "https://example.test/alpha.jpg",
        )
        self.assertEqual(result.status, LivePostMediaStatus.APPLIED)
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")
        self.assertEqual(bot.edit_message_media.await_count, 2)

    async def test_fallback_is_due_on_next_poll_without_waiting_for_thumbnail_bucket(self):
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "due-now", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        poller = StreamPoller(SimpleNamespace(), self.db, SimpleNamespace(), 60)
        self.assertFalse(await poller._photo_fallback_due(101, "alpha"))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=425785231)
        self.assertTrue(await poller._photo_fallback_due(101, "alpha"))

    async def test_offline_unfollow_and_deselect_between_content_and_send_skip_animation(self):
        for case in ("offline", "unfollow", "deselect"):
            with self.subTest(case=case):
                await self.db.add_channel(101, "alpha")
                await self.db.set_live_state(
                    101, "alpha", True, "live-1", 700, "Title",
                    stream_started_at="2026-10-01T00:00:00Z", broadcaster_id="1001",
                    message_kind="photo",
                )
                now = time.time()
                await self.db.issue_test_viewer_plus(
                    101, f"race-{case}", starts_at=now - 5, expires_at=now + 600,
                    issued_by=425785231, now=now,
                )
                selection = await self.db.get_video_selection(101)
                await self.db.replace_video_selection(
                    101, [("1001", "alpha")], expected_version=selection.version,
                )
                bot = SimpleNamespace(edit_message_media=AsyncMock())
                updater = LivePostUpdater(bot, self.db)

                async def change_after_render():
                    if case == "offline":
                        await self.db.set_live_state(101, "alpha", False, "live-1", 700, "Title", message_kind="photo")
                    elif case == "unfollow":
                        await self.db.remove_channel(101, "alpha")
                    else:
                        current = await self.db.get_video_selection(101)
                        await self.db.replace_video_selection(101, [], expected_version=current.version)
                    return LivePostContent("Live", None)

                result = await updater.apply_animation(
                    target=LivePostTarget(101, "alpha", "live-1", 700),
                    animation=TelegramAnimation("cached", 6),
                    is_current_physical_stream=lambda: True,
                    build_content=change_after_render,
                )
                self.assertIn(result.status, {LivePostMediaStatus.SKIPPED_DISABLED, LivePostMediaStatus.STALE_TARGET})
                bot.edit_message_media.assert_not_awaited()
                if case == "offline":
                    await self.db.set_live_state(
                        101, "alpha", True, "live-1", 700, "Title",
                        stream_started_at="2026-10-01T00:00:00Z", broadcaster_id="1001",
                        message_kind="photo",
                    )

    async def test_fifty_first_paused_subscription_restores_existing_animation(self):
        for index in range(49):
            await self.db.add_channel(101, f"other{index:02d}")
        await self.db.add_channel(101, "zeta")
        now = time.time()
        grant_id = await self.db.issue_test_viewer_plus(
            101, "paused-video", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("9001", "zeta")], expected_version=0)
        await self.db.set_live_state(
            101, "zeta", True, "live-z", 900, "Title", broadcaster_id="9001",
            last_seen_live_at=now,
        )
        await self.db.set_live_message_kind_if_current(
            101, "zeta", "live-z", 900, "animation", False,
        )
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=time.time(), issued_by=425785231)
        chats, _states = await self.db.snapshot_tracked_state()
        self.assertNotIn(101, chats.get("zeta", []))
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(), send_message=AsyncMock(),
            delete_message=AsyncMock(),
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={
            "zeta": StreamInfo(
                "zeta", "live-z", "Title", "Game", 10,
                "2026-10-01T00:00:00Z", "https://static-cdn.jtvnw.net/previews-ttv/live_user_zeta-{width}x{height}.jpg",
                "9001",
            ),
        }))
        poller = StreamPoller(
            bot, self.db, twitch, 60,
            live_post_updater=LivePostUpdater(bot, self.db),
        )
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        await poller._check_streams_cycle({})
        self.assertIsNotNone(poller._paused_media_cleanup_task)
        await poller._paused_media_cleanup_task
        self.assertIn("zeta", twitch.get_live_streams.await_args.args[0])
        self.assertEqual((await self.db.get_live_post_state(101, "zeta")).message_kind, "photo")
        bot.edit_message_media.assert_awaited_once()
        bot.send_message.assert_not_awaited()

    async def test_disabling_notifications_restores_photo_without_new_post(self):
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "notify-off-video", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )
        await self.db.replace_video_selection(101, [("1001", "alpha")], expected_version=0)
        await self.db.set_live_message_kind_if_current(101, "alpha", "live-1", 700, "animation", False)
        await self.db.set_notify_enabled(101, "alpha", False)
        bot = SimpleNamespace(
            edit_message_media=AsyncMock(), send_message=AsyncMock(), delete_message=AsyncMock(),
        )
        twitch = SimpleNamespace(get_live_streams=AsyncMock(return_value={
            "alpha": StreamInfo(
                "alpha", "live-1", "Title", "Game", 10,
                "2026-10-01T00:00:00Z", "https://static-cdn.jtvnw.net/previews-ttv/live_user_alpha-{width}x{height}.jpg",
                "1001",
            ),
        }))
        poller = StreamPoller(
            bot, self.db, twitch, 60,
            live_post_updater=LivePostUpdater(bot, self.db),
        )
        poller._live_post_content = AsyncMock(return_value=LivePostContent("Live", None))
        await poller._check_streams_cycle({})
        self.assertIsNotNone(poller._paused_media_cleanup_task)
        await poller._paused_media_cleanup_task
        self.assertEqual((await self.db.get_live_post_state(101, "alpha")).message_kind, "photo")
        bot.edit_message_media.assert_awaited_once()
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
