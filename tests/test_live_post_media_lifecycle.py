from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaVideo

from bot.database import Database
from bot.poller import OFFLINE_GRACE_SECONDS, StreamPoller
from bot.twitch import StreamInfo


def _live_post_module():
    return importlib.import_module("bot.live_post")


class LivePostDatabaseP2BTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._directory.name, "media.db")
        self.db = Database(self.path)
        await self.db.connect()
        await self.db.add_channel(101, "channel")
        await self.db.set_live_state(
            101,
            "channel",
            True,
            "logical-1",
            701,
            "Title",
            message_kind="text",
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._directory.cleanup()

    async def _stored_state(self) -> tuple[int | None, str, int]:
        columns = {
            row[1]
            for row in await (
                await self.db.conn.execute("PRAGMA table_info(tracked_channels)")
            ).fetchall()
        }
        self.assertIn(
            "media_transition_pending",
            columns,
            "P2B must add durable media-transition ambiguity state",
        )
        cursor = await self.db.conn.execute(
            "SELECT last_message_id, last_message_kind, media_transition_pending "
            "FROM tracked_channels WHERE chat_id = 101 AND twitch_login = 'channel'"
        )
        row = await cursor.fetchone()
        self.assertIsNotNone(row)
        return row

    def _require_db_method(self, name: str):
        method = getattr(self.db, name, None)
        self.assertIsNotNone(method, f"Database.{name} must exist")
        return method

    async def test_pending_column_defaults_to_zero_and_migrates_legacy_rows(self) -> None:
        self.assertEqual(await self._stored_state(), (701, "text", 0))
        await self.db.close()

        raw = sqlite3.connect(self.path)
        raw.execute("ALTER TABLE tracked_channels DROP COLUMN media_transition_pending")
        raw.commit()
        raw.close()

        self.db = Database(self.path)
        await self.db.connect()

        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_set_live_state_preserves_fresh_kind_and_pending_for_same_message(self) -> None:
        begin = self._require_db_method("begin_video_transition")
        finish = self._require_db_method("finish_video_transition")
        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        self.assertTrue(await finish(101, "channel", "logical-1", 701))

        await self.db.set_live_state(
            101,
            "channel",
            True,
            "logical-1",
            701,
            "Stale snapshot",
            message_kind="text",
        )
        self.assertEqual(await self._stored_state(), (701, "video", 0))

        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        await self.db.set_live_state(
            101,
            "channel",
            True,
            "logical-1",
            701,
            "Another stale snapshot",
            message_kind="text",
        )
        self.assertEqual(await self._stored_state(), (701, "video", 1))

    async def test_new_or_null_message_resets_kind_and_pending_to_text_zero(self) -> None:
        begin = self._require_db_method("begin_video_transition")
        finish = self._require_db_method("finish_video_transition")
        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        self.assertTrue(await finish(101, "channel", "logical-1", 701))

        await self.db.set_live_state(
            101, "channel", True, "logical-1", 702, message_kind="video"
        )
        self.assertEqual(await self._stored_state(), (702, "text", 0))

        await self.db.set_live_state(
            101, "channel", False, "logical-1", None, message_kind="video"
        )
        self.assertEqual(await self._stored_state(), (None, "text", 0))

    async def test_transition_cas_cannot_finish_against_replacement_row(self) -> None:
        begin = self._require_db_method("begin_video_transition")
        finish = self._require_db_method("finish_video_transition")
        self.assertTrue(await begin(101, "channel", "logical-1", 701))

        await self.db.set_live_state(101, "channel", True, "logical-1", 702)

        self.assertFalse(await finish(101, "channel", "logical-1", 701))
        self.assertEqual(await self._stored_state(), (702, "text", 0))

    async def test_conditional_kind_update_and_clear_require_current_target(self) -> None:
        set_kind = self._require_db_method("set_live_message_kind_if_current")
        clear = self._require_db_method("clear_live_message_if_current")

        self.assertFalse(
            await set_kind(101, "channel", "logical-1", 999, "video", False)
        )
        self.assertFalse(await clear(101, "channel", "logical-1", 999))
        self.assertEqual(await self._stored_state(), (701, "text", 0))

        self.assertTrue(
            await set_kind(101, "channel", "logical-1", 701, "video", False)
        )
        self.assertTrue(await clear(101, "channel", "logical-1", 701))
        self.assertEqual(await self._stored_state(), (None, "text", 0))


class LivePostUpdaterP2BTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._directory.name, "updater.db"))
        await self.db.connect()
        await self.db.add_channel(101, "channel")
        await self.db.set_live_state(
            101,
            "channel",
            True,
            "logical-1",
            701,
            "Title",
            stream_started_at="2026-01-01T00:00:00Z",
        )
        await self.db.set_preview_enabled(101, "channel", True)
        self.keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Смотреть на Twitch",
                        url="https://twitch.tv/channel",
                    )
                ]
            ]
        )
        self.bot = SimpleNamespace(
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(
                return_value=SimpleNamespace(video=SimpleNamespace(file_id="uploaded-file-id"))
            ),
            send_message=AsyncMock(),
            delete_message=AsyncMock(),
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._directory.cleanup()

    def _require_type(self, name: str):
        value = getattr(_live_post_module(), name, None)
        self.assertIsNotNone(value, f"bot.live_post.{name} must exist")
        return value

    def _updater(self, bot=None):
        cls = self._require_type("LivePostUpdater")
        try:
            return cls(bot or self.bot, self.db)
        except TypeError as error:
            self.fail(f"LivePostUpdater must accept the shared Database: {error}")

    def _target(
        self,
        *,
        chat_id: int = 101,
        login: str = "channel",
        stream_id: str = "logical-1",
        message_id: int = 701,
    ):
        cls = self._require_type("LivePostTarget")
        return cls(
            chat_id=chat_id,
            twitch_login=login,
            logical_stream_id=stream_id,
            message_id=message_id,
        )

    def _content(self, text: str = "<b>Fresh live HTML</b>"):
        cls = self._require_type("LivePostContent")
        return cls(html=text, reply_markup=self.keyboard)

    def _local_video(self, path: Path | None = None):
        cls = self._require_type("LocalVideo")
        return cls(path=path or Path(self._directory.name) / "preview.mp4")

    def _telegram_video(self, file_id: str = "cached-file-id"):
        cls = self._require_type("TelegramVideo")
        return cls(file_id=file_id)

    def _status(self, name: str):
        enum = self._require_type("LivePostMediaStatus")
        return getattr(enum, name)

    async def _build_content(self, text: str = "<b>Fresh live HTML</b>"):
        return self._content(text)

    async def _stored_state(
        self, chat_id: int = 101, login: str = "channel"
    ) -> tuple[int | None, str, int]:
        cursor = await self.db.conn.execute(
            "SELECT last_message_id, last_message_kind, media_transition_pending "
            "FROM tracked_channels WHERE chat_id = ? AND twitch_login = ?",
            (chat_id, login),
        )
        row = await cursor.fetchone()
        self.assertIsNotNone(row)
        return row

    async def _mark_video(
        self,
        *,
        chat_id: int = 101,
        login: str = "channel",
        stream_id: str = "logical-1",
        message_id: int = 701,
    ) -> None:
        method = getattr(self.db, "set_live_message_kind_if_current", None)
        self.assertIsNotNone(method, "Database guarded kind update must exist")
        self.assertTrue(
            await method(chat_id, login, stream_id, message_id, "video", False)
        )

    async def test_text_to_local_video_keeps_message_and_builds_expected_media(self) -> None:
        updater = self._updater()

        result = await updater.apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertEqual(result.file_id, "uploaded-file-id")
        self.assertEqual(await self._stored_state(), (701, "video", 0))
        call = self.bot.edit_message_media.await_args
        self.assertEqual(call.kwargs["chat_id"], 101)
        self.assertEqual(call.kwargs["message_id"], 701)
        self.assertIs(call.kwargs["reply_markup"], self.keyboard)
        media = call.kwargs["media"]
        self.assertIsInstance(media, InputMediaVideo)
        self.assertIsInstance(media.media, FSInputFile)
        self.assertEqual(media.caption, "<b>Fresh live HTML</b>")
        self.assertEqual(media.parse_mode, "HTML")
        self.assertTrue(media.supports_streaming)
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_telegram_file_id_is_reused_without_local_upload(self) -> None:
        result = await self._updater().apply_video(
            target=self._target(),
            video=self._telegram_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("APPLIED"))
        media = self.bot.edit_message_media.await_args.kwargs["media"]
        self.assertEqual(media.media, "cached-file-id")
        self.assertNotIsInstance(media.media, FSInputFile)

    async def test_media_success_without_video_file_id_still_applies(self) -> None:
        self.bot.edit_message_media.return_value = SimpleNamespace(video=None)

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertIsNone(result.file_id)
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_media_state_changes_only_after_confirmed_success(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_edit(**_kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(video=None)

        self.bot.edit_message_media.side_effect = blocked_edit
        task = asyncio.create_task(
            self._updater().apply_video(
                target=self._target(),
                video=self._local_video(),
                is_current_physical_stream=lambda: True,
                build_content=self._build_content,
            )
        )
        await entered.wait()
        self.assertEqual(await self._stored_state(), (701, "text", 1))

        release.set()
        result = await task
        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_definitive_media_failures_are_non_destructive_and_clear_pending(self) -> None:
        cases = (
            (
                TelegramBadRequest(SimpleNamespace(), "wrong file identifier"),
                "INVALID_MEDIA",
            ),
            (TelegramBadRequest(SimpleNamespace(), "message to edit not found"), "MESSAGE_MISSING"),
            (TelegramForbiddenError(SimpleNamespace(), "blocked"), "REJECTED"),
            (TelegramBadRequest(SimpleNamespace(), "other bad request"), "REJECTED"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.bot.edit_message_media.reset_mock(side_effect=True)
                self.bot.edit_message_media.side_effect = error

                result = await self._updater().apply_video(
                    target=self._target(),
                    video=self._local_video(),
                    is_current_physical_stream=lambda: True,
                    build_content=self._build_content,
                )

                self.assertEqual(result.status, self._status(expected))
                self.assertEqual(await self._stored_state(), (701, "text", 0))
                self.bot.send_message.assert_not_awaited()
                self.bot.delete_message.assert_not_awaited()

    async def test_media_retry_after_is_non_destructive_and_retryable(self) -> None:
        self.bot.edit_message_media.side_effect = TelegramRetryAfter(
            SimpleNamespace(), "retry", retry_after=3
        )

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("RETRY_LATER"))
        self.assertEqual(await self._stored_state(), (701, "text", 0))
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_media_network_ambiguity_leaves_durable_pending(self) -> None:
        self.bot.edit_message_media.side_effect = TelegramNetworkError(
            SimpleNamespace(), "offline"
        )

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("RETRY_LATER"))
        self.assertEqual(await self._stored_state(), (701, "text", 1))
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_stale_message_and_physical_guards_stop_before_telegram(self) -> None:
        updater = self._updater()
        stale_message = await updater.apply_video(
            target=self._target(message_id=999),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )
        stale_physical = await updater.apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: False,
            build_content=self._build_content,
        )

        self.assertEqual(stale_message.status, self._status("STALE_TARGET"))
        self.assertEqual(stale_physical.status, self._status("STALE_TARGET"))
        self.bot.edit_message_media.assert_not_awaited()

    async def test_physical_guard_is_rechecked_after_fresh_content(self) -> None:
        current = True

        async def supersede_artifact_during_build():
            nonlocal current
            current = False
            return self._content()

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: current,
            build_content=supersede_artifact_during_build,
        )

        self.assertEqual(result.status, self._status("STALE_TARGET"))
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_target_is_rechecked_after_async_physical_guard(self) -> None:
        await self._mark_video()
        guard_calls = 0

        async def replace_target_on_final_guard():
            nonlocal guard_calls
            guard_calls += 1
            if guard_calls == 2:
                await self.db.set_live_state(
                    101, "channel", True, "logical-1", 702, "Replacement"
                )
            return True

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._telegram_video(),
            is_current_physical_stream=replace_target_on_final_guard,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("STALE_TARGET"))
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (702, "text", 0))

    async def test_first_transition_rechecks_physical_guard_after_begin_cas(self) -> None:
        current = True
        original_begin = self.db.begin_video_transition

        async def supersede_during_begin(*args):
            nonlocal current
            began = await original_begin(*args)
            current = False
            return began

        self.db.begin_video_transition = supersede_during_begin

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: current,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("STALE_TARGET"))
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_finish_cas_conflict_never_clobbers_replacement(self) -> None:
        async def replace_during_request(**_kwargs):
            await self.db.set_live_state(101, "channel", True, "logical-1", 702)
            return SimpleNamespace(video=SimpleNamespace(file_id="old-artifact"))

        self.bot.edit_message_media.side_effect = replace_during_request

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("STATE_CONFLICT"))
        self.assertEqual(await self._stored_state(), (702, "text", 0))

    async def test_update_content_selects_text_or_caption_from_durable_kind(self) -> None:
        updater = self._updater()
        update_result = self._require_type("LivePostUpdateResult")

        text_result = await updater.update_content(
            target=self._target(), build_content=self._build_content
        )
        self.assertEqual(text_result, update_result.UPDATED)
        self.bot.edit_message_text.assert_awaited_once()

        await self._mark_video()
        self.bot.edit_message_text.reset_mock()
        caption_result = await updater.update_content(
            target=self._target(), build_content=self._build_content
        )
        self.assertEqual(caption_result, update_result.UPDATED)
        self.bot.edit_message_text.assert_not_awaited()
        call = self.bot.edit_message_caption.await_args
        self.assertEqual(call.kwargs["chat_id"], 101)
        self.assertEqual(call.kwargs["message_id"], 701)
        self.assertEqual(call.kwargs["caption"], "<b>Fresh live HTML</b>")
        self.assertEqual(call.kwargs["parse_mode"], "HTML")
        self.assertIs(call.kwargs["reply_markup"], self.keyboard)

    async def test_update_content_rechecks_target_after_async_content_build(self) -> None:
        async def replace_during_build():
            await self.db.set_live_state(
                101, "channel", True, "logical-1", 702, "Replacement"
            )
            return self._content()

        result = await self._updater().update_content(
            target=self._target(), build_content=replace_during_build
        )

        self.assertEqual(
            result, self._require_type("LivePostUpdateResult").STALE_TARGET
        )
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.edit_message_caption.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (702, "text", 0))

    async def test_video_caption_preserves_p2a_error_semantics(self) -> None:
        await self._mark_video()
        update_result = self._require_type("LivePostUpdateResult")
        cases = (
            (
                TelegramBadRequest(SimpleNamespace(), "message is not modified"),
                update_result.UPDATED,
            ),
            (
                TelegramRetryAfter(SimpleNamespace(), "retry", retry_after=3),
                update_result.RETRY_LATER,
            ),
            (
                TelegramNetworkError(SimpleNamespace(), "offline"),
                update_result.RETRY_LATER,
            ),
            (
                TelegramBadRequest(SimpleNamespace(), "message to edit not found"),
                update_result.REPLACE_REQUIRED,
            ),
            (
                TelegramForbiddenError(SimpleNamespace(), "blocked"),
                update_result.KEEP_EXISTING,
            ),
            (
                TelegramBadRequest(SimpleNamespace(), "other bad request"),
                update_result.KEEP_EXISTING,
            ),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                self.bot.edit_message_caption.reset_mock(side_effect=True)
                self.bot.edit_message_caption.side_effect = error

                result = await self._updater().update_content(
                    target=self._target(), build_content=self._build_content
                )

                self.assertEqual(result, expected)
                self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_existing_video_media_not_modified_is_applied_noop(self) -> None:
        await self._mark_video()
        self.bot.edit_message_media.side_effect = TelegramBadRequest(
            SimpleNamespace(), "message is not modified"
        )

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._telegram_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_pending_actual_video_reconciles_with_caption_probe(self) -> None:
        begin = getattr(self.db, "begin_video_transition", None)
        self.assertIsNotNone(begin)
        self.assertTrue(await begin(101, "channel", "logical-1", 701))

        result = await self._updater().update_content(
            target=self._target(), build_content=self._build_content
        )

        self.assertEqual(result, self._require_type("LivePostUpdateResult").UPDATED)
        self.bot.edit_message_caption.assert_awaited_once()
        self.bot.edit_message_text.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_pending_actual_text_reconciles_with_exact_mismatch_and_text_probe(self) -> None:
        begin = getattr(self.db, "begin_video_transition", None)
        self.assertIsNotNone(begin)
        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        self.bot.edit_message_caption.side_effect = TelegramBadRequest(
            SimpleNamespace(), "message is not a media message"
        )

        result = await self._updater().update_content(
            target=self._target(), build_content=self._build_content
        )

        self.assertEqual(result, self._require_type("LivePostUpdateResult").UPDATED)
        self.bot.edit_message_caption.assert_awaited_once()
        self.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_pending_reconciliation_network_error_keeps_pending_without_fallback(self) -> None:
        begin = getattr(self.db, "begin_video_transition", None)
        self.assertIsNotNone(begin)
        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        self.bot.edit_message_caption.side_effect = TelegramNetworkError(
            SimpleNamespace(), "offline"
        )

        result = await self._updater().update_content(
            target=self._target(), build_content=self._build_content
        )

        self.assertEqual(result, self._require_type("LivePostUpdateResult").RETRY_LATER)
        self.assertEqual(await self._stored_state(), (701, "text", 1))
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
        self.bot.delete_message.assert_not_awaited()

    async def test_restart_after_telegram_success_before_db_finish_recovers_video(self) -> None:
        begin = getattr(self.db, "begin_video_transition", None)
        self.assertIsNotNone(begin)
        self.assertTrue(await begin(101, "channel", "logical-1", 701))
        self.assertEqual(await self._stored_state(), (701, "text", 1))

        restarted_updater = self._updater()
        result = await restarted_updater.update_content(
            target=self._target(), build_content=self._build_content
        )

        self.assertEqual(result, self._require_type("LivePostUpdateResult").UPDATED)
        self.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_first_media_not_modified_does_not_blindly_flip_text_to_video(self) -> None:
        self.bot.edit_message_media.side_effect = TelegramBadRequest(
            SimpleNamespace(), "message is not modified"
        )
        self.bot.edit_message_caption.side_effect = TelegramBadRequest(
            SimpleNamespace(), "message is not a media message"
        )

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )

        self.assertEqual(result.status, self._status("STATE_CONFLICT"))
        self.bot.edit_message_caption.assert_awaited_once()
        self.bot.edit_message_text.assert_awaited_once()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_caption_preflight_blocks_first_conversion_without_truncation(self) -> None:
        long_html = "x" * 1025

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=lambda: self._build_content(long_html),
        )

        self.assertEqual(result.status, self._status("CAPTION_TOO_LONG"))
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_caption_preflight_counts_raw_html_utf16_code_units(self) -> None:
        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=lambda: self._build_content("😀" * 512),
        )

        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertEqual(
            self.bot.edit_message_media.await_args.kwargs["media"].caption,
            "😀" * 512,
        )

    async def test_oversize_update_for_existing_video_keeps_previous_caption(self) -> None:
        await self._mark_video()

        result = await self._updater().update_content(
            target=self._target(),
            build_content=lambda: self._build_content("😀" * 513),
        )

        self.assertEqual(
            result, self._require_type("LivePostUpdateResult").CONTENT_TOO_LONG
        )
        self.bot.edit_message_caption.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_preview_disabled_blocks_conversion_but_not_existing_video_caption(self) -> None:
        updater = self._updater()
        await self.db.set_preview_enabled(101, "channel", False)

        blocked = await updater.apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=self._build_content,
        )
        self.assertEqual(blocked.status, self._status("SKIPPED_DISABLED"))
        self.bot.edit_message_media.assert_not_awaited()

        await self._mark_video()
        updated = await updater.update_content(
            target=self._target(), build_content=self._build_content
        )
        self.assertEqual(updated, self._require_type("LivePostUpdateResult").UPDATED)
        self.bot.edit_message_caption.assert_awaited_once()
        self.assertEqual(await self._stored_state(), (701, "video", 0))

    async def test_preview_is_rechecked_after_fresh_content_before_media_request(self) -> None:
        async def disable_during_content_build():
            await self.db.set_preview_enabled(101, "channel", False)
            return self._content()

        result = await self._updater().apply_video(
            target=self._target(),
            video=self._local_video(),
            is_current_physical_stream=lambda: True,
            build_content=disable_during_content_build,
        )

        self.assertEqual(result.status, self._status("SKIPPED_DISABLED"))
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._stored_state(), (701, "text", 0))

    async def test_same_message_operations_serialize_and_build_fresh_content_inside_lock(self) -> None:
        updater = self._updater()
        holder_entered = asyncio.Event()
        release_holder = asyncio.Event()
        content_built = asyncio.Event()

        async def hold_lock() -> None:
            async with updater.serialized(101, 701):
                holder_entered.set()
                await release_holder.wait()

        async def build_after_lock():
            content_built.set()
            return self._content()

        holder = asyncio.create_task(hold_lock())
        await holder_entered.wait()
        media = asyncio.create_task(
            updater.apply_video(
                target=self._target(),
                video=self._local_video(),
                is_current_physical_stream=lambda: True,
                build_content=build_after_lock,
            )
        )
        await asyncio.sleep(0)
        self.assertFalse(content_built.is_set())
        self.bot.edit_message_media.assert_not_awaited()

        release_holder.set()
        await holder
        result = await asyncio.wait_for(media, timeout=1)
        self.assertEqual(result.status, self._status("APPLIED"))
        self.assertTrue(content_built.is_set())
        self.assertEqual(updater.active_lock_count, 0)

    async def test_caption_and_media_for_same_message_never_overlap(self) -> None:
        await self._mark_video()
        updater = self._updater()
        caption_entered = asyncio.Event()
        release_caption = asyncio.Event()

        async def blocked_caption(**_kwargs):
            caption_entered.set()
            await release_caption.wait()

        self.bot.edit_message_caption.side_effect = blocked_caption
        caption = asyncio.create_task(
            updater.update_content(
                target=self._target(), build_content=self._build_content
            )
        )
        await caption_entered.wait()
        media = asyncio.create_task(
            updater.apply_video(
                target=self._target(),
                video=self._telegram_video(),
                is_current_physical_stream=lambda: True,
                build_content=self._build_content,
            )
        )
        await asyncio.sleep(0)
        self.bot.edit_message_media.assert_not_awaited()

        release_caption.set()
        await caption
        await asyncio.wait_for(media, timeout=1)
        self.bot.edit_message_media.assert_awaited_once()
        self.assertEqual(updater.active_lock_count, 0)

    async def test_different_message_ids_run_in_parallel_and_registry_cleans_up(self) -> None:
        await self.db.add_channel(202, "other")
        await self.db.set_live_state(202, "other", True, "logical-2", 702)
        await self.db.set_preview_enabled(202, "other", True)
        updater = self._updater()
        both_entered = asyncio.Event()
        release = asyncio.Event()
        entered: set[int] = set()

        async def blocked_media(**kwargs):
            entered.add(kwargs["message_id"])
            if entered == {701, 702}:
                both_entered.set()
            await release.wait()
            return SimpleNamespace(video=None)

        self.bot.edit_message_media.side_effect = blocked_media
        tasks = [
            asyncio.create_task(
                updater.apply_video(
                    target=target,
                    video=self._local_video(),
                    is_current_physical_stream=lambda: True,
                    build_content=self._build_content,
                )
            )
            for target in (
                self._target(),
                self._target(
                    chat_id=202,
                    login="other",
                    stream_id="logical-2",
                    message_id=702,
                ),
            )
        ]
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        self.assertEqual(updater.active_lock_count, 2)

        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(updater.active_lock_count, 0)

    async def test_cancelled_lock_waiter_releases_registry_registration(self) -> None:
        updater = self._updater()
        holder_entered = asyncio.Event()
        release = asyncio.Event()

        async def hold() -> None:
            async with updater.serialized(101, 701):
                holder_entered.set()
                await release.wait()

        async def wait() -> None:
            async with updater.serialized(101, 701):
                self.fail("cancelled waiter must not enter")

        holder = asyncio.create_task(hold())
        await holder_entered.wait()
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(updater.active_lock_count, 1)

        release.set()
        await holder
        self.assertEqual(updater.active_lock_count, 0)


