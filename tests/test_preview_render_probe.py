from __future__ import annotations

import importlib
import json
import unittest
from fractions import Fraction


def _probe():
    try:
        return importlib.import_module("bot.preview_render.probe")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render probe boundary must exist") from exc


def _models():
    try:
        return importlib.import_module("bot.preview_render.models")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render models must exist") from exc


class FakeCommandRunner:
    def __init__(self, ffmpeg_major: int = 9, ffprobe_major: int = 9) -> None:
        self.ffmpeg_major = ffmpeg_major
        self.ffprobe_major = ffprobe_major
        self.calls: list[tuple[tuple[str, ...], float, int]] = []

    async def run_command(self, argv, *, timeout: float, max_output: int):
        command = tuple(argv)
        self.calls.append((command, timeout, max_output))
        executable = command[0].lower()
        if "-version" in command:
            product = "ffprobe" if "ffprobe" in executable else "ffmpeg"
            major = self.ffprobe_major if product == "ffprobe" else self.ffmpeg_major
            return 0, f"{product} version {major}.0-test\n".encode()
        if "-encoders" in command:
            return 0, b" V..... libx264 H.264\n"
        if "-muxers" in command:
            return 0, b" E  mp4 MP4 muxer\n"
        if "-filters" in command:
            filters = "\n".join(
                f" ... {name} test" for name in (
                    "trim", "setpts", "concat", "scale", "pad", "setsar", "format", "fps"
                )
            )
            return 0, filters.encode()
        return 1, b""


def _source_payload(
    *,
    avg: str = "30/1",
    nominal: str = "30/1",
    pix_fmt: str = "yuv420p",
    field_order: str = "progressive",
    sar: str = "1:1",
    transfer: str = "bt709",
    rotation: int = 0,
) -> bytes:
    return json.dumps(
        {
            "programs": [{"streams": [{"index": 0}]}],
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "width": 1280,
                    "height": 720,
                    "pix_fmt": pix_fmt,
                    "field_order": field_order,
                    "sample_aspect_ratio": sar,
                    "avg_frame_rate": avg,
                    "r_frame_rate": nominal,
                    "color_transfer": transfer,
                    "tags": {"rotate": str(rotation)},
                }
            ],
        }
    ).encode()


def _output_payload(
    *, fps: str = "30/1", duration: str = "3.000000", size: str = "2048"
) -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 854,
                    "height": 480,
                    "pix_fmt": "yuv420p",
                    "sample_aspect_ratio": "1:1",
                    "avg_frame_rate": fps,
                    "r_frame_rate": fps,
                }
            ],
            "format": {"duration": duration, "size": size},
        }
    ).encode()


class CapabilityProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_different_paths_with_same_supported_major_are_allowed(self) -> None:
        probe = _probe()
        runner = FakeCommandRunner()
        result = await probe.probe_capability(
            "C:/ffmpeg/bin/ffmpeg.exe",
            "D:/probe/bin/ffprobe.exe",
            runner=runner,
            timeout=5.0,
        )
        self.assertTrue(result.available)
        self.assertEqual(result.major_version, 9)
        self.assertEqual(result.ffmpeg_executable, "C:/ffmpeg/bin/ffmpeg.exe")
        self.assertEqual(result.ffprobe_executable, "D:/probe/bin/ffprobe.exe")
        self.assertTrue(all(timeout == 5.0 for _, timeout, _ in runner.calls))
        self.assertTrue(all(limit <= 256 * 1024 for _, _, limit in runner.calls))

    async def test_major_mismatch_is_rejected(self) -> None:
        probe = _probe()
        models = _models()
        result = await probe.probe_capability(
            "ffmpeg",
            "ffprobe",
            runner=FakeCommandRunner(ffmpeg_major=9, ffprobe_major=8),
        )
        self.assertFalse(result.available)
        self.assertEqual(result.reason, models.RenderCapabilityReason.MAJOR_MISMATCH)

    async def test_missing_encoder_muxer_or_filter_fails_closed(self) -> None:
        probe = _probe()
        models = _models()

        class MissingRunner(FakeCommandRunner):
            async def run_command(self, argv, *, timeout: float, max_output: int):
                code, output = await super().run_command(
                    argv, timeout=timeout, max_output=max_output
                )
                if "-filters" in tuple(argv):
                    output = output.replace(b" ... pad test\n", b"")
                    output += b"\n ... apad audio pad delay"
                return code, output

        result = await probe.probe_capability(
            "ffmpeg", "ffprobe", runner=MissingRunner()
        )
        self.assertFalse(result.available)
        self.assertEqual(
            result.reason, models.RenderCapabilityReason.UNSUPPORTED_CAPABILITY
        )


