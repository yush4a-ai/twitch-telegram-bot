from __future__ import annotations

import importlib
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "FFmpeg is not installed")
class RealFfmpegIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.fixture = cls.root / "visual-audio.ts"
        cls.silent_fixture = cls.root / "visual-only.ts"
        cls.manifest = cls.root / "visual-audio.ffconcat"
        cls.silent_manifest = cls.root / "visual-only.ffconcat"
        cls.max_window_manifest = cls.root / "max-window.ffconcat"
        cls._make_fixture(cls.fixture, include_audio=True)
        cls._make_fixture(cls.silent_fixture, include_audio=False)
        cls._write_manifest(cls.manifest, cls.fixture, 6.0)
        cls._write_manifest(cls.silent_manifest, cls.silent_fixture, 6.0)
        cls._write_manifest(
            cls.max_window_manifest, cls.fixture, 6.0, repeat=40
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    @classmethod
    def _make_fixture(cls, path: Path, *, include_audio: bool) -> None:
        command = [
            FFMPEG,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=160x90:rate=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=white:size=160x90:rate=30:d=2",
        ]
        if include_audio:
            command.extend(
                (
                    "-f",
                    "lavfi",
                    "-i",
                    "aevalsrc=if(between(t\\,2\\,3)\\,0.9*sin(440*2*PI*t)\\,0.05*sin(440*2*PI*t)):s=48000:d=6",
                )
            )
        command.extend(
            (
                "-filter_complex",
                "[0:v:0][1:v:0][2:v:0]concat=n=3:v=1:a=0,format=yuv420p[v]",
                "-map",
                "[v]",
            )
        )
        if include_audio:
            command.extend(("-map", "3:a:0", "-c:a", "mp2"))
        command.extend(
            (
                "-c:v",
                "mpeg2video",
                "-g",
                "30",
                "-f",
                "mpegts",
                str(path),
            )
        )
        completed = subprocess.run(command, capture_output=True, timeout=20)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))

    @staticmethod
    def _write_manifest(
        manifest: Path, media: Path, duration: float, *, repeat: int = 1
    ) -> None:
        quoted = media.resolve().as_posix().replace("'", "'\\''")
        entry = f"file '{quoted}'\nduration {duration:g}\n"
        manifest.write_text(
            "ffconcat version 1.0\n" + entry * repeat,
            encoding="utf-8",
        )

    def _run_production(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr.decode("utf-8", errors="replace")[-4000:],
        )
        return completed

    def test_target_ffmpeg_has_required_analysis_filters(self) -> None:
        completed = subprocess.run(
            [FFMPEG, "-hide_banner", "-filters"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        for required in ("signalstats", "scdet", "blackframe", "freezedetect", "astats", "metadata"):
            self.assertIn(required, completed.stdout)

    def test_real_metadata_is_machine_readable_and_audio_pulse_exceeds_baseline(self) -> None:
        metrics = importlib.import_module("bot.preview_analysis.metrics")
        parser = metrics.AudioMetadataParser(max_records=8)
        command = [
            FFMPEG,
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=if(between(t\\,2\\,3)\\,0.9*sin(440*2*PI*t)\\,0.05*sin(440*2*PI*t)):s=48000:d=5",
            "-af",
            "asetpts=PTS-STARTPTS,asetnsamples=n=48000:p=1,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level+Peak_level,ametadata=print:file=pipe\\\\:1:direct=1",
            "-f",
            "null",
            "-",
        ]
        completed = subprocess.run(command, check=True, capture_output=True, timeout=20)
        for line in completed.stdout.decode("utf-8").splitlines():
            parser.feed_line(line)
        audio = parser.finish()
        self.assertGreaterEqual(len(audio.samples), 4)
        levels = [sample.rms_db for sample in audio.samples]
        self.assertGreater(max(levels), min(levels) + 10.0)

    def test_production_visual_graph_emits_metrics_and_fingerprints(self) -> None:
        service = importlib.import_module("bot.preview_analysis.service")
        metrics = importlib.import_module("bot.preview_analysis.metrics")
        models = importlib.import_module("bot.preview_analysis.models")
        config = models.AnalysisConfig()
        fingerprint = self.root / "fingerprints.gray"
        command = service.build_visual_argv(FFMPEG, self.manifest, fingerprint, config)

        completed = self._run_production(command)
        parser = metrics.VisualMetadataParser(
            max_records=math.ceil(6.0 * config.analysis_fps)
            + config.visual_record_tolerance
        )
        for line in completed.stdout.decode("utf-8").splitlines():
            parser.feed_line(line)
        visual = parser.finish()

        self.assertGreaterEqual(len(visual.samples), 55)
        self.assertEqual(fingerprint.stat().st_size, 6 * config.fingerprint_frame_bytes)
        moving = [sample.ydiff for sample in visual.samples if 2.2 <= sample.timestamp < 3.8]
        static = [sample.ydiff for sample in visual.samples if 4.2 <= sample.timestamp < 5.8]
        self.assertGreater(sum(moving) / len(moving), sum(static) / len(static) + 1.0)
        self.assertGreater(max(sample.black_ratio for sample in visual.samples if sample.timestamp < 1.8), 0.9)
        self.assertGreaterEqual(max(sample.scene_score for sample in visual.samples), config.scene_cut_threshold)
        self.assertTrue(visual.freezes)

    def test_production_visual_graph_fits_output_bound_at_max_capture_window(self) -> None:
        service = importlib.import_module("bot.preview_analysis.service")
        models = importlib.import_module("bot.preview_analysis.models")
        config = models.AnalysisConfig()
        fingerprint = self.root / "max-window-fingerprints.gray"
        command = service.build_visual_argv(
            FFMPEG, self.max_window_manifest, fingerprint, config
        )

        completed = self._run_production(command)

        self.assertLessEqual(len(completed.stdout), config.metadata_total_max_bytes)
        self.assertEqual(
            fingerprint.stat().st_size,
            240 * config.fingerprint_frame_bytes,
        )

    def test_production_audio_graph_emits_pulse_and_accepts_audio_less_input(self) -> None:
        service = importlib.import_module("bot.preview_analysis.service")
        metrics = importlib.import_module("bot.preview_analysis.metrics")

        completed = self._run_production(service.build_audio_argv(FFMPEG, self.manifest))
        parser = metrics.AudioMetadataParser(max_records=8)
        for line in completed.stdout.decode("utf-8").splitlines():
            parser.feed_line(line)
        audio = parser.finish()
        self.assertGreaterEqual(len(audio.samples), 5)
        levels = [sample.rms_db for sample in audio.samples]
        self.assertGreater(max(levels), min(levels) + 10.0)

        silent = self._run_production(
            service.build_audio_argv(FFMPEG, self.silent_manifest)
        )
        silent_parser = metrics.AudioMetadataParser(max_records=8)
        for line in silent.stdout.decode("utf-8").splitlines():
            silent_parser.feed_line(line)
        self.assertEqual(silent_parser.finish().samples, ())


if __name__ == "__main__":
    unittest.main()
