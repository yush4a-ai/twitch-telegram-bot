"""Tests for the one-command production release.

The release touches the live bot, so the contract is fail-closed: the target is
pinned, only committed blobs are uploaded, the database copy must be consistent,
and an unfinished deployment must stop the run instead of being reported as done.
"""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
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
    def test_release_target_is_pinned_to_production(self):
        self.assertEqual(release.PROJECT_ID, "14282646-e318-4b80-b35d-4369270de255")
        self.assertEqual(release.ENVIRONMENT, "production")
        self.assertEqual(release.SERVICE, "worker")
        self.assertEqual(release.EXPECTED_BOT_USERNAME, "TwitchSignalBot")
        self.assertTrue(release.PUBLIC_BASE_URL.startswith("https://worker-production-"))

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
    def test_success_is_returned(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d1", "status": "SUCCESS"}):
            self.assertEqual(release.wait_for_success(timeout_seconds=5)["id"], "d1")

    def test_failed_deployment_stops_the_release(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d2", "status": "FAILED"}):
            with self.assertRaises(release.ReleaseError):
                release.wait_for_success(timeout_seconds=5)

    def test_unfinished_deployment_stops_the_release(self):
        with patch.object(release, "latest_deployment", return_value={"id": "d3", "status": "BUILDING"}), \
                patch.object(release, "POLL_SECONDS", 0):
            with self.assertRaises(release.ReleaseError):
                release.wait_for_success(timeout_seconds=0)


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


if __name__ == "__main__":
    unittest.main()
