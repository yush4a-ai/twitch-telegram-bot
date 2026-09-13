from __future__ import annotations

import importlib
import math
import tempfile
import unittest
from pathlib import Path


def _modules():
    try:
        return (
            importlib.import_module("bot.preview_analysis.metrics"),
            importlib.import_module("bot.preview_analysis.models"),
        )
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 metrics must exist") from exc


def _visual_second(metrics, second: int, ydiff: float, *, scene=0.0, black=0.0):
    return tuple(
        metrics.VisualSample(second + index / 10, ydiff, scene, black)
        for index in range(10)
    )


class MetadataParserTests(unittest.TestCase):
    def test_visual_parser_reads_metadata_blocks_not_binary(self) -> None:
        metrics, _ = _modules()
        parser = metrics.VisualMetadataParser(max_records=20)
        for line in (
            "frame:0 pts:0 pts_time:0.000000",
            "lavfi.signalstats.YDIF=4.5",
            "lavfi.scd.score=12.0",
            "lavfi.blackframe.pblack=25",
            "lavfi.freezedetect.freeze_start=0.0",
            "frame:10 pts:1000000 pts_time:1.000000",
            "lavfi.signalstats.YDIF=2.0",
            "lavfi.freezedetect.freeze_end=1.25",
        ):
            parser.feed_line(line)
        parsed = parser.finish()
        self.assertEqual(len(parsed.samples), 2)
        self.assertAlmostEqual(parsed.samples[0].black_ratio, 0.25)
        self.assertEqual(parsed.samples[0].scene_score, 12.0)
        self.assertEqual(parsed.freezes[0].end_seconds, 1.25)
        with self.assertRaises(metrics.MetricParseError):
            parser.feed_line("\x00\x01raw-fingerprint")

    def test_audio_parser_handles_minus_inf_but_rejects_nan_and_plus_inf(self) -> None:
        metrics, _ = _modules()
        parser = metrics.AudioMetadataParser(max_records=4)
        for line in (
            "frame:0 pts:0 pts_time:0",
            "lavfi.astats.Overall.RMS_level=-inf",
            "lavfi.astats.Overall.Peak_level=-10",
        ):
            parser.feed_line(line)
        parsed = parser.finish()
        self.assertEqual(parsed.samples[0].rms_db, -90.0)
        for bad in ("nan", "+inf", "inf"):
            parser = metrics.AudioMetadataParser(max_records=4)
            parser.feed_line("frame:0 pts:0 pts_time:0")
            parser.feed_line(f"lavfi.astats.Overall.RMS_level={bad}")
            parser.feed_line("lavfi.astats.Overall.Peak_level=-3")
            with self.subTest(bad=bad), self.assertRaises(metrics.MetricParseError):
                parser.finish()

    def test_timestamps_must_be_finite_monotonic_and_records_bounded(self) -> None:
        metrics, _ = _modules()
        parser = metrics.VisualMetadataParser(max_records=1)
        for line in (
            "frame:0 pts:0 pts_time:1",
            "lavfi.signalstats.YDIF=1",
            "frame:1 pts:1 pts_time:0",
            "lavfi.signalstats.YDIF=1",
        ):
            parser.feed_line(line)
        with self.assertRaises(metrics.MetricParseError):
            parser.finish()


class FingerprintTests(unittest.TestCase):
    def test_exact_tiny_frames_are_read_and_cap_is_enforced(self) -> None:
        metrics, models = _modules()
        config = models.AnalysisConfig(fingerprint_size_tolerance=16)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "fingerprints.gray"
            path.write_bytes(bytes(range(144)) * 3)
            frames = metrics.read_fingerprints(path, 3.0, config)
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(len(frame) == 144 for frame in frames))
            path.write_bytes(b"x" * (3 * 144 + 17))
            with self.assertRaises(metrics.MetricParseError):
                metrics.read_fingerprints(path, 3.0, config)

    def test_truncated_or_misaligned_file_is_malformed(self) -> None:
        metrics, models = _modules()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "fingerprints.gray"
            for payload in (b"", b"x" * 143, b"x" * 145, b"x" * 288):
                path.write_bytes(payload)
                with self.subTest(size=len(payload)), self.assertRaises(
                    metrics.MetricParseError
                ):
                    metrics.read_fingerprints(path, 3.0, models.AnalysisConfig())


