from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, TypeAlias

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaVideo

from .database import Database, LivePostState
from .logging_utils import mask_chat_id


logger = logging.getLogger(__name__)

CAPTION_UTF16_LIMIT = 1024


class LivePostUpdateResult(Enum):
    UPDATED = "updated"
    RETRY_LATER = "retry_later"
    REPLACE_REQUIRED = "replace_required"
    KEEP_EXISTING = "keep_existing"
    STALE_TARGET = "stale_target"
    CONTENT_TOO_LONG = "content_too_long"


class LivePostMediaStatus(Enum):
    APPLIED = "applied"
    RETRY_LATER = "retry_later"
    SKIPPED_DISABLED = "skipped_disabled"
    STALE_TARGET = "stale_target"
    MESSAGE_MISSING = "message_missing"
    CAPTION_TOO_LONG = "caption_too_long"
    INVALID_MEDIA = "invalid_media"
    REJECTED = "rejected"
    STATE_CONFLICT = "state_conflict"


@dataclass(frozen=True)
class LivePostMediaResult:
    status: LivePostMediaStatus
    file_id: str | None = None


@dataclass(frozen=True)
class LivePostTarget:
    chat_id: int
    twitch_login: str
    logical_stream_id: str
    message_id: int


@dataclass(frozen=True)
class LivePostContent:
    html: str
    reply_markup: InlineKeyboardMarkup | None


@dataclass(frozen=True)
class LocalVideo:
    path: str | Path
    filename: str | None = None


@dataclass(frozen=True)
class TelegramVideo:
    file_id: str


VideoInput: TypeAlias = LocalVideo | TelegramVideo
ContentFactory: TypeAlias = Callable[
    [], LivePostContent | Awaitable[LivePostContent]
]
PhysicalStreamGuard: TypeAlias = Callable[[], bool | Awaitable[bool]]


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    references: int = 0


class _EditOutcome(Enum):
    APPLIED = "applied"
    RETRY_LATER = "retry_later"
    MISSING = "missing"
    KIND_MISMATCH = "kind_mismatch"
    REJECTED = "rejected"


def _error_text(error: BaseException) -> str:
    return str(error).lower()


def _is_not_modified(error: BaseException) -> bool:
    return "message is not modified" in _error_text(error)


def _is_missing(error: BaseException) -> bool:
    return "message to edit not found" in _error_text(error)


def _is_text_kind_mismatch(error: BaseException) -> bool:
    return "there is no text in the message to edit" in _error_text(error)


def _is_caption_kind_mismatch(error: BaseException) -> bool:
    text = _error_text(error)
    return any(
        marker in text
        for marker in (
            "message is not a media message",
            "there is no caption in the message to edit",
            "message has no caption",
        )
    )


def _is_invalid_media(error: BaseException) -> bool:
    text = _error_text(error)
    return any(
        marker in text
        for marker in (
            "wrong file identifier",
            "media_invalid",
            "invalid media",
            "invalid video",
            "failed to get http url content",
        )
    )


def _caption_fits(raw_html: str) -> bool:
    return len(raw_html.encode("utf-16-le")) // 2 <= CAPTION_UTF16_LIMIT


