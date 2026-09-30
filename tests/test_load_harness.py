import tempfile
import unittest
from pathlib import Path

from scripts.load_harness import run_preview_profile, run_profile, run_queue_profile


class LoadHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_profiles_use_real_manager_bound_with_fake_media(self):
        serial = await run_preview_profile(
            1, stream_count=4, capture_delay=0.2, send_delay=0.005
        )
        parallel = await run_preview_profile(
            4, stream_count=4, capture_delay=0.2, send_delay=0.005
        )
        self.assertEqual(serial["completed_previews"], 4)
        self.assertEqual(parallel["completed_previews"], 4)
        self.assertEqual(serial["max_active_captures"], 1)
        self.assertGreaterEqual(parallel["max_active_captures"], 2)
        self.assertLessEqual(parallel["max_active_captures"], 4)
        self.assertGreaterEqual(serial["max_pending_capture_jobs"], 2)
        self.assertTrue(serial["synthetic"])
        self.assertEqual(serial["latency_ms"]["count"], 4)

    async def test_preview_profile_rejects_unbounded_concurrency(self):
        with self.assertRaises(ValueError):
            await run_preview_profile(40)

    async def test_queue_profile_drains_every_synthetic_job_without_network(self):
        profile = await run_queue_profile(40)
        self.assertEqual(profile["enqueued_jobs"], 40)
        self.assertEqual(profile["completed_jobs"], 40)
        self.assertEqual(profile["remaining_jobs"], 0)
        self.assertEqual(profile["latency_ms"]["count"], 40)
        self.assertTrue(profile["synthetic"])

    async def test_queue_profile_rejects_unbounded_jobs(self):
        with self.assertRaises(ValueError):
            await run_queue_profile(40001)

    async def test_small_profile_is_synthetic_reproducible_and_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            first = await run_profile(100, seed=17, db_path=str(Path(directory) / "first.db"), rounds=2)
            second = await run_profile(100, seed=17, db_path=str(Path(directory) / "second.db"), rounds=2)
        self.assertTrue(first["synthetic"])
        self.assertEqual(first["workload_sha256"], second["workload_sha256"])
        self.assertEqual(first["operations"]["track_insert"]["count"], 100)
        self.assertEqual(first["operations"]["sample_insert"]["count"], 200)
        self.assertEqual(first["sample_rows"], 200)
        self.assertGreater(first["db_bytes"], 0)
        for operation in first["operations"].values():
            self.assertGreater(operation["count"], 0)
            self.assertLessEqual(operation["p50_ms"], operation["p95_ms"])
            self.assertLessEqual(operation["p95_ms"], operation["p99_ms"])
        self.assertNotIn("chat_id", first)
        self.assertNotIn("bot_token", first)

    async def test_never_overwrites_existing_or_non_temp_database(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "existing.db"
            existing.write_bytes(b"sentinel")
            with self.assertRaises(ValueError):
                await run_profile(10, seed=1, db_path=str(existing), rounds=1)
            self.assertEqual(existing.read_bytes(), b"sentinel")
        outside = Path.cwd() / "forbidden-load-harness.db"
        with self.assertRaises(ValueError):
            await run_profile(10, seed=1, db_path=str(outside), rounds=1)
        self.assertFalse(outside.exists())

    async def test_shared_mode_keeps_exact_memberships_with_one_payload_per_login(self):
        with tempfile.TemporaryDirectory() as directory:
            result = await run_profile(
                100, seed=17, db_path=str(Path(directory) / "shared.db"),
                rounds=2, sample_mode="shared",
            )
        self.assertEqual(result["sample_mode"], "shared")
        self.assertEqual(result["sample_rows"], 2)
        self.assertEqual(result["membership_rows"], 200)
        self.assertEqual(result["sample_duplication_factor"], 1.0)


if __name__ == "__main__":
    unittest.main()
