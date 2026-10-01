"""The R5 drill must never open the active Railway database."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.staging_mock_billing import run_drill, verify_staging_runtime


TARGET = json.loads(
    (Path(__file__).parents[1] / "scripts" / "staging_target.json").read_text(encoding="utf-8")
)


def staging_environment() -> dict[str, str]:
    return {
        "RAILWAY_PROJECT_ID": TARGET["project_id"],
        "RAILWAY_ENVIRONMENT_ID": TARGET["staging_environment_id"],
        "RAILWAY_SERVICE_ID": TARGET["service_id"],
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "RAILWAY_VOLUME_MOUNT_PATH": "/data",
        "DB_PATH": "/data/bot.db",
        "OWNER_CHAT_ID": "425785231",
    }


class StagingMockBillingGuardTests(unittest.TestCase):
    def test_only_exact_staging_runtime_is_accepted(self):
        valid = staging_environment()
        verify_staging_runtime(valid)
        bad_values = {
            "RAILWAY_PROJECT_ID": "other-project",
            "RAILWAY_ENVIRONMENT_ID": TARGET["production_environment_id"],
            "RAILWAY_SERVICE_ID": "other-service",
            "RAILWAY_ENVIRONMENT_NAME": "production",
            "RAILWAY_VOLUME_MOUNT_PATH": "/other",
            "DB_PATH": "/tmp/other.db",
            "OWNER_CHAT_ID": "425785232",
        }
        for key, value in bad_values.items():
            with self.subTest(key=key):
                invalid = valid | {key: value}
                with self.assertRaises(PermissionError):
                    verify_staging_runtime(invalid)
                invalid.pop(key)
                with self.assertRaises(PermissionError):
                    verify_staging_runtime(invalid)


class StagingMockBillingDrillTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_database_lifecycle_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "active-sentinel.db"
            sentinel.write_bytes(b"active database sentinel")
            report = await run_drill(staging_environment(), temp_root=root)
            self.assertEqual(sentinel.read_bytes(), b"active database sentinel")
            self.assertEqual(sorted(root.iterdir()), [sentinel])
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["paid"], 1)
        self.assertEqual(report["refunded"], 1)
        self.assertEqual(report["cancelled"], 1)
        self.assertEqual(report["expired"], 1)
        self.assertEqual(report["rollback_verified"], True)
        self.assertEqual(report["mock_grants"], 1)

    async def test_drill_refuses_production_before_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            production = staging_environment() | {
                "RAILWAY_ENVIRONMENT_ID": TARGET["production_environment_id"]
            }
            with self.assertRaises(PermissionError):
                await run_drill(production, temp_root=root)
            self.assertEqual(list(root.iterdir()), [])