class BinningTests(unittest.TestCase):
    def test_low_visual_coverage_invalidates_bin_and_incomplete_tail_is_ignored(self) -> None:
        metrics, models = _modules()
        samples = _visual_second(metrics, 0, 2.0)[:6] + _visual_second(metrics, 1, 3.0)
        visual = metrics.VisualMetrics(samples=samples)
        frames = (b"a" * 144, b"b" * 144)
        bins = metrics.build_analysis_bins(
            2.75, visual, metrics.AudioMetrics(()), frames, models.AnalysisConfig()
        )
        self.assertEqual(len(bins), 2)
        self.assertFalse(bins[0].valid)
        self.assertTrue(bins[1].valid)

    def test_hard_cut_samples_are_excluded_from_motion_median(self) -> None:
        metrics, models = _modules()
        samples = list(_visual_second(metrics, 0, 2.0))
        samples[-1] = metrics.VisualSample(0.9, 100.0, 15.0, 0.0)
        bins = metrics.build_analysis_bins(
            1.0,
            metrics.VisualMetrics(tuple(samples)),
            metrics.AudioMetrics(()),
            (b"a" * 144,),
            models.AnalysisConfig(),
        )
        self.assertAlmostEqual(bins[0].motion_raw, 2.0)
        self.assertEqual(bins[0].scene_cut_count, 1)

    def test_audio_less_success_produces_zero_spikes(self) -> None:
        metrics, models = _modules()
        visual = metrics.VisualMetrics(
            _visual_second(metrics, 0, 2.0) + _visual_second(metrics, 1, 3.0)
        )
        bins = metrics.build_analysis_bins(
            2.0,
            visual,
            metrics.AudioMetrics(()),
            (b"a" * 144, b"b" * 144),
            models.AnalysisConfig(),
        )
        self.assertTrue(all(item.audio_spike == 0.0 for item in bins))
        partial = metrics.build_analysis_bins(
            2.0,
            visual,
            metrics.AudioMetrics(
                (metrics.AudioSample(0.0, -10.0, -5.0),)
            ),
            (b"a" * 144, b"b" * 144),
            models.AnalysisConfig(),
        )
        self.assertTrue(partial[0].valid)
        self.assertFalse(partial[1].valid)

    def test_audio_uses_local_delta_not_absolute_loudness(self) -> None:
        metrics, models = _modules()
        visual = metrics.VisualMetrics(
            tuple(sample for sec in range(12) for sample in _visual_second(metrics, sec, 2.0))
        )
        constant = metrics.AudioMetrics(
            tuple(metrics.AudioSample(float(sec), -10.0, -5.0) for sec in range(12))
        )
        pulse = metrics.AudioMetrics(
            tuple(
                metrics.AudioSample(float(sec), -1.0 if sec == 6 else -20.0, -0.5 if sec == 6 else -15.0)
                for sec in range(12)
            )
        )
        frames = tuple(bytes([sec]) * 144 for sec in range(12))
        flat = metrics.build_analysis_bins(12.0, visual, constant, frames, models.AnalysisConfig())
        spiky = metrics.build_analysis_bins(12.0, visual, pulse, frames, models.AnalysisConfig())
        self.assertEqual(max(item.audio_spike for item in flat), 0.0)
        self.assertGreater(spiky[6].audio_spike, 0.9)

    def test_hybrid_motion_preserves_absolute_support_and_flat_spread_is_safe(self) -> None:
        metrics, models = _modules()
        raw = tuple(
            models.AnalysisBin(
                start_seconds=float(sec), motion_raw=12.0, valid=True, fingerprint=b"a" * 144
            )
            for sec in range(4)
        )
        normalized = metrics.normalize_motion_bins(raw, models.AnalysisConfig())
        self.assertTrue(all(math.isfinite(item.motion) for item in normalized))
        self.assertTrue(all(item.motion >= 0.6 for item in normalized))

    def test_scene_is_fixed_scale_and_hard_cuts_are_counted(self) -> None:
        metrics, models = _modules()
        samples = list(_visual_second(metrics, 0, 2.0, scene=8.0))
        samples[-1] = metrics.VisualSample(0.9, 2.0, 20.0, 0.0)
        bins = metrics.build_analysis_bins(
            1.0,
            metrics.VisualMetrics(tuple(samples)),
            metrics.AudioMetrics(()),
            (b"a" * 144,),
            models.AnalysisConfig(),
        )
        self.assertEqual(bins[0].scene_score, 1.0)
        self.assertEqual(bins[0].scene_cut_count, 1)

    def test_unclosed_freeze_is_closed_at_snapshot_duration(self) -> None:
        metrics, models = _modules()
        visual = metrics.VisualMetrics(
            tuple(sample for sec in range(3) for sample in _visual_second(metrics, sec, 2.0)),
            (metrics.FreezeInterval(1.0, math.inf),),
        )
        bins = metrics.build_analysis_bins(
            3.0,
            visual,
            metrics.AudioMetrics(()),
            (b"a" * 144,) * 3,
            models.AnalysisConfig(),
        )
        self.assertEqual(tuple(item.static_seconds for item in bins), (0.0, 1.0, 1.0))

    def test_black_ratio_is_sample_weighted_inside_each_bin(self) -> None:
        metrics, models = _modules()
        samples = tuple(
            metrics.VisualSample(index / 10, 2.0, 0.0, 1.0 if index < 2 else 0.0)
            for index in range(10)
        )
        bins = metrics.build_analysis_bins(
            1.0,
            metrics.VisualMetrics(samples),
            metrics.AudioMetrics(()),
            (b"a" * 144,),
            models.AnalysisConfig(),
        )
        self.assertAlmostEqual(bins[0].black_ratio, 0.2)

    def test_variable_segment_duration_timeline_uses_snapshot_seconds(self) -> None:
        metrics, models = _modules()
        visual = metrics.VisualMetrics(
            tuple(sample for sec in range(3) for sample in _visual_second(metrics, sec, 2.0))
        )
        bins = metrics.build_analysis_bins(
            3.75,
            visual,
            metrics.AudioMetrics(()),
            (b"a" * 144,) * 4,
            models.AnalysisConfig(),
        )
        self.assertEqual(tuple(item.start_seconds for item in bins), (0.0, 1.0, 2.0))

    def test_nonfinite_direct_metric_input_is_rejected(self) -> None:
        metrics, models = _modules()
        bad_visuals = (
            metrics.VisualMetrics(
                (metrics.VisualSample(0.0, math.nan, 0.0, 0.0),)
            ),
            metrics.VisualMetrics(
                (metrics.VisualSample(-0.1, 1.0, 0.0, 0.0),)
            ),
            metrics.VisualMetrics(
                (
                    metrics.VisualSample(0.5, 1.0, 0.0, 0.0),
                    metrics.VisualSample(0.4, 1.0, 0.0, 0.0),
                )
            ),
        )
        for visual in bad_visuals:
            with self.subTest(visual=visual), self.assertRaises(
                metrics.MetricParseError
            ):
                metrics.build_analysis_bins(
                    1.0,
                    visual,
                    metrics.AudioMetrics(()),
                    (b"a" * 144,),
                    models.AnalysisConfig(),
                )
        valid_visual = metrics.VisualMetrics(
            _visual_second(metrics, 0, 1.0)
        )
        with self.assertRaises(metrics.MetricParseError):
            metrics.build_analysis_bins(
                1.0,
                valid_visual,
                metrics.AudioMetrics(
                    (
                        metrics.AudioSample(0.5, -10.0, -5.0),
                        metrics.AudioSample(0.4, -10.0, -5.0),
                    )
                ),
                (b"a" * 144,),
                models.AnalysisConfig(),
            )


if __name__ == "__main__":
    unittest.main()
