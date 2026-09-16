from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAILPACK_PATH = PROJECT_ROOT / "railpack.json"
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"

FFMPEG_IMAGE = (
    "mwader/static-ffmpeg:9.0.1@"
    "sha256:54e55b0cb8f672870fc38ceb2e6c411855cb3b39c505f5f3b2505ee01ed5f2b7"
)
PREVIEW_BIN = "/app/.preview-bin"

UNCHANGED_FILE_SHA256 = {
    ".python-version": (
        "5b703ca38d3fd391f3e889f7ba893f94b921ff9e7b7e5d8d6622d5f7daca7049"
    ),
    "Procfile": (
        "c52a15bf646894a3a95343b5c792016ec9713f6c2826cc657b266b694cbd8345"
    ),
}


class DeploymentPackagingTests(unittest.TestCase):
    def _config(self) -> dict[str, object]:
        if not RAILPACK_PATH.is_file():
            self.fail("railpack.json must exist")
        try:
            config = json.loads(RAILPACK_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self.fail(f"railpack.json must be valid UTF-8 JSON: {error}")
        self.assertIsInstance(config, dict)
        return config

    def _copy_commands(self) -> list[dict[str, str]]:
        config = self._config()
        steps = config.get("steps")
        self.assertIsInstance(steps, dict)
        preview_step = steps.get("preview-tools")
        self.assertIsInstance(preview_step, dict)
        commands = preview_step.get("commands")
        self.assertIsInstance(commands, list)
        copies = [command for command in commands if isinstance(command, dict)]
        self.assertEqual(copies, commands)
        return copies

    def test_railpack_config_exists_and_has_basic_structure(self) -> None:
        config = self._config()

        self.assertEqual(set(config), {"$schema", "packages", "steps", "deploy"})
        self.assertEqual(config.get("$schema"), "https://schema.railpack.com")
        self.assertIsInstance(config.get("packages"), dict)
        self.assertIsInstance(config.get("steps"), dict)
        self.assertIsInstance(config.get("deploy"), dict)
        self.assertEqual(set(config["packages"]), {"python"})
        self.assertEqual(set(config["steps"]), {"preview-tools"})
        self.assertEqual(
            set(config["steps"]["preview-tools"]), {"commands", "deployOutputs"}
        )
        self.assertEqual(set(config["deploy"]), {"paths"})

    def test_python_package_is_exactly_versioned_without_pipx(self) -> None:
        packages = self._config()["packages"]

        self.assertEqual(packages.get("python"), "3.12.10")
        self.assertNotIn("pipx", packages)
        self.assertNotIn("pipx:streamlink", packages)
        self.assertNotIn("latest", json.dumps(packages).lower())

    def test_streamlink_is_pinned_once_in_application_requirements(self) -> None:
        requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        streamlink_requirements = [
            line.strip()
            for line in requirements
            if line.strip().lower().startswith("streamlink")
        ]

        self.assertEqual(streamlink_requirements, ["streamlink==8.5.0"])

    def test_streamlink_urllib3_compatibility_is_pinned(self) -> None:
        requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        urllib3_requirements = [
            line.strip()
            for line in requirements
            if line.strip().lower().startswith("urllib3")
        ]

        self.assertEqual(urllib3_requirements, ["urllib3==2.7.0"])

    def test_railpack_does_not_install_streamlink_a_second_time(self) -> None:
        serialized = json.dumps(self._config(), sort_keys=True).lower()

        self.assertNotIn("streamlink", serialized)
        self.assertNotIn("pipx", serialized)

    def test_ffmpeg_source_is_tagged_and_digest_pinned(self) -> None:
        copies = self._copy_commands()

        self.assertEqual({copy.get("image") for copy in copies}, {FFMPEG_IMAGE})
        self.assertIn(":9.0.1@sha256:", FFMPEG_IMAGE)
        self.assertEqual(len(FFMPEG_IMAGE.rsplit("sha256:", 1)[1]), 64)

    def test_exactly_ffmpeg_and_ffprobe_are_copied_to_scoped_directory(self) -> None:
        copies = self._copy_commands()

        self.assertEqual(len(copies), 2)
        self.assertEqual({copy.get("src") for copy in copies}, {"/ffmpeg", "/ffprobe"})
        self.assertEqual(
            {copy.get("dest") for copy in copies},
            {f"{PREVIEW_BIN}/ffmpeg", f"{PREVIEW_BIN}/ffprobe"},
        )
        self.assertEqual(
            {(copy.get("src"), copy.get("dest")) for copy in copies},
            {
                ("/ffmpeg", f"{PREVIEW_BIN}/ffmpeg"),
                ("/ffprobe", f"{PREVIEW_BIN}/ffprobe"),
            },
        )
        self.assertTrue(
            all(set(copy) == {"image", "src", "dest"} for copy in copies)
        )

    def test_preview_step_exports_only_the_scoped_binary_directory(self) -> None:
        preview_step = self._config()["steps"]["preview-tools"]

        self.assertEqual(preview_step.get("deployOutputs"), [{"include": [PREVIEW_BIN]}])
        self.assertNotIn("/data", json.dumps(preview_step))

    def test_deploy_path_extends_generated_path_without_root(self) -> None:
        deploy = self._config()["deploy"]
        paths = deploy.get("paths")

        self.assertIsInstance(paths, list)
        self.assertEqual(paths, ["...", PREVIEW_BIN])
        self.assertIn("...", paths)
        self.assertIn(PREVIEW_BIN, paths)
        self.assertNotIn("/", paths)
        self.assertEqual(paths.count(PREVIEW_BIN), 1)

    def test_generated_python_plan_is_not_reimplemented_or_overridden(self) -> None:
        config = self._config()
        deploy = config["deploy"]
        serialized = json.dumps(config, sort_keys=True)

        self.assertNotIn("startCommand", deploy)
        self.assertNotIn("python main.py", serialized)
        self.assertNotIn("pip install", serialized)
        self.assertNotIn("/app/.venv", serialized)
        self.assertNotIn("base", deploy)
        if "inputs" in deploy:
            self.assertIn("...", deploy["inputs"])

    def test_config_does_not_enable_preview_or_add_runtime_variables(self) -> None:
        config = self._config()
        deploy = config["deploy"]
        serialized = json.dumps(config, sort_keys=True).lower()

        self.assertNotIn("variables", deploy)
        for forbidden in (
            "preview_runtime_enabled",
            "streamlink_path",
            "ffmpeg_path",
            "ffprobe_path",
            "tmpdir",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_config_has_no_secrets_or_persistent_media_paths(self) -> None:
        serialized = json.dumps(self._config(), sort_keys=True).lower()

        self.assertNotIn("secrets", serialized)
        self.assertNotIn("/data", serialized)
        for marker in ("token", "password", "secret", "api_key", "private_key"):
            self.assertNotIn(marker, serialized)

    def test_no_parallel_dockerfile_or_nixpacks_config_exists(self) -> None:
        self.assertFalse((PROJECT_ROOT / "Dockerfile").exists())
        self.assertFalse((PROJECT_ROOT / "nixpacks.toml").exists())

    def test_existing_python_manifests_are_byte_for_byte_unchanged(self) -> None:
        for relative_path, expected_sha256 in UNCHANGED_FILE_SHA256.items():
            with self.subTest(path=relative_path):
                payload = (PROJECT_ROOT / relative_path).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_sha256)


if __name__ == "__main__":
    unittest.main()
