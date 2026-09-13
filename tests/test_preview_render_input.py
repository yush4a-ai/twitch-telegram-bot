from __future__ import annotations

import importlib
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot.preview_analysis import HighlightSelection, HighlightWindow
from bot.preview_capture import SegmentRecord


def _input():
    try:
        return importlib.import_module("bot.preview_render.input")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render input boundary must exist") from exc


class FakeSnapshot:
    def __init__(self, records, *, valid: bool = True) -> None:
        self.records = tuple(records)
        self.actual_duration_seconds = math.fsum(
            record.duration_seconds for record in records
        )
        self.valid = valid
        self.release_calls = 0

    def ensure_valid(self) -> None:
        if not self.valid:
            raise RuntimeError("invalidated")

    def release(self) -> None:
        self.release_calls += 1


def _segment(parent: Path, sequence: int, duration: float) -> SegmentRecord:
    path = parent / f"segment-{sequence:09d}.ts"
    path.write_bytes(b"\x47" + b"x" * 187)
    return SegmentRecord(
        sequence=sequence,
        path=path,
        duration_seconds=duration,
        size_bytes=path.stat().st_size,
        completed_at_monotonic=float(sequence),
    )


def _window(start: float, duration: float) -> HighlightWindow:
    return HighlightWindow(start, duration, 0.8)


class SelectionValidationTests(unittest.TestCase):
    def test_empty_overlap_unsorted_and_out_of_range_are_rejected(self) -> None:
        render_input = _input()
        invalid = (
            HighlightSelection(),
            HighlightSelection((_window(0, 2), _window(1, 2))),
            SimpleNamespace(windows=(_window(2, 1), _window(1, 1))),
            HighlightSelection((_window(3, 2),)),
        )
        for selection in invalid:
            with self.subTest(selection=selection), self.assertRaises(
                render_input.SelectionValidationError
            ):
                render_input.validate_selection(selection, 4.0)

    def test_nonfinite_nonpositive_and_more_than_three_are_rejected_defensively(self) -> None:
        render_input = _input()
        malformed_windows = (
            (SimpleNamespace(start_seconds=float("nan"), duration_seconds=1.0),),
            (SimpleNamespace(start_seconds=0.0, duration_seconds=float("inf")),),
            (SimpleNamespace(start_seconds=-1.0, duration_seconds=1.0),),
            (SimpleNamespace(start_seconds=0.0, duration_seconds=0.0),),
            tuple(SimpleNamespace(start_seconds=float(index), duration_seconds=0.5) for index in range(4)),
        )
        for windows in malformed_windows:
            with self.subTest(windows=windows), self.assertRaises(
                render_input.SelectionValidationError
            ):
                render_input.validate_selection(
                    SimpleNamespace(windows=windows), 10.0
                )

    def test_valid_selection_keeps_original_order_without_sorting(self) -> None:
        render_input = _input()
        selection = HighlightSelection((_window(0.5, 1), _window(2.5, 1)))
        validated = render_input.validate_selection(selection, 4.0)
        self.assertEqual(validated.windows, selection.windows)


