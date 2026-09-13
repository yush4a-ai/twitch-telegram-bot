from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction


class RenderStatus(str, Enum):
    SUCCESS = "success"
    INVALID_SELECTION = "invalid_selection"
    SNAPSHOT_INVALIDATED = "snapshot_invalidated"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    PROCESS_FAILED = "process_failed"
    TIMEOUT = "timeout"
    OUTPUT_TOO_LARGE = "output_too_large"
    VALIDATION_FAILED = "validation_failed"
    INTERNAL_ERROR = "internal_error"


class RenderCapabilityReason(str, Enum):
    MISSING_EXECUTABLE = "missing_executable"
    PROBE_FAILED = "probe_failed"
    MAJOR_MISMATCH = "major_mismatch"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    STORAGE_UNAVAILABLE = "storage_unavailable"


@dataclass(frozen=True)
class RenderCapability:
    available: bool
    reason: RenderCapabilityReason | None = None
    ffmpeg_executable: str | None = None
    ffprobe_executable: str | None = None
    major_version: int | None = None

    def __post_init__(self) -> None:
        details = (
            self.ffmpeg_executable,
            self.ffprobe_executable,
            self.major_version,
        )
        if self.available and (self.reason is not None or any(x is None for x in details)):
            raise ValueError("available capability requires executable details")
        if not self.available and self.reason is None:
            raise ValueError("unavailable capability requires a reason")


@dataclass(frozen=True)
class RenderConfig:
    width: int = 854
    height: int = 480
    max_output_bytes: int = 16 * 1024 * 1024
    capability_timeout: float = 5.0
    source_probe_timeout: float = 15.0
    encode_timeout: float = 60.0
    output_probe_timeout: float = 10.0
    overall_timeout: float = 90.0
    output_poll_seconds: float = 0.1
    stderr_max_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("render geometry must be integers")
        if (self.width, self.height) != (854, 480):
            raise ValueError("render geometry is fixed at 854x480")
        if (
            type(self.max_output_bytes) is not int
            or not 0 < self.max_output_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("render output cap is invalid")
        timeouts = (
            self.capability_timeout,
            self.source_probe_timeout,
            self.encode_timeout,
            self.output_probe_timeout,
            self.overall_timeout,
        )
        if not all(math.isfinite(value) and value > 0 for value in timeouts):
            raise ValueError("render timeouts must be finite and positive")
        if not math.isfinite(self.output_poll_seconds) or not 0.1 <= self.output_poll_seconds <= 0.25:
            raise ValueError("render output polling interval is invalid")
        if type(self.stderr_max_bytes) is not int or self.stderr_max_bytes <= 0:
            raise ValueError("render stderr cap is invalid")


@dataclass(frozen=True)
class RenderedPreviewMetadata:
    duration_seconds: float
    width: int
    height: int
    fps: Fraction
    size_bytes: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError("rendered duration is invalid")
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("rendered geometry is invalid")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("rendered geometry is invalid")
        if not isinstance(self.fps, Fraction) or self.fps <= 0:
            raise ValueError("rendered cadence is invalid")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError("rendered size is invalid")


DIAGNOSTIC_CODES = frozenset(
    {
        "invalid_selection",
        "invalid_snapshot",
        "source_file_missing",
        "capability_unavailable",
        "ffmpeg_missing",
        "ffmpeg_exit",
        "source_probe_failed",
        "source_fps_unreliable",
        "source_unsupported",
        "render_timeout",
        "output_too_large",
        "invalid_output",
        "storage_unavailable",
        "unexpected_error",
    }
)


@dataclass(frozen=True)
class RenderResult:
    status: RenderStatus
    artifact: object | None = None
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is RenderStatus.SUCCESS and self.artifact is None:
            raise ValueError("SUCCESS requires an artifact")
        if self.status is not RenderStatus.SUCCESS and self.artifact is not None:
            raise ValueError("non-success result cannot expose an artifact")
        if self.diagnostic_code not in DIAGNOSTIC_CODES | {None}:
            raise ValueError("render diagnostic is not allowlisted")
