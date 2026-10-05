"""Tests for the one-command release to a pinned environment.

The release touches a live bot, so the contract is fail-closed: the target is
pinned, only committed blobs are uploaded, the database copy must be consistent,
and an unfinished deployment must stop the run instead of being reported as done.
"""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from scripts import production_release as release


def _database(path: Path) -> None:
    # closing() matters: sqlite3.Connection as a context manager only commits and
    # would leave the write-ahead log unflushed, so the file reads as empty.
    with closing(sqlite3.connect(path)) as con:
        con.execute("CREATE TABLE tracked_channels (chat_id INTEGER, twitch_login TEXT)")
        con.execute("INSERT INTO tracked_channels VALUES (1, 'alpha')")
        con.execute("CREATE TABLE schema_migrations (version TEXT)")
        con.execute("INSERT INTO schema_migrations VALUES ('r1')")
        con.commit()


class PinnedTargetTests(unittest.TestCase):
    def test_production_target_is_pinned(self):
        target = release.target_for("production")
        self.assertEqual(target["environment_id"], "af6d873b-a2cf-45aa-be42-cd9efbd102a7")
        self.assertEqual(target["service"], "worker")
        self.assertEqual(target["volume_id"], "9afd2204-881d-41af-bfd8-ad395b9c9ca9")
        self.assertEqual(target["expected_bot_username"], "TwitchSignalBot")
        self.assertEqual(target["http"]["/admin/api/snapshot"], {401})
        self.assertTrue(target["require_journal_clean"])

    def test_staging_target_is_pinned_separately(self):
        target = release.target_for("staging")
        self.assertEqual(target["environment_id"], "7a873177-8ada-4b78-8732-a0bfdc1d519b")
        self.assertEqual(target["volume_id"], "3ead0ce7-ed8c-482d-946c-ee768bf909ff")
        self.assertTrue(target["base_url"].startswith("https://worker-staging-"))
        # Тестовый контур переезжает на другое имя бота, поэтому имя не пинится.
        self.assertIsNone(target["expected_bot_username"])
        self.assertFalse(target["require_journal_clean"])

    def test_unknown_environment_is_rejected(self):
        with self.assertRaises(release.ReleaseError):
            release.target_for("development")

    def test_database_backup_module_is_required_in_the_package(self):
        self.assertIn("bot/db_backup.py", release.REQUIRED_PACKAGE_FILES)