class SnapshotValidationTests(unittest.TestCase):
    def test_records_must_be_owned_regular_exact_and_chronological(self) -> None:
        render_input = _input()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            one = root / "one"
            two = root / "two"
            one.mkdir()
            two.mkdir()
            a = _segment(one, 1, 1.0)
            b = _segment(one, 2, 1.0)
            other_parent = _segment(two, 3, 1.0)
            wrong_name_path = one / "clip.ts"
            wrong_name_path.write_bytes(b"x")
            wrong_name = SegmentRecord(3, wrong_name_path, 1.0, 1, 3.0)
            wrong_size = SegmentRecord(1, a.path, 1.0, a.size_bytes + 1, 1.0)
            duplicate = SegmentRecord(1, b.path, 1.0, b.size_bytes, 2.0)
            cases = (
                (),
                (b, a),
                (a, duplicate),
                (a, other_parent),
                (wrong_name,),
                (wrong_size,),
            )
            for records in cases:
                snapshot = FakeSnapshot(records)
                with self.subTest(records=records), self.assertRaises(
                    render_input.SnapshotValidationError
                ):
                    render_input.validate_snapshot(snapshot)
                self.assertEqual(snapshot.release_calls, 0)

    def test_duration_and_reported_duration_are_validated(self) -> None:
        render_input = _input()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            base = _segment(parent, 1, 1.0)
            for duration in (0.0, -1.0, float("nan"), float("inf")):
                record = SegmentRecord(
                    1, base.path, duration, base.size_bytes, 1.0
                )
                with self.subTest(duration=duration), self.assertRaises(
                    render_input.SnapshotValidationError
                ):
                    render_input.validate_snapshot(FakeSnapshot((record,)))
            snapshot = FakeSnapshot((base,))
            snapshot.actual_duration_seconds = 2.0
            with self.assertRaises(render_input.SnapshotValidationError):
                render_input.validate_snapshot(snapshot)

    def test_invalidated_and_missing_snapshot_are_distinct(self) -> None:
        render_input = _input()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            record = _segment(parent, 1, 1.0)
            invalid = FakeSnapshot((record,), valid=False)
            with self.assertRaises(render_input.SnapshotBecameInvalid):
                render_input.validate_snapshot(invalid)
            record.path.unlink()
            with self.assertRaises(render_input.SnapshotFileMissing):
                render_input.validate_snapshot(FakeSnapshot((record,)))
            self.assertEqual(invalid.release_calls, 0)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_segment_is_rejected(self) -> None:
        render_input = _input()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            target = _segment(parent, 1, 1.0)
            link = parent / "segment-000000002.ts"
            try:
                link.symlink_to(target.path)
            except OSError as exc:
                self.skipTest(str(exc))
            record = SegmentRecord(2, link, 1.0, link.stat().st_size, 2.0)
            with self.assertRaises(render_input.SnapshotValidationError):
                render_input.validate_snapshot(FakeSnapshot((record,)))


class WindowMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.parent = Path(self.temporary.name)
        self.records = tuple(
            _segment(self.parent, sequence, duration)
            for sequence, duration in enumerate((1.0, 2.5, 0.5, 3.0), 1)
        )
        self.snapshot = FakeSnapshot(self.records)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _plans(self, windows):
        render_input = _input()
        validated_snapshot = render_input.validate_snapshot(self.snapshot)
        validated_selection = render_input.validate_selection(
            HighlightSelection(tuple(windows)),
            self.snapshot.actual_duration_seconds,
        )
        return render_input.plan_window_inputs(
            validated_snapshot, validated_selection
        )

    def test_exact_segment_start_and_end_select_only_that_segment(self) -> None:
        plan = self._plans((_window(1.0, 2.5),))[0]
        self.assertEqual(tuple(record.sequence for record in plan.records), (2,))
        self.assertEqual(plan.local_start_seconds, 0.0)
        self.assertEqual(plan.duration_seconds, 2.5)

    def test_mid_segment_and_cross_segment_ranges_are_exact(self) -> None:
        mid = self._plans((_window(0.25, 0.5),))[0]
        self.assertEqual(tuple(record.sequence for record in mid.records), (1,))
        self.assertEqual(mid.local_start_seconds, 0.25)
        crossed = self._plans((_window(0.5, 3.0),))[0]
        self.assertEqual(tuple(record.sequence for record in crossed.records), (1, 2))
        self.assertEqual(crossed.local_start_seconds, 0.5)

    def test_variable_durations_choose_minimal_covering_segments(self) -> None:
        plan = self._plans((_window(3.25, 1.0),))[0]
        self.assertEqual(tuple(record.sequence for record in plan.records), (2, 3, 4))
        self.assertEqual(plan.local_start_seconds, 2.25)
        self.assertEqual(plan.duration_seconds, 1.0)

    def test_one_two_and_three_windows_preserve_chronology(self) -> None:
        for count in (1, 2, 3):
            windows = tuple(_window(index * 2.0, 0.5) for index in range(count))
            plans = self._plans(windows)
            with self.subTest(count=count):
                self.assertEqual(len(plans), count)
                self.assertEqual(
                    tuple(plan.start_seconds for plan in plans),
                    tuple(window.start_seconds for window in windows),
                )


if __name__ == "__main__":
    unittest.main()
