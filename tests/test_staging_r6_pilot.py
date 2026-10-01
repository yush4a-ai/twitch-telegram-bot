"""R6 pilot simulation is synthetic, owner-isolated, and disposable."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.staging_r6_pilot import run_simulation


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


class StagingPilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_eight_streamers_are_isolated_and_all_lifecycles_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "existing.db"
            sentinel.write_bytes(b"untouched")
            report = await run_simulation(staging_environment(), temp_root=root)
            self.assertEqual(sentinel.read_bytes(), b"untouched")
            self.assertEqual(sorted(root.iterdir()), [sentinel])
        for key, expected in {
            "streamers": 8, "communities": 8, "templates": 8,
            "published_events": 8, "refunds": 2, "expired_grants": 6,
            "cancelled_orders": 1, "expected_denials": 16,
            "unexpected_errors": 0, "telegram_calls": 0, "twitch_calls": 0,
            "payment_network_calls": 0, "external_cost_units": 0,
            "integrity": "ok",
        }.items():
            with self.subTest(key=key):
                self.assertEqual(report[key], expected)
        self.assertGreater(report["db_bytes"], 0)
        self.assertGreaterEqual(report["elapsed_ms"], 0)
        self.assertNotIn("telegram_user_id", report)
        self.assertNotIn("broadcaster_id", report)

    async def test_wrong_environment_or_volume_cannot_create_simulation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for changed in (
                {"RAILWAY_ENVIRONMENT_ID": TARGET["production_environment_id"]},
                {"RAILWAY_SERVICE_ID": "wrong"},
                {"OWNER_CHAT_ID": "0"},
            ):
                with self.subTest(changed=changed), self.assertRaises(PermissionError):
                    await run_simulation(staging_environment() | changed, temp_root=root)
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaises(PermissionError):
                await run_simulation(staging_environment(), temp_root=Path("/data"))
