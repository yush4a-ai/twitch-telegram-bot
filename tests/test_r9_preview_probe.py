import unittest

from scripts.r9_preview_probe import run_preview_probe


class R9PreviewProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_854x480_h264_has_no_audio_and_bounded_coordinator(self):
        result = await run_preview_probe(duration_seconds=1, coordinator_limits=(1, 2))
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["video"]["width"], 854)
        self.assertEqual(result["video"]["height"], 480)
        self.assertEqual(result["video"]["codec"], "h264")
        self.assertEqual(result["video"]["audio_streams"], 0)
        self.assertLessEqual(result["video"]["bytes"], 10 * 1024 * 1024)
        self.assertIsInstance(result["video"]["encode_cpu_seconds"], float)
        self.assertEqual(len(result["coordinator"]), 2)
        for item in result["coordinator"]:
            self.assertEqual(item["completed_previews"], 4)
            self.assertLessEqual(item["max_active_captures"], item["capture_concurrency"])


if __name__ == "__main__":
    unittest.main()
