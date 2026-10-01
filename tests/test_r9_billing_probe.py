import unittest

from scripts.r9_billing_probe import run_billing_probe


class R9BillingProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_replay_creates_one_mock_grant_and_refund_revokes_it(self):
        result = await run_billing_probe(replays=20)
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["provider"], "mock")
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["capture_replays"], 20)
        self.assertEqual(result["payments"], 1)
        self.assertEqual(result["grants"], 1)
        self.assertEqual(result["webhook_events"], 2)
        self.assertEqual(result["active_mock_grants_after_refund"], 0)
        self.assertTrue(result["bad_signature_rejected"])
        self.assertEqual(result["cancelled_orders"], 1)
        self.assertEqual(result["expired_orders"], 1)
        self.assertEqual(result["integrity"], "ok")

    async def test_replay_count_is_bounded(self):
        with self.assertRaises(ValueError):
            await run_billing_probe(replays=0)


if __name__ == "__main__":
    unittest.main()
