from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from bot.preview_capture import SegmentRecord


def _concat():
    try:
        return importlib.import_module("bot.preview_analysis.concat")
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 concat boundary must exist") from exc


class FakeSnapshot:
    def __init__(self, records, *, valid: bool = True) -> None:
        self.records = tuple(records)
        self.actual_duration_seconds = sum(x.duration_seconds for x in records)
        self.valid = valid
        self.release_calls = 0

    def ensure_valid(self) -> None:
        if not self.valid:
            raise RuntimeError("invalid")

    def release(self) -> None:
        self.release_calls += 1


def _segment(parent: Path, sequence: int, duration: float = 2.0) -> SegmentRecord:
    path = parent / f"segment-{sequence:09d}.ts"
    path.write_bytes(b"\x47" + (b"x" * 187))
    return SegmentRecord(sequence, path, duration, path.stat().st_size, float(sequence))


class SnapshotValidationTests(unittest.TestCase):
    def test_empty_and_invalidated_snapshots_are_rejected(self) -> None:
        concat = _concat()
        with self.assertRaises(concat.SnapshotValidationError):
            concat.validate_snapshot(FakeSnapshot(()))
        with self.assertRaises(concat.SnapshotBecameInvalid):
            concat.validate_snapshot(FakeSnapshot((), valid=False))

    def test_sequences_must_be_unique_and_strictly_increasing(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            a = _segment(parent, 1)
            b = _segment(parent, 2)
            duplicate = SegmentRecord(1, b.path, 2.0, b.size_bytes, 2.0)
            for records in ((b, a), (a, duplicate)):
                with self.subTest(records=records), self.assertRaises(
                    concat.SnapshotValidationError
                ):
                    concat.validate_snapshot(FakeSnapshot(records))

    def test_paths_require_same_parent_exact_name_regular_file_and_size(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            one = root / "one"
            two = root / "two"
            one.mkdir()
            two.mkdir()
            a = _segment(one, 1)
            b = _segment(two, 2)
            invalid_name = SegmentRecord(1, one / "clip.ts", 2.0, 0, 1.0)
            invalid_name.path.write_bytes(b"")
            wrong_size = SegmentRecord(1, a.path, 2.0, a.size_bytes + 1, 1.0)
            missing = SegmentRecord(1, one / "segment-000000099.ts", 2.0, 1, 1.0)
            for records in ((a, b), (invalid_name,), (wrong_size,), (missing,)):
                with self.subTest(records=records), self.assertRaises(
                    concat.SnapshotValidationError
                ):
                    concat.validate_snapshot(FakeSnapshot(records))

    def test_duration_must_be_finite_positive(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            base = _segment(parent, 1)
            for value in (0.0, -1.0, float("nan"), float("inf")):
                record = SegmentRecord(1, base.path, value, base.size_bytes, 1.0)
                with self.subTest(value=value), self.assertRaises(
                    concat.SnapshotValidationError
                ):
                    concat.validate_snapshot(FakeSnapshot((record,)))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_segment_is_rejected(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            target = _segment(parent, 1)
            link = parent / "segment-000000002.ts"
            try:
                link.symlink_to(target.path)
            except OSError as exc:
                self.skipTest(str(exc))
            record = SegmentRecord(2, link, 2.0, link.stat().st_size, 2.0)
            with self.assertRaises(concat.SnapshotValidationError):
                concat.validate_snapshot(FakeSnapshot((record,)))

    def test_reported_duration_must_match_record_sum(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            record = _segment(Path(raw), 1, 2.0)
            snapshot = FakeSnapshot((record,))
            snapshot.actual_duration_seconds = 3.0
            with self.assertRaises(concat.SnapshotValidationError):
                concat.validate_snapshot(snapshot)

    def test_segment_filename_number_must_match_record_sequence(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            base = _segment(Path(raw), 1)
            record = SegmentRecord(2, base.path, 2.0, base.size_bytes, 2.0)
            with self.assertRaises(concat.SnapshotValidationError):
                concat.validate_snapshot(FakeSnapshot((record,)))


class ConcatManifestTests(unittest.TestCase):
    def test_manifest_preserves_chronology_duration_and_escapes_quote(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory(prefix="analysis-'quote-") as raw:
            parent = Path(raw)
            records = (_segment(parent, 7, 1.25), _segment(parent, 8, 2.75))
            validated = concat.validate_snapshot(FakeSnapshot(records))
            job = concat.AnalysisTempManager(root=parent / "jobs").create_job()
            try:
                manifest = concat.write_concat_manifest(validated, job)
                text = manifest.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("ffconcat version 1.0\n"))
                self.assertLess(text.index("segment-000000007.ts"), text.index("segment-000000008.ts"))
                self.assertIn("duration 1.25", text)
                self.assertIn("duration 2.75", text)
                self.assertIn("'\\''", text)
            finally:
                job.cleanup()

    def test_owned_cleanup_removes_job_and_fingerprint_not_borrowed_segment(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            segment = _segment(parent, 1)
            job = concat.AnalysisTempManager(root=parent / "jobs").create_job()
            job.fingerprint_path.write_bytes(b"x" * 144)
            job.cleanup()
            self.assertFalse(job.path.exists())
            self.assertTrue(segment.path.exists())

    def test_missing_or_tampered_marker_prevents_cleanup(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            job = concat.AnalysisTempManager(root=root / "jobs").create_job()
            job.marker_path.write_text("tampered", encoding="utf-8")
            self.assertFalse(job.cleanup())
            self.assertTrue(job.path.exists())
            foreign = root / "foreign"
            foreign.mkdir()
            with self.assertRaises(PermissionError):
                concat.AnalysisTempManager(root=foreign).create_job()

    def test_default_root_is_system_ephemeral_temp(self) -> None:
        concat = _concat()
        manager = concat.AnalysisTempManager()
        self.assertEqual(
            Path(os.path.commonpath((manager.root.resolve(), Path(tempfile.gettempdir()).resolve()))),
            Path(tempfile.gettempdir()).resolve(),
        )

    def test_manifest_creation_never_mutates_borrowed_segment(self) -> None:
        concat = _concat()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            segment = _segment(parent, 1)
            before = segment.path.read_bytes()
            job = concat.AnalysisTempManager(root=parent / "jobs").create_job()
            try:
                validated = concat.validate_snapshot(FakeSnapshot((segment,)))
                concat.write_concat_manifest(validated, job)
                self.assertEqual(segment.path.read_bytes(), before)
                self.assertEqual(segment.path.stat().st_size, segment.size_bytes)
            finally:
                job.cleanup()


if __name__ == "__main__":
    unittest.main()