class BackupVerificationTests(unittest.TestCase):
    def test_consistent_database_is_accepted(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "copy.db"
            _database(path)
            report = release.verify_backup_file(path)
            self.assertEqual(report["integrity"], "ok")
            self.assertEqual(report["fk_violations"], 0)
            self.assertEqual(report["tracked_channels"], 1)

    def test_unreadable_copy_stops_the_release(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "broken.db"
            path.write_bytes(b"not a database")
            with self.assertRaises(release.ReleaseError):
                release.verify_backup_file(path)


class DeploymentGateTests(unittest.TestCase):
    def setUp(self):
        self.target = release.target_for("production")

    def test_success_is_returned(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d1", "status": "SUCCESS"}):
            self.assertEqual(release.wait_for_success(self.target, timeout_seconds=5)["id"], "d1")

    def test_failed_deployment_stops_the_release(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d2", "status": "FAILED"}):
            with self.assertRaises(release.ReleaseError):
                release.wait_for_success(self.target, timeout_seconds=5)

    def test_unfinished_deployment_stops_the_release(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d3", "status": "BUILDING"}), \
                patch.object(release, "POLL_SECONDS", 0):
            with self.assertRaises(release.ReleaseError):
                release.wait_for_success(self.target, timeout_seconds=0)


class PackageGateTests(unittest.TestCase):
    def test_package_without_runtime_file_is_rejected(self):
        manifest = {"files": [{"path": "main.py"}], "file_count": 1, "total_bytes": 10,
                    "manifest_sha256": "x"}
        with TemporaryDirectory() as directory, \
                patch.object(release, "build_runtime_package", return_value=manifest):
            with self.assertRaises(release.ReleaseError):
                release.build_release_package(Path(directory), "HEAD", Path(directory) / "pkg")

    def test_reviewed_package_passes(self):
        manifest = {
            "files": [{"path": name} for name in release.REQUIRED_PACKAGE_FILES],
            "file_count": len(release.REQUIRED_PACKAGE_FILES), "total_bytes": 10,
            "manifest_sha256": "x",
        }
        with TemporaryDirectory() as directory, \
                patch.object(release, "build_runtime_package", return_value=manifest):
            result = release.build_release_package(Path(directory), "HEAD", Path(directory) / "pkg")
        self.assertEqual(result["manifest_sha256"], "x")


class CleanTreeGateTests(unittest.TestCase):
    @staticmethod
    def _run_with_dirty_tree():
        class Result:
            def __init__(self, stdout="", returncode=0):
                self.stdout, self.returncode = stdout, returncode

        def fake_run(cmd, **kwargs):
            if "status" in cmd:
                return Result(stdout=" M bot/poller.py\n")
            return Result(stdout="deadbeef\n")

        return fake_run

    def test_dirty_runtime_tree_is_rejected(self):
        with patch.object(release, "run", side_effect=self._run_with_dirty_tree()):
            with self.assertRaises(release.ReleaseError):
                release.assert_release_tree_is_reviewed(Path("."), "deadbeef")

    def test_dirty_runtime_is_allowed_only_with_the_explicit_flag(self):
        with patch.object(release, "run", side_effect=self._run_with_dirty_tree()):
            release.assert_release_tree_is_reviewed(Path("."), "deadbeef", allow_dirty=True)


class BackupTransferTests(unittest.TestCase):
    """Backups are pulled over ssh: the volume API needs a CLI link we cannot set."""

    def test_payload_between_markers_is_decoded(self):
        import base64

        raw = b"sqlite-bytes"
        output = ("noise\nBACKUP_B64_START\n"
                  + base64.b64encode(raw).decode("ascii")
                  + "\nBACKUP_B64_END\ntrailing\n")
        self.assertEqual(release.decode_ssh_payload(output), raw)

    def test_missing_markers_stop_the_release(self):
        with self.assertRaises(release.ReleaseError):
            release.decode_ssh_payload("no markers here")

    def test_invalid_base64_stops_the_release(self):
        output = "BACKUP_B64_START\n!!!not-base64!!!\nBACKUP_B64_END\n"
        with self.assertRaises(release.ReleaseError):
            release.decode_ssh_payload(output)


class ArtifactVerificationTests(unittest.TestCase):
    """A successful deploy alone does not prove which files are in the container."""

    manifest = {"files": [{"path": "main.py", "sha256": "a" * 64},
                          {"path": "bot/poller.py", "sha256": "b" * 64}]}

    def setUp(self):
        self.target = release.target_for("production")

    def test_matching_artifact_passes(self):
        output = "ARTIFACT_TOTAL=2\nARTIFACT_MISSING=0\nARTIFACT_MISMATCHED=0\n"
        with patch.object(release, "run", return_value=SimpleNamespace(stdout=output)):
            report = release.verify_artifact(self.target, self.manifest)
        self.assertEqual(report, {"files": 2, "missing": 0, "mismatched": 0})

    def test_missing_file_stops_the_release(self):
        output = ("ARTIFACT_TOTAL=2\nARTIFACT_MISSING=1\nARTIFACT_MISMATCHED=0\n"
                  "ARTIFACT_MISSING_FILES=bot/poller.py\n")
        with patch.object(release, "run", return_value=SimpleNamespace(stdout=output)):
            with self.assertRaises(release.ReleaseError):
                release.verify_artifact(self.target, self.manifest)

    def test_mismatched_file_stops_the_release(self):
        output = ("ARTIFACT_TOTAL=2\nARTIFACT_MISSING=0\nARTIFACT_MISMATCHED=1\n"
                  "ARTIFACT_MISMATCHED_FILES=main.py\n")
        with patch.object(release, "run", return_value=SimpleNamespace(stdout=output)):
            with self.assertRaises(release.ReleaseError):
                release.verify_artifact(self.target, self.manifest)

    def test_empty_output_stops_the_release(self):
        with patch.object(release, "run", return_value=SimpleNamespace(stdout="")):
            with self.assertRaises(release.ReleaseError):
                release.verify_artifact(self.target, self.manifest)

    def test_report_lines_are_parsed_without_noise(self):
        values = release.parse_artifact_report(
            "some log line\nARTIFACT_TOTAL=166\nARTIFACT_MISSING=0\nARTIFACT_MISMATCHED=0\n"
        )
        self.assertEqual(values["ARTIFACT_TOTAL"], "166")
        self.assertEqual(values["ARTIFACT_MISSING"], "0")


if __name__ == "__main__":
    unittest.main()
