from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass(frozen=True, repr=False)
class UrlCaptureInput:
    url: str

    def __repr__(self) -> str:
        return "UrlCaptureInput(<redacted>)"


@dataclass(frozen=True)
class FileCaptureInput:
    path: Path

    def __init__(self, path: Path | str) -> None:
        object.__setattr__(self, "path", Path(path))


@dataclass(frozen=True)
class SegmentRecord:
    sequence: int
    path: Path
    duration_seconds: float
    size_bytes: int
    completed_at_monotonic: float


class SnapshotStatus(str, Enum):
    READY = "ready"
    EMPTY = "empty"
    CLOSING = "closing"


@dataclass(frozen=True)
class SnapshotAcquireResult:
    status: SnapshotStatus
    snapshot: object | None = None


@dataclass(frozen=True)
class ManifestScanResult:
    segments: tuple[SegmentRecord, ...] = ()
    invalid_entries: int = 0


class CaptureState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ENDED = "ended"
    CLOSED = "closed"


class CaptureEndReason(str, Enum):
    CLEAN_EOF = "clean_eof"
    PROCESS_EXIT = "process_exit"
    STALLED = "stalled"
    INVALID_INPUT = "invalid_input"
    DISK_PRESSURE = "disk_pressure"
    PERMISSION_DENIED = "permission_denied"
    START_FAILED = "start_failed"
    SHUTDOWN = "shutdown"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INTERNAL_ERROR = "internal_error"


class CapabilityReason(str, Enum):
    MISSING_EXECUTABLE = "missing_executable"
    PROBE_FAILED = "probe_failed"
    UNSUPPORTED_HLS = "unsupported_hls"
    ROOT_UNOWNED = "root_unowned"
    CONFIG_ERROR = "config_error"


@dataclass(frozen=True)
class CaptureOutcome:
    reason: CaptureEndReason
    exit_code: int | None = None
    diagnostic_code: str | None = None
    cleanup_deferred: bool = False


@dataclass(frozen=True)
class CaptureCapability:
    available: bool
    reason: CapabilityReason | None = None
    ffmpeg_executable: str | None = None
    ffmpeg_version: str | None = None


@dataclass(frozen=True)
class CaptureStartResult:
    handle: object | None = None
    outcome: CaptureOutcome | None = None

    @property
    def started(self) -> bool:
        return self.handle is not None


@dataclass(frozen=True)
class CaptureSettings:
    buffer_seconds: int = 120
    buffer_max_bytes: int = 150 * 1024 * 1024