class StreamPollerP2BTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self._directory.name, "poller.db"))
        await self.db.connect()
        await self.db.add_channel(101, "channel")
        await self.db.set_live_state(
            101,
            "channel",
            True,
            "logical-1",
            701,
            "Initial title",
            stream_started_at="2026-01-01T00:00:00Z",
            last_seen_live_at=1000.0,
        )
        self.telegram = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=702)),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(return_value=SimpleNamespace(video=None)),
            delete_message=AsyncMock(),
        )
        self.twitch = SimpleNamespace(
            get_live_streams=AsyncMock(return_value={"channel": self._live()})
        )
        live_post = _live_post_module()
        updater_cls = getattr(live_post, "LivePostUpdater", None)
        self.assertIsNotNone(updater_cls)
        try:
            self.updater = updater_cls(self.telegram, self.db)
        except TypeError as error:
            self.fail(f"LivePostUpdater must accept Database: {error}")
        try:
            self.poller = StreamPoller(
                self.telegram,
                self.db,
                self.twitch,
                60,
                live_post_updater=self.updater,
            )
        except TypeError as error:
            self.fail(f"StreamPoller must accept the shared updater: {error}")
        self.poller._maybe_snapshot_followers = AsyncMock()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._directory.cleanup()

    @staticmethod
    def _live(stream_id: str = "logical-1", title: str = "Fresh title") -> StreamInfo:
        return StreamInfo(
            user_login="channel",
            stream_id=stream_id,
            title=title,
            game_name="Fresh game",
            viewer_count=42,
            started_at="2026-01-01T00:00:00Z",
        )

    async def _mark_video(self) -> None:
        method = getattr(self.db, "set_live_message_kind_if_current", None)
        self.assertIsNotNone(method)
        self.assertTrue(
            await method(101, "channel", "logical-1", 701, "video", False)
        )

    async def _state(self) -> tuple[int | None, str, int]:
        cursor = await self.db.conn.execute(
            "SELECT last_message_id, last_message_kind, media_transition_pending "
            "FROM tracked_channels WHERE chat_id = 101 AND twitch_login = 'channel'"
        )
        row = await cursor.fetchone()
        self.assertIsNotNone(row)
        return row

    async def test_poller_uses_injected_shared_updater(self) -> None:
        self.assertIs(self.poller._live_post_updater, self.updater)

    async def test_stale_update_result_cannot_restore_replaced_message_pointer(self) -> None:
        update_result = getattr(_live_post_module(), "LivePostUpdateResult", None)
        self.assertIsNotNone(update_result)

        async def replace_before_update(**_kwargs):
            await self.db.set_live_state(
                101, "channel", True, "logical-1", 702, "Replacement"
            )
            return update_result.STALE_TARGET

        self.updater.update_content = AsyncMock(side_effect=replace_before_update)

        await self.poller._check_streams()

        self.assertEqual(await self._state(), (702, "text", 0))
        self.telegram.send_message.assert_not_awaited()
        self.telegram.delete_message.assert_not_awaited()

    async def test_restart_video_and_reconnect_update_caption_on_same_message(self) -> None:
        await self._mark_video()

        await self.poller._check_streams()
        self.telegram.edit_message_caption.assert_awaited_once()
        self.assertEqual(
            self.telegram.edit_message_caption.await_args.kwargs["message_id"], 701
        )
        self.telegram.edit_message_text.assert_not_awaited()
        self.telegram.send_message.assert_not_awaited()

        await self.db.set_live_state(
            101,
            "channel",
            False,
            "logical-1",
            701,
            "Fresh title",
            offline_since=1060.0,
            stream_started_at="2026-01-01T00:00:00Z",
            message_kind="video",
        )
        self.twitch.get_live_streams.return_value = {
            "channel": self._live("physical-2", "Reconnected")
        }
        self.telegram.edit_message_caption.reset_mock()

        with unittest.mock.patch("bot.poller.time.time", return_value=1120.0):
            await self.poller._check_streams()

        self.telegram.edit_message_caption.assert_awaited_once()
        self.telegram.send_message.assert_not_awaited()
        self.assertEqual(await self._state(), (701, "video", 0))
        self.assertEqual((await self.db.get_live_state(101, "channel"))[1], "logical-1")

    async def test_notify_false_then_true_reuses_video_post_without_duplicate(self) -> None:
        await self._mark_video()
        await self.db.set_notify_enabled(101, "channel", False)

        await self.poller._check_streams()
        self.assertEqual(await self._state(), (701, "video", 0))
        self.telegram.edit_message_caption.assert_not_awaited()

        await self.db.set_notify_enabled(101, "channel", True)
        await self.poller._check_streams()

        self.telegram.edit_message_caption.assert_awaited_once()
        self.telegram.send_message.assert_not_awaited()
        self.assertEqual(await self._state(), (701, "video", 0))

    async def test_video_offline_cleanup_deletes_same_id_and_resets_media_state(self) -> None:
        await self._mark_video()
        await self.db.set_live_state(
            101,
            "channel",
            False,
            "logical-1",
            701,
            "Title",
            offline_since=1000.0,
            message_kind="video",
        )

        with unittest.mock.patch(
            "bot.poller.time.time", return_value=1000.0 + OFFLINE_GRACE_SECONDS + 1
        ):
            await self.poller._cleanup_offline_posts()

        self.telegram.delete_message.assert_awaited_once_with(101, 701)
        self.assertEqual(await self._state(), (None, "text", 0))

    async def test_deleted_video_gets_one_silent_text_replacement_and_old_artifact_is_stale(self) -> None:
        await self._mark_video()
        self.telegram.edit_message_caption.side_effect = TelegramBadRequest(
            SimpleNamespace(), "message to edit not found"
        )

        await self.poller._check_streams()

        self.telegram.send_message.assert_awaited_once()
        self.assertTrue(self.telegram.send_message.await_args.kwargs["disable_notification"])
        self.assertEqual(await self._state(), (702, "text", 0))

        target_cls = getattr(_live_post_module(), "LivePostTarget", None)
        local_cls = getattr(_live_post_module(), "LocalVideo", None)
        self.assertIsNotNone(target_cls)
        self.assertIsNotNone(local_cls)
        result = await self.updater.apply_video(
            target=target_cls(101, "channel", "logical-1", 701),
            video=local_cls(Path(self._directory.name) / "old.mp4"),
            is_current_physical_stream=lambda: True,
            build_content=lambda: self.poller._live_post_content(
                "channel", "Old", "Game", 1, None, include_track_link=False
            ),
        )

        status = getattr(_live_post_module(), "LivePostMediaStatus", None)
        self.assertIsNotNone(status)
        self.assertEqual(result.status, status.STALE_TARGET)
        self.telegram.edit_message_media.assert_not_awaited()
