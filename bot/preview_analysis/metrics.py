from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

from .models import AnalysisBin, AnalysisConfig


class MetricParseError(ValueError):
    pass


@dataclass(frozen=True)
class VisualSample:
    timestamp: float
    ydiff: float
    scene_score: float = 0.0
    black_ratio: float = 0.0


@dataclass(frozen=True)
class AudioSample:
    timestamp: float
    rms_db: float
    peak_db: float


@dataclass(frozen=True)
class FreezeInterval:
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class VisualMetrics:
    samples: tuple[VisualSample, ...] = ()
    freezes: tuple[FreezeInterval, ...] = ()


@dataclass(frozen=True)
class AudioMetrics:
    samples: tuple[AudioSample, ...] = ()


def _finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise MetricParseError("metric is not numeric") from exc
    if not math.isfinite(parsed):
        raise MetricParseError("metric is not finite")
    return parsed


def _db_float(value: str) -> float:
    if value.strip().lower() == "-inf":
        return -90.0
    return _finite_float(value)


class _MetadataParser:
    def __init__(self, max_records: int) -> None:
        self.max_records = max_records
        self._current_timestamp: float | None = None
        self._records: dict[float, dict[str, str]] = {}
        self._bad_chronology = False
        self._last_header_timestamp: float | None = None

    def feed_line(self, line: str) -> None:
        if any(ord(character) < 32 and character not in "\t" for character in line):
            raise MetricParseError("metadata contains control bytes")
        stripped = line.strip()
        if not stripped:
            return
        if stripped.startswith("frame:"):
            marker = "pts_time:"
            if marker not in stripped:
                raise MetricParseError("metadata frame has no timestamp")
            value = stripped.split(marker, 1)[1].split()[0]
            timestamp = _finite_float(value)
            if timestamp < 0:
                self._bad_chronology = True
            if self._last_header_timestamp is not None and timestamp < self._last_header_timestamp:
                self._bad_chronology = True
            self._last_header_timestamp = timestamp
            self._current_timestamp = timestamp
            self._records.setdefault(timestamp, {})
            return
        if "=" not in stripped or self._current_timestamp is None:
            raise MetricParseError("malformed metadata line")
        key, value = stripped.split("=", 1)
        self._records[self._current_timestamp][key] = value

    def _validated_items(self) -> list[tuple[float, dict[str, str]]]:
        if self._bad_chronology or len(self._records) > self.max_records:
            raise MetricParseError("metadata chronology or count is invalid")
        items = list(self._records.items())
        if any(left[0] >= right[0] for left, right in zip(items, items[1:])):
            raise MetricParseError("metadata timestamps are not increasing")
        return items


class VisualMetadataParser(_MetadataParser):
    def finish(self) -> VisualMetrics:
        samples: list[VisualSample] = []
        freezes: list[FreezeInterval] = []
        freeze_start: float | None = None
        for timestamp, values in self._validated_items():
            if "lavfi.signalstats.YDIF" in values:
                ydiff = _finite_float(values["lavfi.signalstats.YDIF"])
                scene = _finite_float(values.get("lavfi.scd.score", "0"))
                black = _finite_float(
                    values.get("lavfi.blackframe.pblack", "0")
                ) / 100.0
                if ydiff < 0 or scene < 0 or not 0 <= black <= 1:
                    raise MetricParseError("visual metric is outside range")
                samples.append(VisualSample(timestamp, ydiff, scene, black))
            if "lavfi.freezedetect.freeze_start" in values:
                start = _finite_float(values["lavfi.freezedetect.freeze_start"])
                if freeze_start is not None:
                    raise MetricParseError("overlapping freeze intervals")
                freeze_start = start
            if "lavfi.freezedetect.freeze_end" in values:
                end = _finite_float(values["lavfi.freezedetect.freeze_end"])
                if freeze_start is None or end < freeze_start:
                    raise MetricParseError("invalid freeze interval")
                freezes.append(FreezeInterval(freeze_start, end))
                freeze_start = None
        if freeze_start is not None:
            freezes.append(FreezeInterval(freeze_start, math.inf))
        return VisualMetrics(tuple(samples), tuple(freezes))


