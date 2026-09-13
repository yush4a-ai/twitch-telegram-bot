from __future__ import annotations

import importlib
import unittest


def _modules():
    try:
        return (
            importlib.import_module("bot.preview_analysis.models"),
            importlib.import_module("bot.preview_analysis.selector"),
        )
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 selector must exist") from exc


def _timeline(models, length: int, **overrides):
    return tuple(
        models.AnalysisBin(
            start_seconds=float(sec),
            motion_raw=overrides.get("motion_raw", 1.0),
            motion=overrides.get("motion", 0.0),
            audio_spike=overrides.get("audio_spike", 0.0),
            scene_score=overrides.get("scene_score", 0.0),
            scene_cut_count=overrides.get("scene_cut_count", 0),
            black_ratio=overrides.get("black_ratio", 0.0),
            static_seconds=overrides.get("static_seconds", 0.0),
            visual_coverage=overrides.get("visual_coverage", 1.0),
            valid=overrides.get("valid", True),
            fingerprint=bytes([sec % 256]) * 144,
        )
        for sec in range(length)
    )


def _replace(models, bins, start: int, stop: int, **values):
    result = list(bins)
    for sec in range(start, stop):
        old = result[sec]
        fields = {
            name: getattr(old, name) for name in old.__dataclass_fields__
        }
        fields.update(values)
        if "fingerprint" not in values:
            fields["fingerprint"] = bytes([(sec * 17) % 256]) * 144
        result[sec] = models.AnalysisBin(**fields)
    return tuple(result)


class SelectorCorpusTests(unittest.TestCase):
    def test_static_menu_and_flat_timeline_have_no_selection(self) -> None:
        models, selector = _modules()
        for bins in (
            _timeline(models, 75),
            _timeline(models, 75, motion=0.05, audio_spike=0.05, scene_score=0.05),
        ):
            with self.subTest():
                self.assertEqual(
                    selector.select_highlights(
                        bins, 75.0, models.AnalysisConfig()
                    ).windows,
                    (),
                )

    def test_one_action_spike_returns_one_chronological_window(self) -> None:
        models, selector = _modules()
        bins = _replace(models, _timeline(models, 75), 20, 23, motion=0.9)
        result = selector.select_highlights(bins, 75.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 1)
        self.assertLessEqual(result.windows[0].start_seconds, 20.0)
        self.assertGreaterEqual(result.windows[0].start_seconds, 18.0)

    def test_three_separated_actions_return_three_windows(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 90)
        for start, shade in ((8, 0), (35, 128), (68, 255)):
            bins = _replace(
                models,
                bins,
                start,
                start + 3,
                motion=0.95,
                fingerprint=bytes([shade]) * 144,
            )
        result = selector.select_highlights(bins, 90.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 3)
        self.assertEqual(
            tuple(sorted(window.start_seconds for window in result.windows)),
            tuple(window.start_seconds for window in result.windows),
        )

    def test_long_continuous_fight_collapses_to_one_event(self) -> None:
        models, selector = _modules()
        bins = _replace(models, _timeline(models, 90), 10, 40, motion=0.8)
        result = selector.select_highlights(bins, 90.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 1)

    def test_two_peaks_with_two_second_low_valley_can_form_two_events(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 90)
        bins = _replace(models, bins, 10, 18, motion=0.75)
        bins = _replace(models, bins, 10, 13, motion=1.0)
        bins = _replace(models, bins, 18, 20, motion=0.0)
        bins = _replace(models, bins, 20, 31, motion=0.75)
        bins = _replace(models, bins, 28, 31, motion=1.0)
        result = selector.select_highlights(bins, 90.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 2)
        self.assertGreaterEqual(result.windows[1].start_seconds - result.windows[0].start_seconds, 12.0)

    def test_constant_music_is_not_highlight_but_audio_pulse_with_motion_is(self) -> None:
        models, selector = _modules()
        constant = _timeline(models, 75, audio_spike=0.0, motion=0.05)
        pulse = _replace(models, constant, 30, 33, audio_spike=1.0, motion=0.25)
        self.assertEqual(selector.select_highlights(constant, 75.0, models.AnalysisConfig()).windows, ())
        self.assertEqual(len(selector.select_highlights(pulse, 75.0, models.AnalysisConfig()).windows), 1)

    def test_quiet_high_motion_is_not_rejected(self) -> None:
        models, selector = _modules()
        bins = _replace(models, _timeline(models, 75), 30, 33, motion=0.95, audio_spike=0.0)
        self.assertEqual(len(selector.select_highlights(bins, 75.0, models.AnalysisConfig()).windows), 1)

    def test_black_freeze_cut_spam_and_corrupt_gap_are_hard_rejected(self) -> None:
        models, selector = _modules()
        cases = (
            {"motion": 1.0, "black_ratio": 0.9},
            {"motion": 1.0, "static_seconds": 1.0},
            {"motion": 1.0, "scene_cut_count": 2},
            {"motion": 1.0, "valid": False},
            {"motion": 1.0, "visual_coverage": 0.6},
        )
        for values in cases:
            bins = _replace(models, _timeline(models, 75), 30, 33, **values)
            with self.subTest(values=values):
                self.assertEqual(
                    selector.select_highlights(bins, 75.0, models.AnalysisConfig()).windows,
                    (),
                )

    def test_similar_fingerprints_are_deduplicated(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 90)
        identical = b"q" * 144
        bins = _replace(models, bins, 8, 11, motion=0.9, fingerprint=identical)
        bins = _replace(models, bins, 40, 43, motion=0.95, fingerprint=identical)
        result = selector.select_highlights(bins, 90.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 1)
        self.assertGreaterEqual(result.windows[0].start_seconds, 38.0)

    def test_exact_tie_prefers_earlier_start(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 45)
        bins = _replace(models, bins, 5, 8, motion=0.9)
        bins = _replace(models, bins, 25, 28, motion=0.9)
        result = selector.select_highlights(bins, 45.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 1)
        self.assertLess(result.windows[0].start_seconds, 15.0)

    def test_capacity_policy_and_no_forced_fallback(self) -> None:
        models, selector = _modules()
        for duration, expected in ((29.9, 0), (45.0, 1), (65.0, 2), (80.0, 3)):
            bins = _timeline(models, int(duration))
            for start in (5, 22, 40, 60):
                if start + 3 < len(bins):
                    bins = _replace(models, bins, start, start + 3, motion=0.95)
            with self.subTest(duration=duration):
                self.assertLessEqual(
                    len(selector.select_highlights(bins, duration, models.AnalysisConfig()).windows),
                    expected,
                )

    def test_repeated_selection_is_byte_for_byte_deterministic(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 90)
        for start in (10, 35, 70):
            bins = _replace(models, bins, start, start + 3, motion=0.91, audio_spike=0.4)
        results = [selector.select_highlights(bins, 90.0, models.AnalysisConfig()) for _ in range(50)]
        self.assertTrue(all(result == results[0] for result in results[1:]))
        self.assertTrue(all(window.score == round(window.score, 6) for window in results[0].windows))

    def test_fingerprint_distance_uses_normalized_absolute_difference(self) -> None:
        _, selector = _modules()
        self.assertEqual(selector.fingerprint_distance(bytes(144), bytes(144)), 0.0)
        self.assertEqual(selector.fingerprint_distance(bytes(144), bytes([255]) * 144), 1.0)

    def test_temporal_nms_rejects_events_with_centers_under_twelve_seconds(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 75)
        bins = _replace(models, bins, 10, 13, motion=1.0, fingerprint=bytes(144))
        bins = _replace(models, bins, 20, 23, motion=0.95, fingerprint=bytes([255]) * 144)
        result = selector.select_highlights(bins, 75.0, models.AnalysisConfig())
        self.assertEqual(len(result.windows), 1)
        self.assertLess(result.windows[0].start_seconds, 15.0)

    def test_three_cut_candidate_gets_penalty_but_is_not_hard_rejected(self) -> None:
        models, selector = _modules()
        base = _timeline(models, 45, motion=0.8, scene_score=1.0)
        penalized = _timeline(
            models, 45, motion=0.8, scene_score=1.0, scene_cut_count=1
        )
        plain_result = selector.select_highlights(base, 45.0, models.AnalysisConfig())
        cut_result = selector.select_highlights(penalized, 45.0, models.AnalysisConfig())
        self.assertEqual(len(cut_result.windows), 1)
        self.assertLess(cut_result.windows[0].score, plain_result.windows[0].score)

    def test_selection_never_starts_without_full_leading_margin(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 30, motion=1.0)
        result = selector.select_highlights(bins, 30.0, models.AnalysisConfig())
        self.assertEqual(result.windows[0].start_seconds, 1.0)

    def test_score_below_minimum_is_not_forced_into_selection(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 75, motion=0.6)
        result = selector.select_highlights(bins, 75.0, models.AnalysisConfig())
        self.assertEqual(result.windows, ())


