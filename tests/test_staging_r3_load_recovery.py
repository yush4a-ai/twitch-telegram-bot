import unittest

from scripts.staging_r3_load_recovery import run_experiment, validate_runtime


class StagingR3LoadRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_guard_requires_pinned_staging_queue(self):
        staging = {
            "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
            "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
            "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "NOTIFICATION_QUEUE_ENABLED": "1",
        }
        validate_runtime(staging)
        for key, value in (
            ("RAILWAY_ENVIRONMENT_NAME", "production"),
            ("RAILWAY_ENVIRONMENT_ID", "af6d873b-a2cf-45aa-be42-cd9efbd102a7"),
            ("NOTIFICATION_QUEUE_ENABLED", "0"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_runtime({**staging, key: value})

    async def test_restart_reclaims_lease_and_mixed_samples_drain_without_loss(self):
        result = await run_experiment(destinations=20, rounds=3)
        self.assertEqual(result["destinations"], 20)
        self.assertEqual(result["rounds"], 3)
        self.assertTrue(result["lease_reclaimed"])
        self.assertFalse(result["old_ack_accepted"])
        self.assertEqual(result["jobs_total"], 20)
        self.assertEqual(result["jobs_done"], 20)
        self.assertEqual(result["jobs_pending"], 0)
        self.assertEqual(result["jobs_failed"], 0)
        self.assertGreaterEqual(result["max_revision"], 3)
        self.assertEqual(result["integrity"], "ok")
        self.assertGreaterEqual(result["process_cpu_seconds"], 0)
        self.assertIn("peak_rss_bytes", result)
        self.assertEqual(result["queue_depth_max"], 20)
        self.assertGreaterEqual(result["job_completion_p95_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
