from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import RenderedPreviewMetadata


ROOT_MARKER = ".preview-render-root"
ROOT_TOKEN = "twitch-signalbot-preview-render-root-v1\n"
JOB_MARKER = ".preview-render-job.json"
JOB_NAME = re.compile(r"render-([0-9a-f]{32})\Z")
ORPHAN_TTL_SECONDS = 6 * 60 * 60
DEFAULT_ROOT = Path(tempfile.gettempdir()) / "twitch-signalbot-preview-render"
_APPLICATION = "twitch-signalbot-preview-render"
_SCHEMA = 1


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def _safe_directory(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISDIR(info.st_mode)
            and not _is_link_or_reparse(path, info)
            and path.resolve(strict=True).parent == parent.resolve(strict=True)
        )
    except (OSError, RuntimeError):
        return False


def _safe_regular(path: Path, parent: Path) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISREG(info.st_mode)
            and not _is_link_or_reparse(path, info)
            and path.resolve(strict=True).parent == parent.resolve(strict=True)
        )
    except (OSError, RuntimeError):
        return False


def _valid_root(root: Path) -> bool:
    marker = root / ROOT_MARKER
    if not _safe_regular(marker, root):
        return False
    try:
        return marker.read_text(encoding="utf-8") == ROOT_TOKEN
    except (OSError, UnicodeError):
        return False


def _read_job_marker(path: Path, expected_id: str) -> dict[str, object] | None:
    marker = path / JOB_MARKER
    if not _safe_regular(marker, path):
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if (
        payload.get("application") != _APPLICATION
        or payload.get("schema") != _SCHEMA
        or payload.get("job_id") != expected_id
        or type(payload.get("updated_unix")) not in {int, float}
    ):
        return None
    updated = float(payload["updated_unix"])
    if not (updated == updated and abs(updated) != float("inf")):
        return None
    return payload


@dataclass(frozen=True)
class RenderJob:
    path: Path
    root: Path
    job_id: str
    clock: Callable[[], float]

    @property
    def marker_path(self) -> Path:
        return self.path / JOB_MARKER

    @property
    def temporary_path(self) -> Path:
        return self.path / "preview.tmp.mp4"

    @property
    def final_path(self) -> Path:
        return self.path / "preview.mp4"

    def manifest_path(self, index: int) -> Path:
        return self.path / f"window-{index}.ffconcat"

    def touch(self) -> None:
        payload = {
            "application": _APPLICATION,
            "schema": _SCHEMA,
            "job_id": self.job_id,
            "updated_unix": float(self.clock()),
        }
        temporary = self.path / f"{JOB_MARKER}.tmp"
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.marker_path)

    def cleanup(self) -> bool:
        try:
            resolved_root = self.root.resolve(strict=True)
            resolved_job = self.path.resolve(strict=True)
            if not _valid_root(resolved_root):
                return False
            if resolved_job.parent != resolved_root:
                return False
            if not _safe_directory(resolved_job, resolved_root):
                return False
            match = JOB_NAME.fullmatch(resolved_job.name)
            if match is None or match.group(1) != self.job_id:
                return False
            if _read_job_marker(resolved_job, self.job_id) is None:
                return False
        except (FileNotFoundError, OSError, RuntimeError):
            return False
        try:
            shutil.rmtree(resolved_job)
        except (FileNotFoundError, OSError):
            return False
        return True


class RenderStorage:
    def __init__(
        self,
        root: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self._clock = clock

    def _initialize(self) -> None:
        if self.root.exists() or self.root.is_symlink():
            if not _safe_directory(self.root, self.root.parent):
                raise PermissionError("render root is not a safe directory")
            if not _valid_root(self.root):
                raise PermissionError("render root is not owned")
            return
        try:
            self.root.mkdir(parents=True, mode=0o700, exist_ok=False)
            marker = self.root / ROOT_MARKER
            with marker.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(ROOT_TOKEN)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise PermissionError("render root initialization failed") from exc

    def create_job(self) -> RenderJob:
        self._initialize()
        job_id = uuid.uuid4().hex
        path = self.root / f"render-{job_id}"
        path.mkdir(mode=0o700)
        job = RenderJob(path, self.root, job_id, self._clock)
        try:
            job.touch()
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return job

    def cleanup_orphans(self) -> int:
        try:
            self._initialize()
            children = tuple(self.root.iterdir())
            now = float(self._clock())
        except (OSError, PermissionError, ValueError, OverflowError):
            return 0
        removed = 0
        for child in children:
            match = JOB_NAME.fullmatch(child.name)
            if match is None or not _safe_directory(child, self.root):
                continue
            payload = _read_job_marker(child, match.group(1))
            if payload is None:
                continue
            if now - float(payload["updated_unix"]) <= ORPHAN_TTL_SECONDS:
                continue
            job = RenderJob(child, self.root, match.group(1), self._clock)
            if job.cleanup():
                removed += 1
        return removed


class RenderedPreview:
    def __init__(self, job: RenderJob, metadata: RenderedPreviewMetadata) -> None:
        self._job = job
        self._released = False
        self.path = job.final_path
        self.duration_seconds = metadata.duration_seconds
        self.width = metadata.width
        self.height = metadata.height
        self.fps = metadata.fps
        self.size_bytes = metadata.size_bytes

    @classmethod
    def from_job(
        cls, job: RenderJob, metadata: RenderedPreviewMetadata
    ) -> RenderedPreview:
        try:
            info = job.final_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or _is_link_or_reparse(job.final_path, info)
                or info.st_size != metadata.size_bytes
            ):
                raise ValueError("rendered artifact changed")
        except OSError as exc:
            raise ValueError("rendered artifact is unavailable") from exc
        return cls(job, metadata)

    def release(self) -> bool:
        if self._released:
            return False
        if not self._job.cleanup():
            return False
        self._released = True
        return True
