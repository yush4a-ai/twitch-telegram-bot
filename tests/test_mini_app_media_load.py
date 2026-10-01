"""The local media load harness stays bounded and isolated from live data."""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pathlib import Path

from scripts.mini_app_media_load import LoadLimits, ResourceStop, _FakeProvider, run_profile


class MiniAppMediaLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_capture_and_file_id_reuse_with_temporary_db(self):
        with tempfile.TemporaryDirectory() as directory:
            active_path = os.path.join(directory, "active.db")
            with open(active_path, "wb") as handle:
                handle.write(b"production marker")
            with patch.dict(os.environ, {"DB_PATH": active_path}):
                result = await run_profile("1x1000", encoder="synthetic")
            with open(active_path, "rb") as handle:
                self.assertEqual(handle.read(), b"production marker")
        self.assertEqual(result["recipients"], 1000)
        self.assertEqual(result["capture_count"], 1)
        self.assertEqual(result["local_uploads"], 1)
        self.assertEqual(result["file_id_reuses"], 999)
        self.assertLessEqual(result["peak_capture"], 2)
        self.assertLessEqual(result["peak_encode"], 2)
        self.assertTrue(result["temp_db_removed"])
        self.assertEqual(result["external_sends"], 0)

    async def test_many_streams_defer_and_expired_session_closes(self):
        result = await run_profile("100x10", encoder="synthetic")
        self.assertEqual(result["streams"], 100)
        self.assertEqual(result["deferred_peak"], 98)
        self.assertLessEqual(result["peak_capture"], 2)
        self.assertEqual(result["expired_sessions_closed"], 1)
        self.assertTrue(result["expired_selection_disabled"])
        self.assertGreater(result["normal_signal_p95_ms"], 0)
        self.assertGreater(result["photo_signal_p95_ms"], 0)

    async def test_five_thousand_distinct_selections_do_not_start_five_thousand_captures(self):
        result = await run_profile("1000x5", encoder="synthetic")
        self.assertEqual((result["viewers"], result["selected_per_viewer"]), (1000, 5))
        self.assertEqual(result["streams"], 5000)
        self.assertEqual(result["deferred_peak"], 4998)
        self.assertLessEqual(result["capture_count"], 2)

    async def test_resource_stop_cleans_temporary_db(self):
        with self.assertRaises(ResourceStop) as stopped:
            await run_profile("1x1000", encoder="synthetic", limits=LoadLimits(max_tasks=1))
        self.assertFalse(os.path.exists(stopped.exception.db_path))

    async def test_timed_out_child_process_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = _FakeProvider(Path(directory), "h264")
            task = asyncio.create_task(provider.run_tool(
                sys._base_executable, "-c", "import time; time.sleep(30)", timeout=0.1,
            ))
            for _ in range(100):
                if provider.children:
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(len(provider.children), 1)
            process = next(iter(provider.children.values()))
            rss, _cpu = provider.child_usage()
            self.assertGreater(rss, 0)
            with self.assertRaises(TimeoutError):
                await task
            self.assertIsNotNone(process.returncode)
            self.assertEqual(provider.children, {})

    async def test_finished_child_cpu_is_counted_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = _FakeProvider(Path(directory), "h264")
            code, _stdout, _stderr = await provider.run_tool(
                sys._base_executable, "-c", "x=0\nfor i in range(8000000): x+=i", timeout=10,
            )
            self.assertEqual(code, 0)
            self.assertGreater(provider.child_usage()[1], 0.05)
            self.assertEqual(provider.children, {})
            self.assertEqual(provider.child_handles, {})

    async def test_no_external_database_or_telegram_target_argument(self):
        with self.assertRaises(ValueError):
            LoadLimits(max_tasks=0)
        with self.assertRaises(ValueError):
            await run_profile("unapproved", encoder="synthetic")
        rejected = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "scripts.mini_app_media_load", "--profile", "1x1000",
             "--db-path", "production.db", "--telegram-target", "public"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("unrecognized arguments", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