class AudioMetadataParser(_MetadataParser):
    def finish(self) -> AudioMetrics:
        samples: list[AudioSample] = []
        for timestamp, values in self._validated_items():
            rms = values.get("lavfi.astats.Overall.RMS_level")
            peak = values.get("lavfi.astats.Overall.Peak_level")
            if rms is None and peak is None:
                continue
            if rms is None or peak is None:
                raise MetricParseError("audio record is incomplete")
            samples.append(AudioSample(timestamp, _db_float(rms), _db_float(peak)))
        return AudioMetrics(tuple(samples))


def read_fingerprints(
    path: Path, duration_seconds: float, config: AnalysisConfig
) -> tuple[bytes, ...]:
    expected_frames = math.ceil(duration_seconds)
    frame_size = config.fingerprint_frame_bytes
    expected_bytes = expected_frames * frame_size
    try:
        actual_bytes = path.stat().st_size
    except (FileNotFoundError, OSError) as exc:
        raise MetricParseError("fingerprint file is unavailable") from exc
    if actual_bytes > expected_bytes + config.fingerprint_size_tolerance:
        raise MetricParseError("fingerprint file exceeded cap")
    if actual_bytes != expected_bytes or actual_bytes % frame_size:
        raise MetricParseError("fingerprint file is truncated or misaligned")
    data = path.read_bytes()
    if len(data) != actual_bytes:
        raise MetricParseError("fingerprint file changed while reading")
    return tuple(
        data[offset : offset + frame_size]
        for offset in range(0, len(data), frame_size)
    )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def normalize_motion_bins(
    bins: tuple[AnalysisBin, ...], config: AnalysisConfig
) -> tuple[AnalysisBin, ...]:
    reference = [
        item.motion_raw
        for item in bins
        if item.valid
        and item.black_ratio <= config.black_weighted_limit
        and item.static_seconds < config.static_overlap_limit
        and math.isfinite(item.motion_raw)
    ]
    if not reference:
        return bins
    low = _percentile(reference, config.relative_percentile_low)
    high = _percentile(reference, config.relative_percentile_high)
    spread = high - low
    normalized: list[AnalysisBin] = []
    for item in bins:
        if not item.valid or not math.isfinite(item.motion_raw):
            normalized.append(replace(item, valid=False, motion=0.0))
            continue
        relative = 0.0 if spread <= 1e-9 else _clamp((item.motion_raw - low) / spread)
        absolute = _clamp(
            (item.motion_raw - config.motion_absolute_low)
            / (config.motion_absolute_high - config.motion_absolute_low)
        )
        normalized.append(
            replace(item, motion=max(relative, config.motion_absolute_weight * absolute))
        )
    return tuple(normalized)


def _freeze_overlap(
    start: float, end: float, intervals: tuple[FreezeInterval, ...], duration: float
) -> float:
    overlap = 0.0
    for interval in intervals:
        interval_end = min(interval.end_seconds, duration)
        overlap += max(0.0, min(end, interval_end) - max(start, interval.start_seconds))
    return min(end - start, overlap)


def _audio_value_by_bin(
    samples: tuple[AudioSample, ...], full_bins: int
) -> list[tuple[float, float] | None]:
    grouped: list[list[AudioSample]] = [[] for _ in range(full_bins)]
    previous_timestamp = -1.0
    for sample in samples:
        if not all(math.isfinite(value) for value in (sample.timestamp, sample.rms_db, sample.peak_db)):
            raise MetricParseError("audio metric is not finite")
        if sample.timestamp < 0 or sample.timestamp <= previous_timestamp:
            raise MetricParseError("audio timestamps are not increasing")
        previous_timestamp = sample.timestamp
        index = math.floor(sample.timestamp)
        if 0 <= index < full_bins:
            grouped[index].append(sample)
    values: list[tuple[float, float] | None] = []
    for items in grouped:
        if not items:
            values.append(None)
        else:
            values.append(
                (
                    statistics.median(item.rms_db for item in items),
                    statistics.median(item.peak_db for item in items),
                )
            )
    return values


def _delta_to_spike(delta: float, config: AnalysisConfig) -> float:
    if delta <= config.audio_delta_low_db:
        return 0.0
    if delta >= config.audio_delta_high_db:
        return 1.0
    return (delta - config.audio_delta_low_db) / (
        config.audio_delta_high_db - config.audio_delta_low_db
    )


