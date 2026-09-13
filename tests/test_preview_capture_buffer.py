from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bot import preview_capture as capture


def _segment(root: Path, sequence: int, duration: float = 2.0, size: int = 188):
    path = root / f"segment-{sequence:09d}.ts"
    path.write_bytes((bytes([0x47]) + b"\x00" * 187) * (size // 188))
    return capture.SegmentRecord(
        sequence=sequence,
        path=path,
        duration_seconds=duration,
        size_bytes=path.stat().st_size,
        completed_at_monotonic=float(sequence),
    )


class RollingSegmentBufferTests(unittest.TestCase):
    def test_segment_record_is_immutable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            record = _segment(Path(temp_dir), 1)
            with self.assertRaises((AttributeError, TypeError)):
                record.sequence = 2

    def test_rejects_duplicate_zero_size_and_invalid_duration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, 120, 1024 * 1024)
            good = _segment(root, 1)

            self.assertTrue(buffer.register(good))
            self.assertFalse(buffer.register(good))
            self.assertFalse(
                buffer.register(
                    capture.SegmentRecord(2, root / "zero.ts", 2, 0, 2)
                )
            )
            self.assertFalse(
                buffer.register(
                    capture.SegmentRecord(3, root / "nan.ts", float("nan"), 188, 3)
                )
            )
            self.assertFalse(
                buffer.register(
                    capture.SegmentRecord(4, root / "negative.ts", -1, 188, 4)
                )
            )

    def test_rejects_non_owned_invalid_ts_size_mismatch_and_evicted_sequence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, 2, 188)
            first = _segment(root, 1)
            self.assertTrue(buffer.register(first))
            self.assertTrue(buffer.register(_segment(root, 2)))
            recreated_old = _segment(root, 1)
            outside = root.parent / "outside-preview-register.ts"
            outside.write_bytes((bytes([0x47]) + b"\0" * 187))
            invalid_ts = root / "segment-000000003.ts"
            invalid_ts.write_bytes(b"x" * 188)
            mismatched = _segment(root, 4).path
            try:
                self.assertFalse(buffer.register(recreated_old))
                self.assertFalse(
                    buffer.register(
                        capture.SegmentRecord(3, invalid_ts, 2, 188, 3)
                    )
                )
                self.assertFalse(
                    buffer.register(
                        capture.SegmentRecord(4, mismatched, 2, 999, 4)
                    )
                )
                self.assertFalse(
                    buffer.register(
                        capture.SegmentRecord(5, outside, 2, 188, 5)
                    )
                )
            finally:
                outside.unlink(missing_ok=True)

    def test_time_then_bytes_then_file_count_evict_oldest_unpinned(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(
                root, max_seconds=4, max_bytes=188 * 2, max_files=2
            )
            for sequence in range(3):
                buffer.register(_segment(root, sequence))

            self.assertEqual([item.sequence for item in buffer.records], [1, 2])
            self.assertFalse((root / "segment-000000000.ts").exists())
            self.assertEqual(buffer.total_duration_seconds, 4)
            self.assertEqual(buffer.total_bytes, 188 * 2)

    def test_snapshot_selects_latest_backwards_and_returns_chronological(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, 120, 1024 * 1024)
            for sequence in range(5):
                buffer.register(_segment(root, sequence, duration=2.0))

            result = buffer.acquire_snapshot(window_seconds=5)

            self.assertEqual(result.status, capture.SnapshotStatus.READY)
            self.assertEqual(
                [item.sequence for item in result.snapshot.records], [2, 3, 4]
            )
            self.assertEqual(result.snapshot.actual_duration_seconds, 6.0)
            self.assertIsInstance(result.snapshot.records, tuple)

    def test_empty_and_closing_snapshot_statuses(self) -> None:
        with TemporaryDirectory() as temp_dir:
            buffer = capture.RollingSegmentBuffer(Path(temp_dir), 120, 1024)

            empty = buffer.acquire_snapshot()
            buffer.begin_close()
            closing = buffer.acquire_snapshot()

        self.assertEqual(empty.status, capture.SnapshotStatus.EMPTY)
        self.assertIsNone(empty.snapshot)
        self.assertEqual(closing.status, capture.SnapshotStatus.CLOSING)

    def test_overlapping_pins_release_idempotently_then_deferred_evict(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, max_seconds=4, max_bytes=1024)
            for sequence in range(2):
                buffer.register(_segment(root, sequence))
            first = buffer.acquire_snapshot(4).snapshot
            second = buffer.acquire_snapshot(4).snapshot
            self.assertEqual(buffer.active_pin_count, 4)
            buffer.max_seconds = 0.5
            buffer.enforce_soft_limits()

            first.release()
            first.release()
            self.assertEqual(buffer.active_pin_count, 2)
            self.assertTrue(all(item.path.exists() for item in second.records))
            second.release()

            self.assertEqual(buffer.active_pin_count, 0)
            self.assertEqual(buffer.records, ())

    def test_pins_may_exceed_soft_limit_but_hard_budget_reports_pressure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(
                root, max_seconds=4, max_bytes=188 * 2
            )
            buffer.register(_segment(root, 0))
            buffer.register(_segment(root, 1))
            snapshot = buffer.acquire_snapshot().snapshot
            buffer.max_seconds = 2
            buffer.max_bytes = 188
            buffer.enforce_soft_limits()

            pressure = buffer.has_unrecoverable_disk_pressure(
                partial_bytes=33 * 1024 * 1024,
                root_bytes=385 * 1024 * 1024,
                free_bytes=255 * 1024 * 1024,
            )

            self.assertTrue(pressure)
            self.assertGreater(buffer.total_duration_seconds, buffer.max_seconds)
            snapshot.release()

    def test_force_invalidation_clears_pins_and_snapshot_reports_invalid(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, 120, 1024)
            buffer.register(_segment(root, 1))
            snapshot = buffer.acquire_snapshot().snapshot
            copied_path = snapshot.records[0].path

            buffer.begin_close()
            buffer.force_invalidate_snapshots()

            self.assertEqual(buffer.active_pin_count, 0)
            self.assertFalse(snapshot.valid)
            with self.assertRaises(capture.SnapshotInvalidated):
                snapshot.ensure_valid()
            self.assertIsInstance(copied_path, Path)

    def test_never_deletes_path_that_fails_ownership_revalidation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            buffer = capture.RollingSegmentBuffer(root, 2, 1024)
            record = _segment(root, 1)
            self.assertTrue(buffer.register(record))
            record.path.unlink()
            record.path.mkdir()
            buffer.max_seconds = 0.5

            buffer.enforce_soft_limits()

            self.assertTrue(record.path.is_dir())


if __name__ == "__main__":
    unittest.main()