class SafeFallbackTests(unittest.TestCase):
    def test_flat_uninteresting_timeline_still_yields_a_fallback(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNotNone(window)

    def test_fallback_duration_is_exactly_five_seconds(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertEqual(window.duration_seconds, 5.0)

    def test_fallback_is_one_continuous_window(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertEqual(window.duration_seconds, selector.FALLBACK_DURATION_SECONDS)
        self.assertGreaterEqual(window.start_seconds, 0.0)
        self.assertLessEqual(window.start_seconds + window.duration_seconds, 40.0)

    def test_fallback_avoids_black_interval(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        bins = _replace(models, bins, 0, 35, black_ratio=1.0)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNotNone(window)
        for offset in range(int(window.duration_seconds)):
            index = int(window.start_seconds) + offset
            self.assertLessEqual(bins[index].black_ratio, models.AnalysisConfig().black_second_limit)

    def test_fallback_avoids_freeze_static_interval(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        bins = _replace(models, bins, 0, 35, static_seconds=1.0)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNotNone(window)
        total_static = sum(
            bins[int(window.start_seconds) + offset].static_seconds
            for offset in range(int(window.duration_seconds))
        )
        self.assertLess(total_static, models.AnalysisConfig().static_overlap_limit)

    def test_fallback_rejects_entirely_invalid_or_corrupt_snapshot(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40, valid=False)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNone(window)

    def test_fallback_rejects_low_coverage_interval(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        bins = _replace(models, bins, 0, 40, visual_coverage=0.1)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNone(window)

    def test_fallback_ignores_lack_of_interestingness(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40, motion=0.0, audio_spike=0.0, scene_score=0.0)
        self.assertEqual(selector.select_highlights(bins, 40.0, models.AnalysisConfig()).windows, ())
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNotNone(window)

    def test_fallback_is_deterministic_not_random(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        first = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        second = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertEqual(first, second)

    def test_fallback_picks_the_safest_window_not_an_arbitrary_one(self) -> None:
        models, selector = _modules()
        bins = _timeline(models, 40)
        bins = _replace(models, bins, 0, 20, black_ratio=0.19)
        window = selector.select_safe_fallback(bins, 40.0, models.AnalysisConfig())
        self.assertIsNotNone(window)
        self.assertGreaterEqual(window.start_seconds, 20.0)


if __name__ == "__main__":
    unittest.main()
