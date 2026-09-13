from __future__ import annotations

import asyncio
import math
import os
import re
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import (
    FileCaptureInput,
    ManifestScanResult,
    SegmentRecord,
    SnapshotAcquireResult,
    SnapshotStatus,
    UrlCaptureInput,
)


SEGMENT_TARGET_SECONDS = 2
HLS_LIST_SIZE = 128
MAX_COMPLETE_FILES = 128
PARTIAL_FILE_MAX_BYTES = 32 * 1024 * 1024
ROOT_HARD_MAX_BYTES = 384 * 1024 * 1024
FREE_DISK_RESERVE_BYTES = 256 * 1024 * 1024
_SEGMENT_NAME = re.compile(r"segment-\d{9}\.ts\Z")
_REPARSE_POINT = 0x400


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT)


def is_owned_regular_file(path: Path, owned_dir: Path) -> bool:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        return path.parent.resolve() == owned_dir.resolve()
    except (FileNotFoundError, OSError, RuntimeError):
        return False


def _is_basic_mpeg_ts(path: Path, size: int) -> bool:
    if size < 188 or size % 188:
        return False
    try:
        with path.open("rb") as stream:
            sample = stream.read(min(size, 188 * 10))
    except OSError:
        return False
    return all(sample[offset] == 0x47 for offset in range(0, len(sample), 188))


def build_hls_argv(
    ffmpeg_executable: str,
    source: UrlCaptureInput | FileCaptureInput,
    owned_dir: Path,
) -> tuple[str, ...]:
    if isinstance(source, UrlCaptureInput):
        input_value = source.url
    elif isinstance(source, FileCaptureInput):
        input_value = str(source.path)
    else:
        raise TypeError("unsupported capture input")
    return (
        ffmpeg_executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        input_value,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_segment_type",
        "mpegts",
        "-hls_time",
        str(SEGMENT_TARGET_SECONDS),
        "-hls_list_size",
        str(HLS_LIST_SIZE),
        "-hls_flags",
        "temp_file",
        "-hls_segment_filename",
        str(owned_dir / "segment-%09d.ts"),
        str(owned_dir / "index.m3u8"),
    )


