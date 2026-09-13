from __future__ import annotations

import math
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.preview_capture import SegmentRecord


SEGMENT_NAME = re.compile(r"segment-(\d{9})\.ts\Z")
JOB_NAME = re.compile(r"analysis-[0-9a-f]{32}\Z")
OWNERSHIP_MARKER = ".preview-analysis-owned"
OWNERSHIP_TOKEN = "twitch-signalbot-preview-analysis-v1\n"
ROOT_MARKER = ".preview-analysis-root"
ROOT_TOKEN = "twitch-signalbot-preview-analysis-root-v1\n"


class SnapshotValidationError(ValueError):
    pass


class SnapshotBecameInvalid(SnapshotValidationError):
    pass


@dataclass(frozen=True)
class ValidatedSnapshot:
    records: tuple[SegmentRecord, ...]
    duration_seconds: float
    canonical_parent: Path


def _is_reparse_or_symlink(path: Path, file_stat: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _safe_directory(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not _is_reparse_or_symlink(path, info)
            and path.resolve(strict=True).parent == parent.resolve(strict=True)
        )
    except (OSError, RuntimeError):
        return False


def _safe_regular(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISREG(info.st_mode)
            and not _is_reparse_or_symlink(path, info)
            and path.resolve(strict=True).parent == parent.resolve(strict=True)
        )
    except (OSError, RuntimeError):
        return False


def _has_valid_root_marker(root: Path) -> bool:
    marker = root / ROOT_MARKER
    if not _safe_regular(marker, root):
        return False
    try:
        return marker.read_text(encoding="utf-8") == ROOT_TOKEN
    except (OSError, UnicodeError):
        return False


def validate_snapshot(snapshot: Any) -> ValidatedSnapshot:
    try:
        snapshot.ensure_valid()
    except Exception as exc:
        raise SnapshotBecameInvalid("snapshot invalidated") from exc

    records = tuple(snapshot.records)
    if not records:
        raise SnapshotValidationError("snapshot has no records")

    previous_sequence: int | None = None
    canonical_parent: Path | None = None
    duration = 0.0
    for record in records:
        if not isinstance(record, SegmentRecord):
            raise SnapshotValidationError("unexpected snapshot record")
        if previous_sequence is not None and record.sequence <= previous_sequence:
            raise SnapshotValidationError("segment sequence is not increasing")
        previous_sequence = record.sequence
        match = SEGMENT_NAME.fullmatch(record.path.name)
        if match is None or int(match.group(1)) != record.sequence:
            raise SnapshotValidationError("segment name does not match sequence")
        if not math.isfinite(record.duration_seconds) or record.duration_seconds <= 0:
            raise SnapshotValidationError("segment duration is invalid")
        try:
            file_stat = record.path.lstat()
        except (FileNotFoundError, OSError) as exc:
            raise SnapshotValidationError("segment is unavailable") from exc
        if _is_reparse_or_symlink(record.path, file_stat):
            raise SnapshotValidationError("segment cannot be a link")
        if not stat.S_ISREG(file_stat.st_mode):
            raise SnapshotValidationError("segment is not a regular file")
        if file_stat.st_size != record.size_bytes:
            raise SnapshotValidationError("segment size changed")
        try:
            resolved = record.path.resolve(strict=True)
            parent = resolved.parent.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise SnapshotValidationError("segment cannot be resolved") from exc
        if canonical_parent is None:
            canonical_parent = parent
        elif parent != canonical_parent:
            raise SnapshotValidationError("segments have different parents")
        duration += record.duration_seconds

    if not math.isfinite(duration) or duration <= 0:
        raise SnapshotValidationError("snapshot duration is invalid")
    if not math.isfinite(float(snapshot.actual_duration_seconds)):
        raise SnapshotValidationError("reported snapshot duration is invalid")
    if not math.isclose(
        duration,
        float(snapshot.actual_duration_seconds),
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise SnapshotValidationError("reported snapshot duration changed")
    assert canonical_parent is not None
    return ValidatedSnapshot(records, duration, canonical_parent)


def _quote_concat_path(path: Path) -> str:
    absolute = path.resolve(strict=True).as_posix()
    return "'" + absolute.replace("'", "'\\''") + "'"


@dataclass(frozen=True)
class AnalysisJob:
    path: Path
    root: Path

    @property
    def marker_path(self) -> Path:
        return self.path / OWNERSHIP_MARKER

    @property
    def manifest_path(self) -> Path:
        return self.path / "snapshot.ffconcat"

    @property
    def fingerprint_path(self) -> Path:
        return self.path / "fingerprints.gray"

    def cleanup(self) -> bool:
        try:
            resolved_root = self.root.resolve(strict=True)
            resolved_job = self.path.resolve(strict=True)
            if not _has_valid_root_marker(resolved_root):
                return False
            if resolved_job.parent != resolved_root:
                return False
            if not _safe_directory(resolved_job, resolved_root):
                return False
            if JOB_NAME.fullmatch(resolved_job.name) is None:
                return False
            marker_stat = self.marker_path.lstat()
            if _is_reparse_or_symlink(self.marker_path, marker_stat):
                return False
            if not stat.S_ISREG(marker_stat.st_mode):
                return False
            if self.marker_path.read_text(encoding="utf-8") != OWNERSHIP_TOKEN:
                return False
        except (FileNotFoundError, OSError, UnicodeError):
            return False
        shutil.rmtree(resolved_job)
        return True


class AnalysisTempManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else Path(tempfile.gettempdir()) / "twitch-signalbot-preview-analysis"

    def create_job(self) -> AnalysisJob:
        if self.root.exists() or self.root.is_symlink():
            if not _safe_directory(self.root, self.root.parent):
                raise PermissionError("analysis root is not a safe directory")
            if not _has_valid_root_marker(self.root):
                raise PermissionError("analysis root is not owned")
        else:
            try:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=False)
                marker = self.root / ROOT_MARKER
                with marker.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(ROOT_TOKEN)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise PermissionError("analysis root initialization failed") from exc
        job_path = self.root / f"analysis-{uuid.uuid4().hex}"
        job_path.mkdir(mode=0o700)
        job = AnalysisJob(job_path, self.root)
        job.marker_path.write_text(OWNERSHIP_TOKEN, encoding="utf-8", newline="\n")
        return job


def write_concat_manifest(
    snapshot: ValidatedSnapshot, job: AnalysisJob
) -> Path:
    lines = ["ffconcat version 1.0"]
    for record in snapshot.records:
        lines.append(f"file {_quote_concat_path(record.path)}")
        lines.append(f"duration {format(record.duration_seconds, '.17g')}")
    job.manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return job.manifest_path