class SourceProbeTests(unittest.TestCase):
    def test_json_uses_top_level_stream_and_preserves_fractional_rates(self) -> None:
        probe = _probe()
        for rate in ("24/1", "24000/1001", "30/1", "30000/1001", "60/1", "60000/1001"):
            with self.subTest(rate=rate):
                parsed = probe.parse_source_probe(
                    _source_payload(avg=rate, nominal=rate)
                )
                self.assertEqual(parsed.fps, Fraction(rate))

    def test_small_nominal_timestamp_jitter_is_accepted_without_guessing(self) -> None:
        probe = _probe()
        parsed = probe.parse_source_probe(
            _source_payload(avg="30000/1001", nominal="30/1")
        )
        self.assertEqual(parsed.fps, Fraction(30000, 1001))

    def test_zero_vfr_and_genuine_rate_disagreement_are_rejected(self) -> None:
        probe = _probe()
        for payload in (
            _source_payload(avg="0/0", nominal="0/0"),
            _source_payload(avg="30/1", nominal="60/1"),
        ):
            with self.assertRaises(probe.SourceProbeError) as caught:
                probe.parse_source_probe(payload)
            self.assertEqual(caught.exception.diagnostic_code, "source_fps_unreliable")

    def test_cross_window_rate_mismatch_is_rejected_but_jitter_is_accepted(self) -> None:
        probe = _probe()
        thirty = probe.parse_source_probe(_source_payload(avg="30/1", nominal="30/1"))
        jitter = probe.parse_source_probe(
            _source_payload(avg="30000/1001", nominal="30/1")
        )
        sixty = probe.parse_source_probe(_source_payload(avg="60/1", nominal="60/1"))
        self.assertEqual(probe.resolve_common_fps((thirty, jitter)), Fraction(30, 1))
        with self.assertRaises(probe.SourceProbeError):
            probe.resolve_common_fps((thirty, sixty))

    def test_unsupported_hdr_high_bit_depth_interlace_rotation_and_sar_fail_typed(self) -> None:
        probe = _probe()
        payloads = (
            _source_payload(transfer="smpte2084"),
            _source_payload(transfer="arib-std-b67"),
            _source_payload(pix_fmt="yuv420p10le"),
            _source_payload(field_order="tt"),
            _source_payload(rotation=90),
            _source_payload(sar="0:1"),
        )
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(
                probe.SourceProbeError
            ) as caught:
                probe.parse_source_probe(payload)
            self.assertEqual(caught.exception.diagnostic_code, "source_unsupported")

    def test_probe_argv_requests_named_json_fields_without_flat_text_ordering(self) -> None:
        probe = _probe()
        argv = probe.build_source_probe_argv("ffprobe", "window.ffconcat")
        self.assertIn("json", argv)
        self.assertIn("-show_entries", argv)
        fields = argv[argv.index("-show_entries") + 1]
        for field in ("avg_frame_rate", "r_frame_rate", "field_order", "sample_aspect_ratio"):
            self.assertIn(field, fields)
        self.assertNotIn("noprint_wrappers", " ".join(argv))

    def test_high_rate_requires_bounded_machine_readable_frame_confirmation(self) -> None:
        probe = _probe()
        argv = probe.build_cadence_probe_argv("ffprobe", "window.ffconcat")
        self.assertIn("json", argv)
        self.assertIn("-read_intervals", argv)
        self.assertIn("frame=best_effort_timestamp_time", argv)
        timestamps = [index / 120 for index in range(121)]
        payload = json.dumps(
            {
                "frames": [
                    {"best_effort_timestamp_time": format(value, ".9f")}
                    for value in timestamps
                ]
            }
        ).encode()
        probe.confirm_high_fps_cadence(payload, Fraction(120, 1))
        with self.assertRaises(probe.SourceProbeError):
            probe.confirm_high_fps_cadence(payload, Fraction(90, 1))

    def test_non_square_source_sar_is_rejected_instead_of_stretched(self) -> None:
        probe = _probe()
        with self.assertRaises(probe.SourceProbeError) as caught:
            probe.parse_source_probe(_source_payload(sar="4:3"))
        self.assertEqual(caught.exception.diagnostic_code, "source_unsupported")


