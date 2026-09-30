import tempfile
import unittest
from pathlib import Path

from scripts.load_harness import run_profile


class LoadHarnessTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
