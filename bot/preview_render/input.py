from __future__ import annotations

import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.preview_analysis import HighlightWindow
from bot.preview_capture import SegmentRecord


SEGMENT_NAME = re.compile(r"segment-(\d{9})\.ts\Z")


class SelectionValidationError(ValueError):
    pass


class SnapshotValidationError(ValueError):
    pass


class SnapshotBecameInvalid(SnapshotValidationError):
    pass


class SnapshotFileMissing(SnapshotValidationError):
    pass


@dataclass(frozen=True)
class ValidatedSelection:
    windows: tuple[HighlightWindow, ...]
    duration_seconds: float


@dataclass(frozen=True)
class ValidatedRenderSnapshot:
    records: tuple[SegmentRecord, ...]
    duration_seconds: float
    canonical_parent: Path


@dataclass(frozen=True)
class WindowInput:
    start_seconds: float
    duration_seconds: float
    local_start_seconds: float
    records: tuple[SegmentRecord, ...]


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def validate_selection(selection: Any, snapshot_duration: float) -> ValidatedSelection:
    try:
        windows = tuple(selection.windows)
        duration_limit = float(snapshot_duration)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise SelectionValidationError("selection structure is invalid") from exc
    if not math.isfinite(duration_limit) or duration_limit <= 0:
        raise SelectionValidationError("snapshot duration is invalid")
    if not 1 <= len(windows) <= 3:
        raise SelectionValidationError("selection must contain one to three windows")
    previous_start: float | None = None
    previous_end: float | None = None
    total = 0.0
    for window in windows:
        try:
            start = float(window.start_seconds)
            duration = float(window.duration_seconds)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise SelectionValidationError("highlight window is invalid") from exc
        if not math.isfinite(start) or not math.isfinite(duration):
            raise SelectionValidationError("highlight window must be finite")
        if start < 0 or duration <= 0:
            raise SelectionValidationError("highlight range is invalid")
        end = math.fsum((start, duration))
        if not math.isfinite(end) or end > duration_limit:
            raise SelectionValidationError("highlight exceeds snapshot")
        if previous_start is not None and start <= previous_start:
            raise SelectionValidationError("highlight starts are not increasing")
        if previous_end is not None and start < previous_end:
            raise SelectionValidationError("highlight windows overlap")
        previous_start = start
        previous_end = end
        total = math.fsum((total, duration))
    return ValidatedSelection(windows, total)


def validate_snapshot(snapshot: Any) -> ValidatedRenderSnapshot:
    try:
        snapshot.ensure_valid()
    except Exception as exc:
        raise SnapshotBecameInvalid("snapshot invalidated") from exc
    try:
        records = tuple(snapshot.records)
    except (AttributeError, TypeError) as exc:
        raise SnapshotValidationError("snapshot records are invalid") from exc
    if not records:
        raise SnapshotValidationError("snapshot has no records")
    previous_sequence: int | None = None
    canonical_parent: Path | None = None
    durations: list[float] = []
    for record in records:
        if not isinstance(record, SegmentRecord):
            raise SnapshotValidationError("unexpected snapshot record")
        if type(record.sequence) is not int or (
            previous_sequence is not None and record.sequence <= previous_sequence
        ):
            raise SnapshotValidationError("segment sequence is not increasing")
        previous_sequence = record.sequence
        match = SEGMENT_NAME.fullmatch(record.path.name)
        if match is None or int(match.group(1)) != record.sequence:
            raise SnapshotValidationError("segment name does not match sequence")
        if (
            not math.isfinite(record.duration_seconds)
            or record.duration_seconds <= 0
        ):
            raise SnapshotValidationError("segment duration is invalid")
        if type(record.size_bytes) is not int or record.size_bytes <= 0:
            raise SnapshotValidationError("segment size is invalid")
        try:
            info = record.path.lstat()
        except FileNotFoundError as exc:
            raise SnapshotFileMissing("segment is unavailable") from exc
        except OSError as exc:
            raise SnapshotValidationError("segment cannot be inspected") from exc
        if not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(record.path, info):
            raise SnapshotValidationError("segment is not an owned regular file")
        if info.st_size != record.size_bytes:
            raise SnapshotValidationError("segment size changed")
        try:
            resolved = record.path.resolve(strict=True)
            parent = resolved.parent.resolve(strict=True)
        except FileNotFoundError as exc:
            raise SnapshotFileMissing("segment disappeared") from exc
        except OSError as exc:
            raise SnapshotValidationError("segment cannot be resolved") from exc
        if canonical_parent is None:
            canonical_parent = parent
        elif parent != canonical_parent:
            raise SnapshotValidationError("segments have different parents")
        durations.append(record.duration_seconds)
    duration = math.fsum(durations)
    try:
        reported = float(snapshot.actual_duration_seconds)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise SnapshotValidationError("reported duration is invalid") from exc
    if not math.isfinite(reported) or not math.isclose(
        duration, reported, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise SnapshotValidationError("reported duration changed")
    assert canonical_parent is not None
    return ValidatedRenderSnapshot(records, duration, canonical_parent)


def plan_window_inputs(
    snapshot: ValidatedRenderSnapshot,
    selection: ValidatedSelection,
) -> tuple[WindowInput, ...]:
    boundaries = [0.0]
    for index in range(len(snapshot.records)):
        boundaries.append(
            math.fsum(record.duration_seconds for record in snapshot.records[: index + 1])
        )
    plans: list[WindowInput] = []
    for window in selection.windows:
        start = float(window.start_seconds)
        duration = float(window.duration_seconds)
        end = math.fsum((start, duration))
        indices = tuple(
            index
            for index in range(len(snapshot.records))
            if boundaries[index + 1] > start and boundaries[index] < end
        )
        if not indices:
            raise SelectionValidationError("highlight has no covering segments")
        first = indices[0]
        plans.append(
            WindowInput(
                start_seconds=start,
                duration_seconds=duration,
                local_start_seconds=start - boundaries[first],
                records=tuple(snapshot.records[index] for index in indices),
            )
        )
    return tuple(plans)


def _quote_path(path: Path) -> str:
    absolute = path.resolve(strict=True).as_posix()
    return "'" + absolute.replace("'", "'\\''") + "'"


def write_window_manifest(plan: WindowInput, path: Path) -> Path:
    lines = ["ffconcat version 1.0"]
    for record in plan.records:
        lines.append(f"file {_quote_path(record.path)}")
        lines.append(f"duration {format(record.duration_seconds, '.17g')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
