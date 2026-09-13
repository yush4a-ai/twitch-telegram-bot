from __future__ import annotations

import importlib
import math
import unittest


def _models():
    try:
        return importlib.import_module("bot.preview_analysis.models")
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 analysis models must exist") from exc


class AnalysisModelTests(unittest.TestCase):
    def test_statuses_are_exact_and_stable(self) -> None:
        models = _models()
        self.assertEqual(
            [(item.name, item.value) for item in models.AnalysisStatus],
            [
                ("SUCCESS", "success"),
                ("NO_SELECTION", "no_selection"),
                ("SNAPSHOT_TOO_SHORT", "snapshot_too_short"),
                ("SNAPSHOT_INVALIDATED", "snapshot_invalidated"),
                ("PROCESS_FAILED", "process_failed"),
                ("TIMEOUT", "timeout"),
                ("MALFORMED_METRICS", "malformed_metrics"),
                ("INTERNAL_ERROR", "internal_error"),
            ],
        )

    def test_window_requires_finite_nonnegative_values(self) -> None:
        models = _models()
        valid = models.HighlightWindow(1.0, 3.0, 0.5)
        self.assertEqual(valid.duration_seconds, 3.0)
        for args in (
            (-1.0, 3.0, 0.5),
            (0.0, 0.0, 0.5),
            (0.0, 3.0, -0.1),
            (0.0, 3.0, 1.1),
            (math.nan, 3.0, 0.5),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                models.HighlightWindow(*args)

    def test_selection_is_chronological_and_bounded(self) -> None:
        models = _models()
        window = models.HighlightWindow(1.0, 3.0, 0.5)
        self.assertEqual(models.HighlightSelection(()).windows, ())
        with self.assertRaises(ValueError):
            models.HighlightSelection((window,) * 4)
        with self.assertRaises(ValueError):
            models.HighlightSelection(
                (
                    models.HighlightWindow(5.0, 3.0, 0.7),
                    models.HighlightWindow(1.0, 3.0, 0.8),
                )
            )

    def test_result_enforces_status_selection_invariants(self) -> None:
        models = _models()
        empty = models.HighlightSelection(())
        one = models.HighlightSelection(
            (models.HighlightWindow(1.0, 3.0, 0.5),)
        )
        self.assertEqual(
            models.AnalysisResult(models.AnalysisStatus.SUCCESS, one).selection,
            one,
        )
        with self.assertRaises(ValueError):
            models.AnalysisResult(models.AnalysisStatus.SUCCESS, empty)
        with self.assertRaises(ValueError):
            models.AnalysisResult(models.AnalysisStatus.NO_SELECTION, one)
        with self.assertRaises(ValueError):
            models.AnalysisResult(models.AnalysisStatus.TIMEOUT, one)

    def test_diagnostic_code_is_allowlisted_and_contains_no_paths(self) -> None:
        models = _models()
        empty = models.HighlightSelection(())
        result = models.AnalysisResult(
            models.AnalysisStatus.PROCESS_FAILED,
            empty,
            "missing_segment",
        )
        self.assertEqual(result.diagnostic_code, "missing_segment")
        for value in (
            "C:\\secret\\segment.ts",
            "/tmp/secret",
            "raw stderr",
            "not-allowlisted",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                models.AnalysisResult(
                    models.AnalysisStatus.PROCESS_FAILED, empty, value
                )

    def test_config_defaults_encode_approved_policy(self) -> None:
        models = _models()
        config = models.AnalysisConfig()
        self.assertEqual((config.analysis_fps, config.analysis_width, config.analysis_height), (10, 320, 180))
        self.assertEqual((config.fingerprint_width, config.fingerprint_height), (16, 9))
        self.assertEqual((config.candidate_duration, config.candidate_step), (3, 1))
        self.assertEqual((config.pass_timeout, config.overall_timeout), (45.0, 60.0))
        self.assertEqual(config.minimum_score, 0.34)
        self.assertEqual(config.temporal_nms_seconds, 12.0)
        self.assertEqual(config.duplicate_threshold, 0.04)
        with self.assertRaises(ValueError):
            models.AnalysisConfig(analysis_fps=0)

    def test_every_failure_status_requires_empty_selection(self) -> None:
        models = _models()
        one = models.HighlightSelection(
            (models.HighlightWindow(1.0, 3.0, 0.5),)
        )
        for status in models.AnalysisStatus:
            if status is models.AnalysisStatus.SUCCESS:
                continue
            with self.subTest(status=status), self.assertRaises(ValueError):
                models.AnalysisResult(status, one)

    def test_config_rejects_inverted_or_nonfinite_ranges(self) -> None:
        models = _models()
        for changes in (
            {"relative_percentile_low": 0.9, "relative_percentile_high": 0.2},
            {"audio_delta_low_db": 12.0, "audio_delta_high_db": 3.0},
            {"scene_normalization_low": 20.0, "scene_normalization_high": 8.0},
            {"motion_absolute_low": 12.0, "motion_absolute_high": 1.0},
            {"pass_timeout": float("inf")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                models.AnalysisConfig(**changes)


if __name__ == "__main__":
    unittest.main()
