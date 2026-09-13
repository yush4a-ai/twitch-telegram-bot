from __future__ import annotations

import ast
import importlib
import re
import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STREAMLINK = shutil.which("streamlink")
LOCAL_COMMAND_SUFFIXES = (
    ("--version",),
    ("--no-config", "--no-plugin-sideloading", "--plugins"),
    ("--help",),
)
REQUIRED_OPTIONS = {
    "--no-config",
    "--no-plugin-sideloading",
    "--no-plugin-cache",
    "--webbrowser",
    "--loglevel",
    "--stream-url",
    "--stream-sorting-excludes",
    "--twitch-supported-codecs",
    "--plugins",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.add(prefix + (node.module or ""))
    return names


class PreviewSourceBoundaryTests(unittest.TestCase):
    def test_preview_capture_has_no_preview_source_streamlink_or_twitch_imports(self) -> None:
        forbidden = ("preview_source", "streamlink", "twitch")
        for path in (REPO_ROOT / "bot" / "preview_capture").glob("*.py"):
            with self.subTest(path=path.name):
                imports = _imports(path)
                rendered = " ".join(imports).lower()
                for token in forbidden:
                    self.assertNotIn(token, rendered)

    def test_preview_source_depends_on_capture_but_not_core_or_telegram(self) -> None:
        source_root = REPO_ROOT / "bot" / "preview_source"
        self.assertTrue(source_root.is_dir(), "P4B preview_source package must exist")
        rendered = " ".join(
            import_name
            for path in source_root.glob("*.py")
            for import_name in _imports(path)
        ).lower()

        self.assertIn("preview_capture", rendered)
        for forbidden in ("aiogram", "database", "live_post", "stream_poller", "main"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_preview_runtime_has_no_p4b_wiring(self) -> None:
        path = REPO_ROOT / "bot" / "preview_runtime.py"
        text = path.read_text(encoding="utf-8").lower()

        self.assertNotIn("preview_source", text)
        self.assertNotIn("twitchcapturesource", text)

    def test_no_streamlink_dependency_or_new_environment_configuration(self) -> None:
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        config = (REPO_ROOT / "bot" / "config.py").read_text(encoding="utf-8")

        streamlink_requirements = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip().lower().startswith("streamlink")
        ]
        self.assertEqual(streamlink_requirements, ["streamlink==8.5.0"])
        self.assertNotIn("streamlink", config.lower())
        self.assertNotIn("preview_source", config.lower())

    def test_package_import_has_no_core_runtime_or_telegram_side_effects(self) -> None:
        source = importlib.import_module("bot.preview_source")
        public = set(source.__all__)

        self.assertIn("TwitchPlaybackResolver", public)
        self.assertIn("TwitchCaptureSource", public)
        self.assertNotIn("PreviewManager", public)
        self.assertNotIn("Analyzer", public)

    def test_optional_commands_are_local_only_and_never_contain_a_url(self) -> None:
        rendered = " ".join(part for command in LOCAL_COMMAND_SUFFIXES for part in command)

        self.assertNotIn("http://", rendered)
        self.assertNotIn("https://", rendered)
        self.assertNotIn("twitch.tv", rendered)
        self.assertNotIn("--json", rendered)


@unittest.skipUnless(STREAMLINK, "Streamlink executable is not installed")
class LocalStreamlinkContractTests(unittest.TestCase):
    def _run(self, suffix: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (STREAMLINK, *suffix),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )

    def test_local_streamlink_version_is_target_major_8(self) -> None:
        result = self._run(LOCAL_COMMAND_SUFFIXES[0])
        match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout + result.stderr)

        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 8)

    def test_local_plain_plugin_probe_has_exact_twitch_token(self) -> None:
        result = self._run(LOCAL_COMMAND_SUFFIXES[1])
        tokens = set(re.findall(r"[A-Za-z0-9_-]+", result.stdout.lower()))

        self.assertEqual(result.returncode, 0)
        self.assertIn("twitch", tokens)

    def test_local_help_exposes_every_required_cli_option(self) -> None:
        result = self._run(LOCAL_COMMAND_SUFFIXES[2])
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0)
        self.assertTrue(REQUIRED_OPTIONS.issubset(set(re.findall(r"--[a-z0-9-]+", output))))


if __name__ == "__main__":
    unittest.main()
