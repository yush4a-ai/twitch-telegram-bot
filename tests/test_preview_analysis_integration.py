from __future__ import annotations

import importlib
import shutil
import subprocess
import unittest


FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "FFmpeg is not installed")
class RealFfmpegIntegrationTests(unittest.TestCase):
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
            "aevalsrc=if(between(t,2,3),0.9*sin(440*2*PI*t),0.05*sin(440*2*PI*t)):s=48000:d=5",
            "-af",
            "asetpts=PTS-STARTPTS,asetnsamples=n=48000:p=1,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level+Peak_level,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=pipe\\:1,ametadata=print:key=lavfi.astats.Overall.Peak_level:file=pipe\\:1",
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


if __name__ == "__main__":
    unittest.main()
