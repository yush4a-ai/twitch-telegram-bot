from __future__ import annotations

import unicodedata
from typing import Any

from bot.deep_links import normalize_twitch_login
from bot.preview_capture import CaptureEndReason, CaptureOutcome, CaptureStartResult

from .models import (
    CaptureRetryAction,
    PlaybackResolveResult,
    PlaybackResolveStatus,
    RetryDisposition,
    TwitchCaptureKey,
    TwitchCaptureStartResult,
    TwitchCaptureStartStatus,
)


def _valid_physical_stream_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and not any(
            character.isspace()
            or unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def capture_retry_action(outcome: CaptureOutcome) -> CaptureRetryAction:
    if outcome.reason in {
        CaptureEndReason.STALLED,
        CaptureEndReason.PROCESS_EXIT,
        CaptureEndReason.CLEAN_EOF,
        CaptureEndReason.START_FAILED,
    }:
        return CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE
    if outcome.reason is CaptureEndReason.DISK_PRESSURE or (
        outcome.reason is CaptureEndReason.CAPABILITY_UNAVAILABLE
        and outcome.diagnostic_code == "capacity"
    ):
        return CaptureRetryAction.WAIT_FOR_RESOURCE_IF_ACTIVE
    return CaptureRetryAction.STOP


def _retry_disposition(action: CaptureRetryAction) -> RetryDisposition:
    if action is CaptureRetryAction.STOP:
        return RetryDisposition.TERMINAL
    return RetryDisposition.RETRYABLE


class TwitchCaptureSource:
    def __init__(self, resolver: Any, capture_service: Any) -> None:
        self.resolver = resolver
        self.capture_service = capture_service

    async def open(
        self, twitch_login: str, physical_stream_id: str
    ) -> TwitchCaptureStartResult:
        login = normalize_twitch_login(twitch_login)
        if login is None or not _valid_physical_stream_id(physical_stream_id):
            return TwitchCaptureStartResult(
                status=TwitchCaptureStartStatus.INVALID_IDENTITY,
                retry_disposition=RetryDisposition.TERMINAL,
            )
        key = TwitchCaptureKey(login, physical_stream_id)
        try:
            resolve_result = await self.resolver.resolve(login)
            if not isinstance(resolve_result, PlaybackResolveResult):
                raise TypeError("resolver returned an invalid result")
        except Exception:
            return TwitchCaptureStartResult(
                status=TwitchCaptureStartStatus.RESOLVE_FAILED,
                key=key,
                resolve_status=PlaybackResolveStatus.INTERNAL_ERROR,
                retry_disposition=RetryDisposition.TERMINAL,
            )
        if resolve_result.status is not PlaybackResolveStatus.RESOLVED:
            return TwitchCaptureStartResult(
                status=TwitchCaptureStartStatus.RESOLVE_FAILED,
                key=key,
                resolve_status=resolve_result.status,
                retry_disposition=resolve_result.retry_disposition,
            )
        try:
            capture_result = await self.capture_service.start(
                resolve_result.playback.to_capture_input()
            )
            if not isinstance(capture_result, CaptureStartResult):
                raise TypeError("capture service returned an invalid result")
        except Exception:
            outcome = CaptureOutcome(CaptureEndReason.INTERNAL_ERROR)
            return TwitchCaptureStartResult(
                status=TwitchCaptureStartStatus.CAPTURE_NOT_STARTED,
                key=key,
                capture_outcome=outcome,
                retry_disposition=RetryDisposition.TERMINAL,
            )
        if capture_result.started:
            return TwitchCaptureStartResult(
                status=TwitchCaptureStartStatus.STARTED,
                key=key,
                handle=capture_result.handle,
                retry_disposition=RetryDisposition.NONE,
            )
        outcome = capture_result.outcome or CaptureOutcome(
            CaptureEndReason.INTERNAL_ERROR
        )
        action = capture_retry_action(outcome)
        return TwitchCaptureStartResult(
            status=TwitchCaptureStartStatus.CAPTURE_NOT_STARTED,
            key=key,
            capture_outcome=outcome,
            retry_disposition=_retry_disposition(action),
        )
