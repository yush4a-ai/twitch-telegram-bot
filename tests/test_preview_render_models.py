from __future__ import annotations

import importlib
import unittest
from fractions import Fraction
from pathlib import Path


def _models():
    try:
        return importlib.import_module("bot.preview_render.models")
    except ModuleNotFoundError as exc:
        raise AssertionError("P6 render models must exist") from exc


class RenderModelTests(unittest.TestCase):
    def test_config_has_fixed_telegram_output_contract_and_bounded_timeouts(self) -> None:
        models = _models()
        config = models.RenderConfig()
        self.assertEqual((config.width, config.height), (854, 480))
        self.assertEqual(config.max_output_bytes, 16 * 1024 * 1024)
        self.assertEqual(config.capability_timeout, 5.0)
        self.assertEqual(config.source_probe_timeout, 15.0)
        self.assertEqual(config.encode_timeout, 60.0)
        self.assertEqual(config.output_probe_timeout, 10.0)
        self.assertEqual(config.overall_timeout, 90.0)
        self.assertGreaterEqual(config.output_poll_seconds, 0.1)
        self.assertLessEqual(config.output_poll_seconds, 0.25)

    def test_invalid_config_values_are_rejected(self) -> None:
        models = _models()
        for changes in (
            {"width": 0},
            {"height": -1},
            {"max_output_bytes": 0},
            {"encode_timeout": float("nan")},
            {"output_poll_seconds": 0.01},
            {"output_poll_seconds": 0.5},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                models.RenderConfig(**changes)

    def test_result_requires_artifact_only_for_success_and_allowlists_diagnostics(self) -> None:
        models = _models()
        artifact = object()
        success = models.RenderResult(models.RenderStatus.SUCCESS, artifact=artifact)
        self.assertIs(success.artifact, artifact)
        with self.assertRaises(ValueError):
            models.RenderResult(models.RenderStatus.SUCCESS)
        with self.assertRaises(ValueError):
            models.RenderResult(models.RenderStatus.PROCESS_FAILED, artifact=artifact)
        with self.assertRaises(ValueError):
            models.RenderResult(
                models.RenderStatus.INTERNAL_ERROR,
                diagnostic_code="raw ffmpeg stderr and a private path",
            )

    def test_capability_contains_only_typed_sanitized_fields(self) -> None:
        models = _models()
        available = models.RenderCapability(
            available=True,
            ffmpeg_executable="C:/tools/ffmpeg.exe",
            ffprobe_executable="D:/other/ffprobe.exe",
            major_version=9,
        )
        self.assertIsNone(available.reason)
        unavailable = models.RenderCapability(
            available=False,
            reason=models.RenderCapabilityReason.MAJOR_MISMATCH,
        )
        self.assertFalse(unavailable.available)
        with self.assertRaises(ValueError):
            models.RenderCapability(available=True)

    def test_rendered_metadata_uses_exact_fraction(self) -> None:
        models = _models()
        metadata = models.RenderedPreviewMetadata(
            duration_seconds=3.0,
            width=854,
            height=480,
            fps=Fraction(30000, 1001),
            size_bytes=1024,
        )
        self.assertEqual(metadata.fps, Fraction(30000, 1001))
        with self.assertRaises(ValueError):
            models.RenderedPreviewMetadata(
                duration_seconds=0,
                width=854,
                height=480,
                fps=Fraction(30, 1),
                size_bytes=1,
            )


if __name__ == "__main__":
    unittest.main()
