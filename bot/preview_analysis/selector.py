from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .models import (
    AnalysisBin,
    AnalysisConfig,
    HighlightSelection,
    HighlightWindow,
)


@dataclass(frozen=True)
class _Candidate:
    start_seconds: float
    duration_seconds: float
    score: float
    fingerprint: bytes

    @property
    def center_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds / 2.0


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _median_fingerprint(frames: tuple[bytes, ...]) -> bytes:
    size = len(frames[0])
    if any(len(frame) != size for frame in frames):
        raise ValueError("fingerprint sizes differ")
    return bytes(
        int(statistics.median(frame[index] for frame in frames))
        for index in range(size)
    )


def fingerprint_distance(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    return sum(abs(a - b) for a, b in zip(left, right)) / (len(left) * 255)


def _windowed(
    bins: tuple[AnalysisBin, ...], start: int, duration: int
) -> tuple[AnalysisBin, ...] | None:
    """Contiguous, chronologically-verified slice of ``duration`` bins, or
    ``None`` if the window is short, gapped, or carries non-finite data."""
    selected = bins[start : start + duration]
    if len(selected) != duration:
        return None
    finite_fields = tuple(
        value
        for item in selected
        for value in (
            item.start_seconds,
            item.motion,
            item.audio_spike,
            item.scene_score,
            item.black_ratio,
            item.static_seconds,
            item.visual_coverage,
        )
    )
    if not all(math.isfinite(value) for value in finite_fields):
        return None
    if any(not item.valid for item in selected):
        return None
    if any(item.fingerprint is None for item in selected):
        return None
    if any(
        not math.isclose(item.start_seconds, float(start + offset), abs_tol=1e-6)
        for offset, item in enumerate(selected)
    ):
        return None
    return selected


def _is_safe_window(
    selected: tuple[AnalysisBin, ...], config: AnalysisConfig
) -> bool:
    """Black/freeze/cut/coverage gate shared by highlight scoring and the
    first-preview fallback: a window must clear this before it is usable at
    all, independent of whether it is also "interesting"."""
    black = statistics.fmean(item.black_ratio for item in selected)
    if black > config.black_weighted_limit:
        return False
    if any(item.black_ratio > config.black_second_limit for item in selected):
        return False
    if sum(item.static_seconds for item in selected) >= config.static_overlap_limit:
        return False
    cut_count = sum(item.scene_cut_count for item in selected)
    if cut_count >= config.cut_count_limit:
        return False
    if any(item.visual_coverage < config.visual_min_coverage for item in selected):
        return False
    return True


def _candidate(
    bins: tuple[AnalysisBin, ...], start: int, config: AnalysisConfig
) -> _Candidate | None:
    selected = _windowed(bins, start, config.candidate_duration)
    if selected is None:
        return None
    if not _is_safe_window(selected, config):
        return None
    cut_count = sum(item.scene_cut_count for item in selected)

    motions = [item.motion for item in selected]
    motion = 0.7 * statistics.fmean(motions) + 0.3 * max(motions)
    audio = max(item.audio_spike for item in selected)
    scene = max(item.scene_score for item in selected)
    if (
        motion < config.low_motion_limit
        and audio < config.low_audio_limit
        and scene < config.low_scene_limit
    ):
        return None
    base = (
        config.motion_weight * motion
        + config.audio_weight * audio
        + config.scene_weight * scene
    )
    score = round(
        _clamp(base - config.excess_cut_penalty * max(0, cut_count - 2)), 6
    )
    if score < config.minimum_score:
        return None
    fingerprint = _median_fingerprint(
        tuple(item.fingerprint for item in selected if item.fingerprint is not None)
    )
    return _Candidate(float(start), float(config.candidate_duration), score, fingerprint)


def _collapse_events(candidates: list[_Candidate]) -> list[_Candidate]:
    if not candidates:
        return []
    chronological = sorted(candidates, key=lambda item: item.start_seconds)
    events: list[list[_Candidate]] = [[chronological[0]]]
    for candidate in chronological[1:]:
        if candidate.start_seconds - events[-1][-1].start_seconds >= 3.0:
            events.append([candidate])
        else:
            events[-1].append(candidate)
    return [
        min(event, key=lambda item: (-round(item.score, 6), item.start_seconds))
        for event in events
    ]


def _capacity(duration_seconds: float) -> int:
    if duration_seconds < 30:
        return 0
    if duration_seconds < 60:
        return 1
    if duration_seconds < 75:
        return 2
    return 3


def select_highlights(
    bins: tuple[AnalysisBin, ...],
    duration_seconds: float,
    config: AnalysisConfig,
) -> HighlightSelection:
    capacity = _capacity(duration_seconds)
    if capacity == 0 or not math.isfinite(duration_seconds):
        return HighlightSelection()
    if any(
        not math.isclose(item.start_seconds, float(index), abs_tol=1e-6)
        for index, item in enumerate(bins)
    ):
        return HighlightSelection()

    candidates = [
        candidate
        for start in range(
            1,
            max(1, len(bins) - config.candidate_duration),
            config.candidate_step,
        )
        if (candidate := _candidate(bins, start, config)) is not None
    ]
    ranked = sorted(
        _collapse_events(candidates),
        key=lambda item: (-round(item.score, 6), item.start_seconds),
    )
    accepted: list[_Candidate] = []
    for candidate in ranked:
        if any(
            abs(candidate.center_seconds - existing.center_seconds)
            < config.temporal_nms_seconds
            for existing in accepted
        ):
            continue
        if any(
            fingerprint_distance(candidate.fingerprint, existing.fingerprint)
            < config.duplicate_threshold
            for existing in accepted
        ):
            continue
        accepted.append(candidate)
        if len(accepted) == capacity:
            break

    windows = tuple(
        HighlightWindow(item.start_seconds, item.duration_seconds, item.score)
        for item in sorted(accepted, key=lambda item: item.start_seconds)
    )
    return HighlightSelection(windows)


FALLBACK_DURATION_SECONDS = 5


def select_safe_fallback(
    bins: tuple[AnalysisBin, ...],
    duration_seconds: float,
    config: AnalysisConfig,
) -> HighlightWindow | None:
    """One safe, continuous ``FALLBACK_DURATION_SECONDS`` window for the very
    first preview of a physical stream when :func:`select_highlights` found no
    interesting highlight. Reuses the same black/freeze/cut/coverage safety
    gate as normal candidates (:func:`_is_safe_window`) so a fallback never
    surfaces a black, frozen, or corrupt interval — it only skips the
    interestingness (motion/audio/scene) requirement. Picks the least
    black/cut/static window rather than a random or arbitrary one; ties break
    on the earliest start so the result is deterministic."""
    if not math.isfinite(duration_seconds):
        return None
    if any(
        not math.isclose(item.start_seconds, float(index), abs_tol=1e-6)
        for index, item in enumerate(bins)
    ):
        return None

    best: tuple[float, float, float, float] | None = None
    best_start: int | None = None
    for start in range(0, max(0, len(bins) - FALLBACK_DURATION_SECONDS + 1)):
        selected = _windowed(bins, start, FALLBACK_DURATION_SECONDS)
        if selected is None or not _is_safe_window(selected, config):
            continue
        black = statistics.fmean(item.black_ratio for item in selected)
        static = sum(item.static_seconds for item in selected)
        cuts = float(sum(item.scene_cut_count for item in selected))
        key = (black, cuts, static, float(start))
        if best is None or key < best:
            best = key
            best_start = start

    if best_start is None:
        return None
    return HighlightWindow(float(best_start), float(FALLBACK_DURATION_SECONDS), 0.0)