def _audio_spikes(
    samples: tuple[AudioSample, ...], full_bins: int, config: AnalysisConfig
) -> list[tuple[float | None, float | None, float]]:
    if not samples:
        return [(None, None, 0.0) for _ in range(full_bins)]
    values = _audio_value_by_bin(samples, full_bins)
    valid_rms = [item[0] for item in values if item is not None]
    valid_peak = [item[1] for item in values if item is not None]
    output: list[tuple[float | None, float | None, float]] = []
    for index, current in enumerate(values):
        if current is None:
            output.append((None, None, 0.0))
            continue
        neighbors = [
            item
            for offset, item in enumerate(values)
            if item is not None
            and offset != index
            and abs(offset - index) <= config.audio_neighbor_radius
        ]
        rms_base = statistics.median(item[0] for item in neighbors) if len(neighbors) >= 2 else statistics.median(valid_rms)
        peak_base = statistics.median(item[1] for item in neighbors) if len(neighbors) >= 2 else statistics.median(valid_peak)
        rms_spike = _delta_to_spike(max(0.0, current[0] - rms_base), config)
        peak_spike = _delta_to_spike(max(0.0, current[1] - peak_base), config)
        output.append((current[0], current[1], 0.8 * rms_spike + 0.2 * peak_spike))
    return output


def build_analysis_bins(
    duration_seconds: float,
    visual: VisualMetrics,
    audio: AudioMetrics,
    fingerprints: tuple[bytes, ...],
    config: AnalysisConfig,
) -> tuple[AnalysisBin, ...]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise MetricParseError("snapshot duration is invalid")
    full_bins = math.floor(duration_seconds)
    if len(fingerprints) < full_bins:
        raise MetricParseError("fingerprints do not cover timeline")
    grouped: list[list[VisualSample]] = [[] for _ in range(full_bins)]
    previous = -1.0
    for sample in visual.samples:
        values = (sample.timestamp, sample.ydiff, sample.scene_score, sample.black_ratio)
        if not all(math.isfinite(value) for value in values):
            raise MetricParseError("visual metric is not finite")
        if sample.timestamp < 0 or sample.timestamp <= previous:
            raise MetricParseError("visual timestamps are not increasing")
        previous = sample.timestamp
        if sample.ydiff < 0 or sample.scene_score < 0 or not 0 <= sample.black_ratio <= 1:
            raise MetricParseError("visual metric is outside range")
        index = math.floor(sample.timestamp)
        if 0 <= index < full_bins:
            grouped[index].append(sample)

    audio_values = _audio_spikes(audio.samples, full_bins, config)
    bins: list[AnalysisBin] = []
    for index, samples in enumerate(grouped):
        coverage = min(1.0, len(samples) / config.analysis_fps)
        motion_samples = [
            sample.ydiff
            for sample in samples
            if sample.scene_score < config.scene_cut_threshold
        ]
        valid = (
            coverage >= config.visual_min_coverage
            and bool(motion_samples)
            and (not audio.samples or audio_values[index][0] is not None)
        )
        motion_raw = statistics.median(motion_samples) if motion_samples else 0.0
        scene_raw = max((sample.scene_score for sample in samples), default=0.0)
        scene = _clamp(
            (scene_raw - config.scene_normalization_low)
            / (config.scene_normalization_high - config.scene_normalization_low)
        )
        cuts = sum(
            sample.scene_score >= config.scene_cut_threshold for sample in samples
        )
        black = statistics.fmean(sample.black_ratio for sample in samples) if samples else 0.0
        static = _freeze_overlap(
            float(index), float(index + 1), visual.freezes, duration_seconds
        )
        rms, peak, spike = audio_values[index]
        fingerprint = fingerprints[index]
        if len(fingerprint) != config.fingerprint_frame_bytes:
            raise MetricParseError("fingerprint frame is malformed")
        bins.append(
            AnalysisBin(
                start_seconds=float(index),
                motion_raw=motion_raw,
                rms_db=rms,
                peak_db=peak,
                audio_spike=spike,
                scene_score=scene,
                scene_cut_count=cuts,
                black_ratio=black,
                static_seconds=static,
                visual_coverage=coverage,
                valid=valid,
                fingerprint=fingerprint,
            )
        )
    return normalize_motion_bins(tuple(bins), config)