class LivePostUpdater:
    """Сериализует и редактирует live-post, не управляя send/delete lifecycle."""

    def __init__(self, bot: Bot, db: Database | None = None) -> None:
        self._bot = bot
        self._db = db
        self._lock_registry: dict[tuple[int, int], _LockEntry] = {}
        self._lock_registry_guard = asyncio.Lock()

    @property
    def active_lock_count(self) -> int:
        return len(self._lock_registry)

    @asynccontextmanager
    async def serialized(self, chat_id: int, message_id: int):
        key = (chat_id, message_id)
        async with self._lock_registry_guard:
            entry = self._lock_registry.get(key)
            if entry is None:
                entry = _LockEntry(asyncio.Lock())
                self._lock_registry[key] = entry
            entry.references += 1

        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._lock_registry_guard:
                entry.references -= 1
                if entry.references == 0:
                    self._lock_registry.pop(key, None)

    async def update_content(
        self,
        *,
        target: LivePostTarget,
        build_content: ContentFactory,
    ) -> LivePostUpdateResult:
        db = self._require_db()
        async with self.serialized(target.chat_id, target.message_id):
            state = await db.get_live_post_state(
                target.chat_id, target.twitch_login
            )
            if not self._is_current(state, target):
                return LivePostUpdateResult.STALE_TARGET

            content = await self._build_content(build_content)
            state = await db.get_live_post_state(
                target.chat_id, target.twitch_login
            )
            if not self._is_current(state, target):
                return LivePostUpdateResult.STALE_TARGET
            if state.media_transition_pending:
                return await self._reconcile_pending_locked(target, content)
            if state.message_kind == "text":
                outcome = await self._edit_text(target, content)
            elif state.message_kind == "video":
                if not _caption_fits(content.html):
                    return LivePostUpdateResult.CONTENT_TOO_LONG
                outcome = await self._edit_caption(target, content)
            else:
                logger.warning(
                    "Неизвестный kind live-post %r для %s",
                    state.message_kind,
                    mask_chat_id(target.chat_id),
                )
                return LivePostUpdateResult.KEEP_EXISTING
            return self._update_result(outcome)

    async def apply_video(
        self,
        *,
        target: LivePostTarget,
        video: VideoInput,
        is_current_physical_stream: PhysicalStreamGuard,
        build_content: ContentFactory,
    ) -> LivePostMediaResult:
        db = self._require_db()
        async with self.serialized(target.chat_id, target.message_id):
            state = await db.get_live_post_state(
                target.chat_id, target.twitch_login
            )
            if not self._is_current(state, target):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
            if not state.preview_enabled:
                return LivePostMediaResult(LivePostMediaStatus.SKIPPED_DISABLED)
            if not await self._guard_is_current(is_current_physical_stream):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)

            content = await self._build_content(build_content)
            if not _caption_fits(content.html):
                return LivePostMediaResult(LivePostMediaStatus.CAPTION_TOO_LONG)

            state = await db.get_live_post_state(
                target.chat_id, target.twitch_login
            )
            if not self._is_current(state, target):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
            if not state.preview_enabled:
                return LivePostMediaResult(LivePostMediaStatus.SKIPPED_DISABLED)
            if not await self._guard_is_current(is_current_physical_stream):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
            state = await db.get_live_post_state(
                target.chat_id, target.twitch_login
            )
            if not self._is_current(state, target):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
            if not state.preview_enabled:
                return LivePostMediaResult(LivePostMediaStatus.SKIPPED_DISABLED)

            if state.media_transition_pending:
                reconciled = await self._reconcile_pending_locked(target, content)
                if reconciled is not LivePostUpdateResult.UPDATED:
                    return LivePostMediaResult(
                        self._media_status_from_reconciliation(reconciled)
                    )
                state = await db.get_live_post_state(
                    target.chat_id, target.twitch_login
                )
                if not self._is_current(state, target):
                    return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
                if not state.preview_enabled:
                    return LivePostMediaResult(LivePostMediaStatus.SKIPPED_DISABLED)
                if not await self._guard_is_current(is_current_physical_stream):
                    return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
                state = await db.get_live_post_state(
                    target.chat_id, target.twitch_login
                )
                if not self._is_current(state, target):
                    return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
                if not state.preview_enabled:
                    return LivePostMediaResult(LivePostMediaStatus.SKIPPED_DISABLED)

            first_transition = state.message_kind == "text"
            if first_transition and not await db.begin_video_transition(
                target.chat_id,
                target.twitch_login,
                target.logical_stream_id,
                target.message_id,
            ):
                return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
            if first_transition:
                if not await self._guard_is_current(is_current_physical_stream):
                    await self._clear_pending(target)
                    return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
                state = await db.get_live_post_state(
                    target.chat_id, target.twitch_login
                )
                if not self._is_current(state, target):
                    return LivePostMediaResult(LivePostMediaStatus.STALE_TARGET)
                if not state.preview_enabled:
                    await self._clear_pending(target)
                    return LivePostMediaResult(
                        LivePostMediaStatus.SKIPPED_DISABLED
                    )
                if not state.media_transition_pending:
                    return LivePostMediaResult(LivePostMediaStatus.STATE_CONFLICT)

            media_input: FSInputFile | str
            if isinstance(video, LocalVideo):
                media_input = FSInputFile(video.path, filename=video.filename)
            elif isinstance(video, TelegramVideo):
                media_input = video.file_id
            else:
                if first_transition:
                    await self._clear_pending(target)
                raise TypeError(f"Unsupported video input: {type(video)!r}")

            media = InputMediaVideo(
                media=media_input,
                caption=content.html,
                parse_mode="HTML",
                supports_streaming=True,
            )
            try:
                message = await self._bot.edit_message_media(
                    chat_id=target.chat_id,
                    message_id=target.message_id,
                    media=media,
                    reply_markup=content.reply_markup,
                )
            except TelegramRetryAfter:
                if first_transition:
                    await self._clear_pending(target)
                return LivePostMediaResult(LivePostMediaStatus.RETRY_LATER)
            except TelegramNetworkError:
                return LivePostMediaResult(LivePostMediaStatus.RETRY_LATER)
            except TelegramBadRequest as error:
                if _is_not_modified(error):
                    if not first_transition:
                        if not await db.finish_video_transition(
                            target.chat_id,
                            target.twitch_login,
                            target.logical_stream_id,
                            target.message_id,
                        ):
                            return LivePostMediaResult(
                                LivePostMediaStatus.STATE_CONFLICT
                            )
                        return LivePostMediaResult(LivePostMediaStatus.APPLIED)
                    reconciled = await self._reconcile_pending_locked(
                        target, content
                    )
                    state = await db.get_live_post_state(
                        target.chat_id, target.twitch_login
                    )
                    if (
                        reconciled is LivePostUpdateResult.UPDATED
                        and self._is_current(state, target)
                        and state.message_kind == "video"
                        and not state.media_transition_pending
                    ):
                        return LivePostMediaResult(LivePostMediaStatus.APPLIED)
                    if reconciled is LivePostUpdateResult.RETRY_LATER:
                        return LivePostMediaResult(LivePostMediaStatus.RETRY_LATER)
                    if reconciled is LivePostUpdateResult.REPLACE_REQUIRED:
                        return LivePostMediaResult(
                            LivePostMediaStatus.MESSAGE_MISSING
                        )
                    return LivePostMediaResult(LivePostMediaStatus.STATE_CONFLICT)
                if first_transition:
                    await self._clear_pending(target)
                if _is_missing(error):
                    return LivePostMediaResult(LivePostMediaStatus.MESSAGE_MISSING)
                if _is_invalid_media(error):
                    return LivePostMediaResult(LivePostMediaStatus.INVALID_MEDIA)
                return LivePostMediaResult(LivePostMediaStatus.REJECTED)
            except TelegramForbiddenError:
                if first_transition:
                    await self._clear_pending(target)
                return LivePostMediaResult(LivePostMediaStatus.REJECTED)

            if not await db.finish_video_transition(
                target.chat_id,
                target.twitch_login,
                target.logical_stream_id,
                target.message_id,
            ):
                return LivePostMediaResult(LivePostMediaStatus.STATE_CONFLICT)
            video_message = getattr(message, "video", None)
            file_id = getattr(video_message, "file_id", None)
            return LivePostMediaResult(LivePostMediaStatus.APPLIED, file_id)

    async def update(
        self,
        *,
        chat_id: int,
        message_id: int,
        message_kind: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> LivePostUpdateResult:
        """P2A-compatible text-only entry point for callers without durable target."""
        if message_kind != "text":
            raise ValueError(f"Unsupported message kind: {message_kind!r}")
        target = LivePostTarget(chat_id, "", "", message_id)
        outcome = await self._edit_text(
            target, LivePostContent(text, reply_markup)
        )
        return self._update_result(outcome)

    def _require_db(self) -> Database:
        if self._db is None:
            raise RuntimeError("Database is required for durable live-post operations")
        return self._db

    @staticmethod
    def _is_current(
        state: LivePostState | None, target: LivePostTarget
    ) -> bool:
        return bool(
            state is not None
            and state.logical_stream_id == target.logical_stream_id
            and state.message_id == target.message_id
        )

    @staticmethod
    async def _build_content(factory: ContentFactory) -> LivePostContent:
        content = factory()
        if inspect.isawaitable(content):
            content = await content
        if not isinstance(content, LivePostContent):
            raise TypeError("build_content must return LivePostContent")
        return content

    @staticmethod
    async def _guard_is_current(guard: PhysicalStreamGuard) -> bool:
        result = guard()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def _edit_text(
        self, target: LivePostTarget, content: LivePostContent
    ) -> _EditOutcome:
        try:
            await self._bot.edit_message_text(
                content.html,
                chat_id=target.chat_id,
                message_id=target.message_id,
                reply_markup=content.reply_markup,
                disable_web_page_preview=True,
            )
            return _EditOutcome.APPLIED
        except TelegramRetryAfter as error:
            logger.info(
                "Лимит Telegram при обновлении поста в %s, повтор через %sс",
                mask_chat_id(target.chat_id),
                error.retry_after,
            )
            return _EditOutcome.RETRY_LATER
        except TelegramNetworkError as error:
            logger.warning(
                "Сеть недоступна при обновлении поста в %s: %s",
                mask_chat_id(target.chat_id),
                error,
            )
            return _EditOutcome.RETRY_LATER
        except TelegramBadRequest as error:
            if _is_not_modified(error):
                return _EditOutcome.APPLIED
            if _is_missing(error) or _is_text_kind_mismatch(error):
                return _EditOutcome.MISSING
            logger.warning(
                "Не удалось отредактировать сообщение %s в %s: %s",
                target.message_id,
                mask_chat_id(target.chat_id),
                error,
            )
            return _EditOutcome.REJECTED
        except TelegramForbiddenError as error:
            logger.warning(
                "Не удалось отредактировать сообщение %s в %s: %s",
                target.message_id,
                mask_chat_id(target.chat_id),
                error,
            )
            return _EditOutcome.REJECTED

    async def _edit_caption(
        self, target: LivePostTarget, content: LivePostContent
    ) -> _EditOutcome:
        try:
            await self._bot.edit_message_caption(
                chat_id=target.chat_id,
                message_id=target.message_id,
                caption=content.html,
                parse_mode="HTML",
                reply_markup=content.reply_markup,
            )
            return _EditOutcome.APPLIED
        except (TelegramRetryAfter, TelegramNetworkError):
            return _EditOutcome.RETRY_LATER
        except TelegramBadRequest as error:
            if _is_not_modified(error):
                return _EditOutcome.APPLIED
            if _is_missing(error):
                return _EditOutcome.MISSING
            if _is_caption_kind_mismatch(error):
                return _EditOutcome.KIND_MISMATCH
            logger.warning(
                "Не удалось обновить caption сообщения %s в %s: %s",
                target.message_id,
                mask_chat_id(target.chat_id),
                error,
            )
            return _EditOutcome.REJECTED
        except TelegramForbiddenError as error:
            logger.warning(
                "Не удалось обновить caption сообщения %s в %s: %s",
                target.message_id,
                mask_chat_id(target.chat_id),
                error,
            )
            return _EditOutcome.REJECTED

    async def _reconcile_pending_locked(
        self, target: LivePostTarget, content: LivePostContent
    ) -> LivePostUpdateResult:
        db = self._require_db()
        state = await db.get_live_post_state(target.chat_id, target.twitch_login)
        if not self._is_current(state, target):
            return LivePostUpdateResult.STALE_TARGET
        if not state.media_transition_pending:
            return LivePostUpdateResult.STALE_TARGET
        if not _caption_fits(content.html):
            return LivePostUpdateResult.CONTENT_TOO_LONG

        caption_outcome = await self._edit_caption(target, content)
        if caption_outcome is _EditOutcome.APPLIED:
            updated = await db.set_live_message_kind_if_current(
                target.chat_id,
                target.twitch_login,
                target.logical_stream_id,
                target.message_id,
                "video",
                False,
            )
            return (
                LivePostUpdateResult.UPDATED
                if updated
                else LivePostUpdateResult.STALE_TARGET
            )
        if caption_outcome is not _EditOutcome.KIND_MISMATCH:
            return self._update_result(caption_outcome)

        text_outcome = await self._edit_text(target, content)
        if text_outcome is _EditOutcome.APPLIED:
            updated = await db.set_live_message_kind_if_current(
                target.chat_id,
                target.twitch_login,
                target.logical_stream_id,
                target.message_id,
                "text",
                False,
            )
            return (
                LivePostUpdateResult.UPDATED
                if updated
                else LivePostUpdateResult.STALE_TARGET
            )
        return self._update_result(text_outcome)

    async def _clear_pending(self, target: LivePostTarget) -> bool:
        return await self._require_db().clear_video_transition(
            target.chat_id,
            target.twitch_login,
            target.logical_stream_id,
            target.message_id,
        )

    @staticmethod
    def _update_result(outcome: _EditOutcome) -> LivePostUpdateResult:
        return {
            _EditOutcome.APPLIED: LivePostUpdateResult.UPDATED,
            _EditOutcome.RETRY_LATER: LivePostUpdateResult.RETRY_LATER,
            _EditOutcome.MISSING: LivePostUpdateResult.REPLACE_REQUIRED,
            _EditOutcome.KIND_MISMATCH: LivePostUpdateResult.KEEP_EXISTING,
            _EditOutcome.REJECTED: LivePostUpdateResult.KEEP_EXISTING,
        }[outcome]

    @staticmethod
    def _media_status_from_reconciliation(
        result: LivePostUpdateResult,
    ) -> LivePostMediaStatus:
        return {
            LivePostUpdateResult.RETRY_LATER: LivePostMediaStatus.RETRY_LATER,
            LivePostUpdateResult.REPLACE_REQUIRED: LivePostMediaStatus.MESSAGE_MISSING,
            LivePostUpdateResult.STALE_TARGET: LivePostMediaStatus.STALE_TARGET,
            LivePostUpdateResult.CONTENT_TOO_LONG: LivePostMediaStatus.CAPTION_TOO_LONG,
            LivePostUpdateResult.KEEP_EXISTING: LivePostMediaStatus.STATE_CONFLICT,
            LivePostUpdateResult.UPDATED: LivePostMediaStatus.STATE_CONFLICT,
        }[result]