class OutputProbeTests(unittest.TestCase):
    def test_output_validation_proves_codec_geometry_audio_sar_size_duration_and_fps(self) -> None:
        probe = _probe()
        config = _models().RenderConfig()
        metadata = probe.validate_output_probe(
            _output_payload(fps="30000/1001"),
            expected_duration=3.0,
            source_fps=Fraction(30000, 1001),
            config=config,
            actual_size=2048,
        )
        self.assertEqual(metadata.fps, Fraction(30000, 1001))
        self.assertEqual((metadata.width, metadata.height), (854, 480))
        self.assertEqual(metadata.size_bytes, 2048)

    def test_output_never_upsamples_low_fps_and_caps_high_fps(self) -> None:
        probe = _probe()
        config = _models().RenderConfig()
        cases = (
            (Fraction(24, 1), "24/1"),
            (Fraction(30, 1), "30/1"),
            (Fraction(60, 1), "60/1"),
            (Fraction(120, 1), "60/1"),
        )
        for source, output in cases:
            with self.subTest(source=source):
                metadata = probe.validate_output_probe(
                    _output_payload(fps=output),
                    expected_duration=3.0,
                    source_fps=source,
                    config=config,
                    actual_size=2048,
                )
                self.assertEqual(metadata.fps, Fraction(output))
        with self.assertRaises(probe.OutputProbeError):
            probe.validate_output_probe(
                _output_payload(fps="60/1"),
                expected_duration=3.0,
                source_fps=Fraction(24, 1),
                config=config,
                actual_size=2048,
            )

    def test_invalid_stream_audio_codec_geometry_duration_and_oversize_are_rejected(self) -> None:
        probe = _probe()
        config = _models().RenderConfig()
        base = json.loads(_output_payload())
        cases = []
        for key, value in (("codec_name", "hevc"), ("width", 640), ("pix_fmt", "yuv444p"), ("sample_aspect_ratio", "4:3")):
            changed = json.loads(json.dumps(base))
            changed["streams"][0][key] = value
            cases.append(json.dumps(changed).encode())
        audio = json.loads(json.dumps(base))
        audio["streams"].append({"codec_type": "audio"})
        cases.append(json.dumps(audio).encode())
        cases.append(_output_payload(duration="4.0"))
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(
                probe.OutputProbeError
            ):
                probe.validate_output_probe(
                    payload,
                    expected_duration=3.0,
                    source_fps=Fraction(30, 1),
                    config=config,
                    actual_size=2048,
                )
        with self.assertRaises(probe.OutputProbeError):
            probe.validate_output_probe(
                _output_payload(size=str(config.max_output_bytes + 1)),
                expected_duration=3.0,
                source_fps=Fraction(30, 1),
                config=config,
                actual_size=config.max_output_bytes + 1,
            )


if __name__ == "__main__":
    unittest.main()
