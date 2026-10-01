import unittest
from pathlib import Path

from scripts.r9_mixed_load import CapacityLimit, run_mixed_profile


class R9MixedLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_rounds_keep_one_job_per_destination_and_recover_lease(self):
        result = await run_mixed_profile(4, rounds=2, seed=7)
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["destinations"], 4)
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["distinct_logins"], 1)
        self.assertEqual(result["shared_observations"], 2)
        self.assertEqual(result["jobs_total"], 4)
        self.assertEqual(result["jobs_done"], 4)
        self.assertEqual(result["jobs_pending"], 0)
        self.assertEqual(result["jobs_leased"], 0)
        self.assertEqual(result["jobs_failed"], 0)
        self.assertEqual(result["max_revision"], 2)
        self.assertTrue(result["lease_reclaimed"])
        self.assertFalse(result["stale_ack_accepted"])
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["backup_integrity"], "ok")
        self.assertGreater(result["backup_restored_tables"], 0)
        self.assertGreaterEqual(result["queue_latency_ms"]["p95_ms"], 0)
        self.assertGreaterEqual(result["process_cpu_seconds"], 0)

    async def test_rejects_active_workspace_path_and_resource_exhaustion(self):
        with self.assertRaises(ValueError):
            await run_mixed_profile(4, temp_root=Path.cwd())
        with self.assertRaises(CapacityLimit):
            await run_mixed_profile(4, max_rss_bytes=1)
        with self.assertRaises(ValueError):
            await run_mixed_profile(0)
        with self.assertRaises(ValueError):
            await run_mixed_profile(4, rounds=1)


if __name__ == "__main__":
    unittest.main()