class ManifestWatcher:
    def __init__(
        self, owned_dir: Path, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self.owned_dir = Path(owned_dir)
        self._monotonic = monotonic
        self._highest_seen_sequence = -1

    @property
    def highest_seen_sequence(self) -> int:
        return self._highest_seen_sequence

    def scan(self) -> ManifestScanResult:
        manifest = self.owned_dir / "index.m3u8"
        if not is_owned_regular_file(manifest, self.owned_dir):
            return ManifestScanResult()
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return ManifestScanResult()
        if not lines or lines[0].strip() != "#EXTM3U":
            return ManifestScanResult()

        media_sequence: int | None = None
        for line in lines:
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    media_sequence = int(line.partition(":")[2].strip())
                except ValueError:
                    return ManifestScanResult()
                break
        if media_sequence is None or media_sequence < 0:
            return ManifestScanResult()

        entries: list[tuple[float, str]] = []
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if not line.startswith("#EXTINF:"):
                index += 1
                continue
            raw_duration = line.partition(":")[2].partition(",")[0].strip()
            if index + 1 >= len(lines):
                break
            uri = lines[index + 1].strip()
            if not uri or uri.startswith("#"):
                index += 1
                continue
            try:
                duration = float(raw_duration)
            except ValueError:
                duration = math.nan
            entries.append((duration, uri))
            index += 2

        registered: list[SegmentRecord] = []
        invalid = 0
        for position, (duration, uri) in enumerate(entries):
            sequence = media_sequence + position
            if sequence <= self._highest_seen_sequence:
                continue
            if (
                not math.isfinite(duration)
                or duration <= 0
                or not _SEGMENT_NAME.fullmatch(uri)
            ):
                invalid += 1
                continue
            path = self.owned_dir / uri
            if not is_owned_regular_file(path, self.owned_dir):
                invalid += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                invalid += 1
                continue
            if size <= 0 or not _is_basic_mpeg_ts(path, size):
                invalid += 1
                continue
            registered.append(
                SegmentRecord(
                    sequence=sequence,
                    path=path,
                    duration_seconds=duration,
                    size_bytes=size,
                    completed_at_monotonic=self._monotonic(),
                )
            )
            self._highest_seen_sequence = sequence
        return ManifestScanResult(tuple(registered), invalid)


@dataclass
class _BufferMetadata:
    record: SegmentRecord
    pin_count: int = 0
    pending_delete: bool = False


class SnapshotInvalidated(RuntimeError):
    pass


class PinnedSnapshot:
    def __init__(
        self,
        owner: RollingSegmentBuffer,
        records: tuple[SegmentRecord, ...],
    ) -> None:
        self._owner = owner
        self.records = records
        self.actual_duration_seconds = sum(
            item.duration_seconds for item in records
        )
        self._valid = True
        self._released = False

    @property
    def valid(self) -> bool:
        return self._valid and not self._released

    def ensure_valid(self) -> None:
        if not self.valid:
            raise SnapshotInvalidated("preview snapshot is no longer valid")

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._valid = False
        self._owner._release_snapshot(self)

    def _force_invalidate(self) -> None:
        if self._released:
            return
        self._released = True
        self._valid = False
        self._owner._release_snapshot(self)


class RollingSegmentBuffer:
    def __init__(
        self,
        owned_dir: Path,
        max_seconds: float,
        max_bytes: int,
        *,
        max_files: int = MAX_COMPLETE_FILES,
    ) -> None:
        self.owned_dir = Path(owned_dir)
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self.max_files = max_files
        self._items: OrderedDict[int, _BufferMetadata] = OrderedDict()
        self._highest_registered_sequence = -1
        self._snapshots: set[PinnedSnapshot] = set()
        self._closing = False
        self._release_event = asyncio.Event()
        self._release_event.set()

    @property
    def records(self) -> tuple[SegmentRecord, ...]:
        return tuple(item.record for item in self._items.values())

    @property
    def total_duration_seconds(self) -> float:
        return sum(item.record.duration_seconds for item in self._items.values())

    @property
    def total_bytes(self) -> int:
        return sum(item.record.size_bytes for item in self._items.values())

    @property
    def active_pin_count(self) -> int:
        return sum(item.pin_count for item in self._items.values())

    @property
    def active_snapshot_count(self) -> int:
        return len(self._snapshots)

    def register(self, record: SegmentRecord) -> bool:
        if (
            record.sequence <= self._highest_registered_sequence
            or record.size_bytes <= 0
            or not math.isfinite(record.duration_seconds)
            or record.duration_seconds <= 0
        ):
            return False
        path = record.path
        if (
            not _SEGMENT_NAME.fullmatch(path.name)
            or not is_owned_regular_file(path, self.owned_dir)
        ):
            return False
        try:
            actual_size = path.stat().st_size
        except OSError:
            return False
        if actual_size != record.size_bytes or not _is_basic_mpeg_ts(
            path, actual_size
        ):
            return False
        self._items[record.sequence] = _BufferMetadata(record)
        self._highest_registered_sequence = record.sequence
        self._items = OrderedDict(sorted(self._items.items()))
        self.enforce_soft_limits()
        return True

    def _over_time(self) -> bool:
        return self.total_duration_seconds > self.max_seconds

    def _over_bytes(self) -> bool:
        return self.total_bytes > self.max_bytes

    def _over_files(self) -> bool:
        return len(self._items) > self.max_files

    def enforce_soft_limits(self) -> None:
        for over_limit in (self._over_time, self._over_bytes, self._over_files):
            while over_limit():
                candidate = next(
                    (item for item in self._items.values() if item.pin_count == 0),
                    None,
                )
                if candidate is None:
                    for item in self._items.values():
                        item.pending_delete = True
                    break
                self._remove(candidate)

    def _remove(self, item: _BufferMetadata) -> None:
        self._items.pop(item.record.sequence, None)
        path = item.record.path
        if is_owned_regular_file(path, self.owned_dir) and _SEGMENT_NAME.fullmatch(
            path.name
        ):
            try:
                path.unlink()
            except OSError:
                pass

    def acquire_snapshot(
        self, window_seconds: float = 90
    ) -> SnapshotAcquireResult:
        if self._closing:
            return SnapshotAcquireResult(SnapshotStatus.CLOSING)
        if not self._items:
            return SnapshotAcquireResult(SnapshotStatus.EMPTY)
        selected: list[_BufferMetadata] = []
        duration = 0.0
        for item in reversed(tuple(self._items.values())):
            selected.append(item)
            duration += item.record.duration_seconds
            if duration >= window_seconds:
                break
        selected.reverse()
        for item in selected:
            item.pin_count += 1
        snapshot = PinnedSnapshot(self, tuple(item.record for item in selected))
        self._snapshots.add(snapshot)
        self._release_event.clear()
        return SnapshotAcquireResult(SnapshotStatus.READY, snapshot)

    def _release_snapshot(self, snapshot: PinnedSnapshot) -> None:
        if snapshot not in self._snapshots:
            return
        self._snapshots.remove(snapshot)
        selected = {record.sequence for record in snapshot.records}
        for sequence in selected:
            item = self._items.get(sequence)
            if item is not None and item.pin_count > 0:
                item.pin_count -= 1
        if not self._snapshots:
            self._release_event.set()
        self.enforce_soft_limits()

    def begin_close(self) -> None:
        self._closing = True

    async def wait_for_snapshot_release(self, timeout: float = 2.0) -> bool:
        if not self._snapshots:
            return True
        try:
            await asyncio.wait_for(self._release_event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    def force_invalidate_snapshots(self) -> None:
        for snapshot in tuple(self._snapshots):
            snapshot._force_invalidate()

    def has_unrecoverable_disk_pressure(
        self, *, partial_bytes: int, root_bytes: int, free_bytes: int
    ) -> bool:
        self.enforce_soft_limits()
        return (
            partial_bytes > PARTIAL_FILE_MAX_BYTES
            or root_bytes > ROOT_HARD_MAX_BYTES
            or free_bytes < FREE_DISK_RESERVE_BYTES
            or len(self._items) > MAX_COMPLETE_FILES
        )
