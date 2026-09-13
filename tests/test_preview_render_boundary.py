from __future__ import annotations

import importlib
import unittest
from pathlib import Path


PROJECT = Path(__file__).parents[1]


class PreviewRenderBoundaryTests(unittest.TestCase):
    def test_public_surface_and_expected_modules_exist(self) -> None:
        try:
            package = importlib.import_module("bot.preview_render")
        except ModuleNotFoundError as exc:
            raise AssertionError("P6 preview_render package must exist") from exc

        expected = {
            "RenderConfig",
            "RenderStatus",
            "RenderResult",
            "RenderCapability",
            "RenderCapabilityReason",
            "RenderedPreview",
            "PreviewRenderer",
        }
        self.assertTrue(expected.issubset(set(package.__all__)))
        self.assertEqual(
            {path.name for path in (PROJECT / "bot" / "preview_render").glob("*.py")},
            {
                "__init__.py",
                "models.py",
                "input.py",
                "probe.py",
                "process.py",
                "storage.py",
                "renderer.py",
            },
        )

    def test_package_has_no_p2b_dependency_and_stays_outside_lower_runtime(self) -> None:
        package = PROJECT / "bot" / "preview_render"
        self.assertTrue(package.is_dir())
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        ).lower()
        self.assertNotIn("localvideo", source)
        self.assertNotIn("bot.preview_source", source)
        self.assertNotIn("aiogram", source)
        for relative in (
            "bot/preview_runtime.py",
            "bot/poller.py",
            "bot/live_post.py",
        ):
            existing = (PROJECT / relative).read_text(encoding="utf-8").lower()
            self.assertNotIn("preview_render", existing)


if __name__ == "__main__":
    unittest.main()
