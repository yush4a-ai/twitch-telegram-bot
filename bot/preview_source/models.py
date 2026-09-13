from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from bot.preview_capture import CaptureOutcome, UrlCaptureInput


_DIAGNOSTIC_CODE = re.compile(r"[a-z0-9_]{1,64}\Z")


def sanitize_diagnostic_code(value: str | None) -> str | None:
    if value is None:
        return None
    if _DIAGNOSTIC_CODE.fullmatch(value) is None:
        return "redacted"
    return value


class RetryDisposition(str, Enum):
    NONE = "none"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class PlaybackResolveStatus(str, Enum):
    RESOLVED = "resolved"
    INVALID_LOGIN = "invalid_login"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    TIMEOUT = "timeout"
    PROCESS_FAILED = "process_failed"
    MALFORMED_OUTPUT = "malformed_output"
    INTERNAL_ERROR = "internal_error"


class StreamlinkCapabilityReason(str, Enum):
    EXECUTABLE_MISSING = "executable_missing"
    VERSION_PROBE_FAILED = "version_probe_failed"
    VERSION_UNSUPPORTED = "version_unsupported"
    TWITCH_PLUGIN_MISSING = "twitch_plugin_missing"
    CLI_CONTRACT_UNSUPPORTED = "cli_contract_unsupported"
    PROBE_TIMEOUT = "probe_timeout"
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True)
class StreamlinkCapability:
    available: bool
    reason: StreamlinkCapabilityReason | None = None
    version: str | None = None
    twitch_plugin_available: bool = False

    def __post_init__(self) -> None:
        if self.available and self.reason is not None:
            raise ValueError("available capability cannot have a failure reason")
        if not self.available and self.reason is None:
            raise ValueError("unavailable capability requires a failure reason")


@dataclass(frozen=True, repr=False)
class ResolvedPlayback:
    _secret_url: str

    def __repr__(self) -> str:
        return "ResolvedPlayback(<redacted>)"

    __str__ = __repr__

    def to_capture_input(self) -> UrlCaptureInput:
        return UrlCaptureInput(self._secret_url)


@dataclass(frozen=True, repr=False)
class PlaybackResolveResult:
    status: PlaybackResolveStatus
    retry_disposition: RetryDisposition
    playback: ResolvedPlayback | None = None
    exit_code: int | None = None
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is PlaybackResolveStatus.RESOLVED:
            if self.playback is None:
                raise ValueError("resolved result requires playback")
        elif self.playback is not None:
            raise ValueError("failure result cannot contain playback")
        object.__setattr__(
            self,
            "diagnostic_code",
            sanitize_diagnostic_code(self.diagnostic_code),
        )

    def __repr__(self) -> str:
        return (
            "PlaybackResolveResult("
            f"status={self.status.value!r}, "
            f"retry_disposition={self.retry_disposition.value!r}, "
            f"exit_code={self.exit_code!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class TwitchCaptureKey:
    twitch_login: str
    physical_stream_id: str

    def __repr__(self) -> str:
        return "TwitchCaptureKey(<redacted>)"

    __str__ = __repr__


class TwitchCaptureStartStatus(str, Enum):
    STARTED = "started"
    INVALID_IDENTITY = "invalid_identity"
    RESOLVE_FAILED = "resolve_failed"
    CAPTURE_NOT_STARTED = "capture_not_started"


@dataclass(frozen=True, repr=False)
class TwitchCaptureStartResult:
    status: TwitchCaptureStartStatus
    retry_disposition: RetryDisposition
    key: TwitchCaptureKey | None = None
    handle: object | None = None
    resolve_status: PlaybackResolveStatus | None = None
    capture_outcome: CaptureOutcome | None = None

    def __post_init__(self) -> None:
        outcome = self.capture_outcome
        if outcome is not None:
            object.__setattr__(
                self,
                "capture_outcome",
                CaptureOutcome(
                    reason=outcome.reason,
                    exit_code=outcome.exit_code,
                    diagnostic_code=sanitize_diagnostic_code(
                        outcome.diagnostic_code
                    ),
                    cleanup_deferred=outcome.cleanup_deferred,
                ),
            )

    def __repr__(self) -> str:
        fields = [
            f"status={self.status.value!r}",
            f"retry_disposition={self.retry_disposition.value!r}",
        ]
        if self.resolve_status is not None:
            fields.append(f"resolve_status={self.resolve_status.value!r}")
        return f"TwitchCaptureStartResult({', '.join(fields)})"

    __str__ = __repr__


class CaptureRetryAction(str, Enum):
    FRESH_RESOLVE_IF_ACTIVE = "fresh_resolve_if_active"
    WAIT_FOR_RESOURCE_IF_ACTIVE = "wait_for_resource_if_active"
    STOP = "stop"
