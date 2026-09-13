from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "bot" / "preview_analysis"


def _analysis():
    try:
        return importlib.import_module("bot.preview_analysis")
    except ModuleNotFoundError as exc:
        raise AssertionError("P5 preview_analysis package must exist") from exc


class PreviewAnalysisBoundaryTests(unittest.TestCase):
    def test_public_contract_is_narrow_and_importable(self) -> None:
        analysis = _analysis()
        self.assertEqual(
            set(analysis.__all__),
            {
                "AnalysisConfig",
                "AnalysisResult",
                "AnalysisStatus",
                "HighlightAnalyzer",
                "HighlightSelection",
                "HighlightWindow",
            },
        )

    def test_required_module_layout_exists(self) -> None:
        self.assertEqual(
            {path.name for path in PACKAGE.glob("*.py")},
            {
                "__init__.py",
                "models.py",
                "concat.py",
                "process.py",
                "metrics.py",
                "selector.py",
                "service.py",
            },
        )

    def test_production_imports_only_preview_capture_boundary(self) -> None:
        forbidden = {
            "aiogram",
            "bot.database",
            "bot.live_post",
            "bot.poller",
            "bot.preview_runtime",
            "bot.preview_source",
        }
        imports: set[str] = set()
        for path in PACKAGE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
        self.assertFalse(
            sorted(name for name in imports if name in forbidden), imports
        )

    def test_analysis_is_dormant_and_not_wired_into_runtime(self) -> None:
        runtime_files = (
            ROOT / "main.py",
            ROOT / "bot" / "preview_runtime.py",
            ROOT / "bot" / "poller.py",
            ROOT / "bot" / "live_post.py",
        )
        for path in runtime_files:
            self.assertNotIn(
                "preview_analysis", path.read_text(encoding="utf-8"), path
            )

    def test_no_heavy_fingerprint_dependencies_are_imported(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")
        ).lower()
        self.assertNotIn("numpy", source)
        self.assertNotIn("opencv", source)
        self.assertNotIn("cv2", source)

    def test_policy_does_not_read_environment_variables(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")
        )
        self.assertNotIn("getenv", source)
        self.assertNotIn("environ", source)

    def test_p5_contains_no_final_media_or_runtime_components(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py")
        )
        for forbidden in (
            "InputMediaVideo",
            "edit_message_media",
            "PreviewManager",
            "LivePostUpdater",
            "Streamlink",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
