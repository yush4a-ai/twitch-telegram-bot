from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum


class AnalysisStatus(str, Enum):
    SUCCESS = "success"
    NO_SELECTION = "no_selection"
    SNAPSHOT_TOO_SHORT = "snapshot_too_short"
    SNAPSHOT_INVALIDATED = "snapshot_invalidated"
    PROCESS_FAILED = "process_failed"
    TIMEOUT = "timeout"
    MALFORMED_METRICS = "malformed_metrics"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class HighlightWindow:
    start_seconds: float
    duration_seconds: float
    score: float

    def __post_init__(self) -> None:
        values = (self.start_seconds, self.duration_seconds, self.score)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("highlight values must be finite")
        if self.start_seconds < 0 or self.duration_seconds <= 0:
            raise ValueError("highlight range is invalid")
        if not 0 <= self.score <= 1:
            raise ValueError("highlight score is invalid")


@dataclass(frozen=True)
class HighlightSelection:
    windows: tuple[HighlightWindow, ...] = ()

    def __post_init__(self) -> None:
        if len(self.windows) > 3:
            raise ValueError("at most three highlight windows are allowed")
        if any(
            left.start_seconds >= right.start_seconds
            for left, right in zip(self.windows, self.windows[1:])
        ):
            raise ValueError("highlight windows must be chronological")


DIAGNOSTIC_CODES = frozenset(
    {
        "empty_snapshot",
        "invalid_snapshot",
        "missing_segment",
        "ffmpeg_missing",
        "ffmpeg_exit",
        "output_limit",
        "invalid_metadata",
        "invalid_fingerprint",
        "analysis_timeout",
        "unexpected_error",
    }
)


@dataclass(frozen=True)
class AnalysisResult:
    status: AnalysisStatus
    selection: HighlightSelection = HighlightSelection()
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        has_windows = bool(self.selection.windows)
        if self.status is AnalysisStatus.SUCCESS and not has_windows:
            raise ValueError("SUCCESS requires at least one window")
        if self.status is not AnalysisStatus.SUCCESS and has_windows:
            raise ValueError("non-success results cannot contain windows")
        if self.diagnostic_code not in DIAGNOSTIC_CODES | {None}:
            raise ValueError("diagnostic code is not allowlisted")


@dataclass(frozen=True)
class AnalysisConfig:
    analysis_fps: int = 10
    analysis_width: int = 320
    analysis_height: int = 180
    fingerprint_width: int = 16
    fingerprint_height: int = 9
    fingerprint_size_tolerance: int = 144
    candidate_duration: int = 3
    candidate_step: int = 1
    pass_timeout: float = 45.0
    overall_timeout: float = 60.0
    metadata_line_max_bytes: int = 4 * 1024
    metadata_total_max_bytes: int = 2 * 1024 * 1024
    visual_record_tolerance: int = 20
    audio_record_tolerance: int = 2
    visual_min_coverage: float = 0.70
    scene_cut_threshold: float = 10.0
    scene_normalization_low: float = 8.0
    scene_normalization_high: float = 20.0
    freeze_noise_db: float = -50.0
    freeze_duration: float = 1.0
    relative_percentile_low: float = 0.20
    relative_percentile_high: float = 0.90
    motion_absolute_low: float = 1.0
    motion_absolute_high: float = 12.0
    motion_absolute_weight: float = 0.60
    motion_weight: float = 0.55
    audio_weight: float = 0.30
    scene_weight: float = 0.15
    excess_cut_penalty: float = 0.12
    minimum_score: float = 0.34
    temporal_nms_seconds: float = 12.0
    duplicate_threshold: float = 0.04
    black_weighted_limit: float = 0.20
    black_second_limit: float = 0.80
    static_overlap_limit: float = 1.5
    cut_count_limit: int = 5
    low_motion_limit: float = 0.20
    low_audio_limit: float = 0.25
    low_scene_limit: float = 0.10
    audio_delta_low_db: float = 3.0
    audio_delta_high_db: float = 12.0
    audio_neighbor_radius: int = 5
    min_snapshot_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(float(value))
            for value in self.__dict__.values()
        ):
            raise ValueError("analysis configuration must be finite")
        positive = (
            self.analysis_fps,
            self.analysis_width,
            self.analysis_height,
            self.fingerprint_width,
            self.fingerprint_height,
            self.candidate_duration,
            self.candidate_step,
            self.pass_timeout,
            self.overall_timeout,
            self.metadata_line_max_bytes,
            self.metadata_total_max_bytes,
        )
        if not all(value > 0 for value in positive):
            raise ValueError("analysis configuration values must be positive")
        if any(
            value < 0
            for value in (
                self.fingerprint_size_tolerance,
                self.visual_record_tolerance,
                self.audio_record_tolerance,
                self.audio_neighbor_radius,
            )
        ):
            raise ValueError("analysis tolerances cannot be negative")
        fractions = (
            self.visual_min_coverage,
            self.motion_absolute_weight,
            self.minimum_score,
            self.duplicate_threshold,
            self.black_weighted_limit,
            self.black_second_limit,
            self.relative_percentile_low,
            self.relative_percentile_high,
        )
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in fractions):
            raise ValueError("analysis fractions must be finite and in range")
        ordered_ranges = (
            (self.relative_percentile_low, self.relative_percentile_high),
            (self.audio_delta_low_db, self.audio_delta_high_db),
            (self.scene_normalization_low, self.scene_normalization_high),
            (self.motion_absolute_low, self.motion_absolute_high),
        )
        if any(low >= high for low, high in ordered_ranges):
            raise ValueError("analysis ranges must be strictly increasing")

    @property
    def fingerprint_frame_bytes(self) -> int:
        return self.fingerprint_width * self.fingerprint_height


@dataclass(frozen=True)
class AnalysisBin:
    start_seconds: float
    motion_raw: float = 0.0
    motion: float = 0.0
    rms_db: float | None = None
    peak_db: float | None = None
    audio_spike: float = 0.0
    scene_score: float = 0.0
    scene_cut_count: int = 0
    black_ratio: float = 0.0
    static_seconds: float = 0.0
    visual_coverage: float = 0.0
    valid: bool = False
    fingerprint: bytes | None = None

    def updated(self, **changes: object) -> AnalysisBin:
        return replace(self, **changes)
