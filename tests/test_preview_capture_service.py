from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import unittest
from collections import deque
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from bot import preview_capture as capture
from bot.config import ConfigError, load_config


CORE_ENV = {
    "TELEGRAM_BOT_TOKEN": "telegram-token",
    "TWITCH_CLIENT_ID": "client-id",
    "TWITCH_CLIENT_SECRET": "client-secret",
}


class FakeStderr:
    async def read(self, _size: int = -1) -> bytes:
        await asyncio.sleep(0)
        return b""


class FakeProcess:
    def __init__(self, *, exit_on_terminate: bool = True) -> None:
        self.pid = 999
        self.returncode: int | None = None
        self.stderr = FakeStderr()
        self.exit_on_terminate = exit_on_terminate
        self.events: list[object] = []
        self._exited = asyncio.Event()

    async def wait(self) -> int:
        await self._exited.wait()
        return int(self.returncode)

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()

    def send_signal(self, value: int) -> None:
        self.events.append(("signal", value))

    def terminate(self) -> None:
        self.events.append("terminate")
        if self.exit_on_terminate:
            self.finish(0)

    def kill(self) -> None:
        self.events.append("kill")
        self.finish(-9)


class FakeProcessRunner:
    def __init__(self, *processes: FakeProcess) -> None:
        self.processes = deque(processes)
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    async def spawn(self, argv, *, cwd: Path):
        self.calls.append((tuple(argv), cwd))
        return self.processes.popleft()


class BlockingProcessRunner(FakeProcessRunner):
    def __init__(self, *processes: FakeProcess) -> None:
        super().__init__(*processes)
        self.all_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def spawn(self, argv, *, cwd: Path):
        self.calls.append((tuple(argv), cwd))
        if len(self.calls) == 2:
            self.all_entered.set()
        await self.release.wait()
        return self.processes.popleft()


def _available_capability() -> capture.CaptureCapability:
    return capture.CaptureCapability(available=True, ffmpeg_executable="ffmpeg")


class CaptureConfigTests(unittest.TestCase):
    def test_capture_defaults_and_two_session_budget_are_consistent(self) -> None:
        with patch.dict(os.environ, CORE_ENV, clear=True):
            config = load_config().preview_capture

        self.assertTrue(config.enabled)
        self.assertEqual(config.buffer_seconds, 120)
        self.assertEqual(config.buffer_max_bytes, 150 * 1024 * 1024)
        worst_case = 2 * (
            config.buffer_max_bytes + capture.PARTIAL_FILE_MAX_BYTES
        )
        self.assertEqual(worst_case, 364 * 1024 * 1024)
        self.assertLess(worst_case, capture.ROOT_HARD_MAX_BYTES)

    def test_invalid_capture_limits_fail_soft_without_logging_raw_values(self) -> None:
        secret_raw = "999999999-secret-value"
        env = dict(
            CORE_ENV,
            PREVIEW_BUFFER_SECONDS="241",
            PREVIEW_BUFFER_MAX_BYTES=secret_raw,
        )

        with patch.dict(os.environ, env, clear=True), self.assertLogs(
            "bot.config", logging.WARNING
        ) as captured_logs:
            config = load_config()

        self.assertFalse(config.preview_capture.enabled)
        self.assertEqual(config.preview_capture.disabled_reason, "config_error")
        rendered = "\n".join(captured_logs.output)
        self.assertIn("PREVIEW_BUFFER_SECONDS", rendered)
        self.assertIn("PREVIEW_BUFFER_MAX_BYTES", rendered)
        self.assertNotIn(secret_raw, rendered)

    def test_lower_bounds_are_fail_soft(self) -> None:
        env = dict(
            CORE_ENV,
            PREVIEW_BUFFER_SECONDS="29",
            PREVIEW_BUFFER_MAX_BYTES=str(32 * 1024 * 1024 - 1),
        )
        with patch.dict(os.environ, env, clear=True):
            config = load_config().preview_capture

        self.assertFalse(config.enabled)
        self.assertEqual(config.disabled_reason, "config_error")

    def test_invalid_capture_env_never_weakens_existing_core_error(self) -> None:
        env = {
            "TWITCH_CLIENT_ID": "client-id",
            "TWITCH_CLIENT_SECRET": "client-secret",
            "PREVIEW_BUFFER_SECONDS": "241",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigError, "TELEGRAM_BOT_TOKEN"):
                load_config()


class CapabilityProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_ffmpeg_is_unavailable_but_not_exception(self) -> None:
        with patch("shutil.which", return_value=None):
            result = await capture.probe_ffmpeg()

        self.assertFalse(result.available)
        self.assertEqual(result.reason, capture.CapabilityReason.MISSING_EXECUTABLE)

    async def test_probe_requires_version_and_hls_temp_file_support(self) -> None:
        command = AsyncMock(
            side_effect=[
                (0, b"ffmpeg version 7.1\n"),
                (0, b"Muxer hls\n-hls_flags <flags> temp_file\n"),
            ]
        )

        result = await capture.probe_ffmpeg(
            executable="C:/tools/ffmpeg.exe", command_runner=command
        )

        self.assertTrue(result.available)
        self.assertEqual(result.ffmpeg_executable, "C:/tools/ffmpeg.exe")
        self.assertEqual(result.ffmpeg_version, "ffmpeg version 7.1")
        self.assertEqual(command.await_count, 2)

    async def test_probe_output_and_timeout_fail_closed_for_capture_only(self) -> None:
        command = AsyncMock(side_effect=TimeoutError)

        result = await capture.probe_ffmpeg(
            executable="ffmpeg", command_runner=command
        )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, capture.CapabilityReason.PROBE_FAILED)

    async def test_probe_rejects_hls_without_temp_file_capability(self) -> None:
        command = AsyncMock(
            side_effect=[
                (0, b"ffmpeg version 7.1\n"),
                (0, b"Muxer hls without required atomic flag\n"),
            ]
        )

        result = await capture.probe_ffmpeg(
            executable="ffmpeg", command_runner=command
        )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, capture.CapabilityReason.UNSUPPORTED_HLS)

    async def test_unexpected_probe_failure_is_capture_unavailable(self) -> None:
        command = AsyncMock(side_effect=RuntimeError("unexpected"))

        result = await capture.probe_ffmpeg(
            executable="ffmpeg", command_runner=command
        )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, capture.CapabilityReason.PROBE_FAILED)


class RootOwnershipTests(unittest.TestCase):
    def test_absent_root_is_created_with_valid_marker(self) -> None:
        with TemporaryDirectory() as parent:
            root = Path(parent) / "signalbot-preview"

            manager = capture.CaptureRootManager(root)

            self.assertTrue(manager.available)
            self.assertTrue((root / capture.ROOT_MARKER_NAME).is_file())
            self.assertTrue(manager.has_valid_root_marker())

    def test_preexisting_root_without_marker_is_not_claimed_or_cleaned(self) -> None:
        with TemporaryDirectory() as parent:
            root = Path(parent) / "signalbot-preview"
            root.mkdir()
            foreign = root / "do-not-delete.txt"
            foreign.write_text("foreign", encoding="utf-8")

            manager = capture.CaptureRootManager(root)
            cleaned = manager.cleanup_orphans(now_unix=10_000)

            self.assertFalse(manager.available)
            self.assertFalse((root / capture.ROOT_MARKER_NAME).exists())
            self.assertEqual(cleaned, 0)
            self.assertTrue(foreign.exists())

    def test_wrong_root_marker_is_not_overwritten(self) -> None:
        with TemporaryDirectory() as parent:
            root = Path(parent) / "signalbot-preview"
            root.mkdir()
            marker = root / capture.ROOT_MARKER_NAME
            original = '{"application":"someone-else","schema":99}'
            marker.write_text(original, encoding="utf-8")

            manager = capture.CaptureRootManager(root)

            self.assertFalse(manager.available)
            self.assertEqual(marker.read_text(encoding="utf-8"), original)

    def test_session_marker_replace_failure_preserves_authoritative_marker(self) -> None:
        with TemporaryDirectory() as parent:
            manager = capture.CaptureRootManager(Path(parent) / "root")
            session = manager.create_session(now_unix=100)
            marker = session / capture.SESSION_MARKER_NAME
            original = marker.read_bytes()

            with self.assertRaises(OSError), patch(
                "bot.preview_capture.service.os.replace",
                side_effect=OSError("interrupted"),
            ):
                manager.write_session_marker(session, now_unix=200)

            self.assertEqual(marker.read_bytes(), original)
            self.assertTrue(any(session.glob(".session.json.tmp-*")))

    def test_corrupt_authoritative_marker_never_authorizes_recursive_delete(self) -> None:
        with TemporaryDirectory() as parent:
            manager = capture.CaptureRootManager(Path(parent) / "root")
            session = manager.create_session(now_unix=0)
            (session / capture.SESSION_MARKER_NAME).write_text(
                "{not-json", encoding="utf-8"
            )
            payload = session / "must-remain.txt"
            payload.write_text("keep", encoding="utf-8")

            cleaned = manager.cleanup_orphans(now_unix=10_000)

            self.assertEqual(cleaned, 0)
            self.assertTrue(payload.exists())

    def test_only_stale_verified_direct_child_is_renamed_and_removed(self) -> None:
        with TemporaryDirectory() as parent:
            parent_path = Path(parent)
            manager = capture.CaptureRootManager(parent_path / "root")
            stale = manager.create_session(now_unix=0)
            active = manager.create_session(now_unix=0)
            manager.register_active(active)
            # Keep a foreign path outside the owned root as a boundary sentinel.
            outside = parent_path / ("capture-" + "0" * 32)
            outside.mkdir()

            cleaned = manager.cleanup_orphans(now_unix=10_000)

            self.assertEqual(cleaned, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(active.exists())
            self.assertTrue(outside.exists())

    def test_cleanup_failure_stays_inside_owned_root_as_deferred_orphan(self) -> None:
        with TemporaryDirectory() as parent:
            manager = capture.CaptureRootManager(Path(parent) / "root")
            session = manager.create_session(now_unix=0)

            with patch(
                "bot.preview_capture.service.shutil.rmtree",
                side_effect=PermissionError("sharing violation"),
            ):
                deferred = manager.remove_session(session)

            self.assertTrue(deferred)
            self.assertFalse(session.exists())
            leftovers = tuple(manager.root.glob(".deleting-*"))
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].parent.resolve(), manager.root.resolve())

    def test_deferred_orphan_counts_toward_root_budget_and_is_retried(self) -> None:
        with TemporaryDirectory() as parent:
            manager = capture.CaptureRootManager(Path(parent) / "root")
            session = manager.create_session(now_unix=0)
            payload = session / "payload.bin"
            payload.write_bytes(b"x" * 4096)
            with patch(
                "bot.preview_capture.service.shutil.rmtree",
                side_effect=PermissionError("sharing violation"),
            ):
                self.assertTrue(manager.remove_session(session))

            self.assertGreaterEqual(manager.total_owned_bytes(), 4096)
            self.assertEqual(manager.cleanup_orphans(now_unix=10_000), 1)
            self.assertFalse(tuple(manager.root.glob(".deleting-*")))

    def test_atomic_rename_during_root_size_scan_is_not_disk_pressure(self) -> None:
        with TemporaryDirectory() as parent:
            manager = capture.CaptureRootManager(Path(parent) / "root")
            session = manager.create_session(now_unix=0)
            transient = session / "segment-000000000.ts.tmp"
            transient.write_bytes(b"x" * 1024)
            original_lstat = Path.lstat

            def disappearing_lstat(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == transient and transient.exists():
                    transient.unlink()
                return info

            with patch.object(type(transient), "lstat", disappearing_lstat):
                total = manager.total_owned_bytes()

            self.assertLess(total, capture.ROOT_HARD_MAX_BYTES)


class ProgressWatchdogTests(unittest.TestCase):
    def test_first_segment_grace_then_stall_from_registered_progress_only(self) -> None:
        now = [0.0]
        watchdog = capture.ProgressWatchdog(
            monotonic=lambda: now[0], first_grace=45, stall_after=30
        )

        now[0] = 44.9
        self.assertFalse(watchdog.expired)
        now[0] = 45.1
        self.assertTrue(watchdog.expired)

        now[0] = 100
        watchdog = capture.ProgressWatchdog(
            monotonic=lambda: now[0], first_grace=45, stall_after=30
        )
        watchdog.register_complete_segment()
        now[0] = 129.9
        self.assertFalse(watchdog.expired)
        now[0] = 130.1
        self.assertTrue(watchdog.expired)

    def test_partial_growth_does_not_count_as_progress(self) -> None:
        now = [0.0]
        watchdog = capture.ProgressWatchdog(
            monotonic=lambda: now[0], first_grace=45, stall_after=30
        )
        watchdog.observe_partial_size(1024)
        now[0] = 46

        self.assertTrue(watchdog.expired)


class CaptureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_tmp_rename_during_scan_is_not_disk_pressure(self) -> None:
        with TemporaryDirectory() as parent:
            process = FakeProcess()
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(process),
                sample_interval=10,
            )
            handle = (await service.start(capture.UrlCaptureInput("https://valid"))).handle
            transient = handle.session_dir / "segment-000000000.ts.tmp"
            transient.write_bytes(b"x" * 1024)
            original_lstat = Path.lstat

            def disappearing_lstat(path, *args, **kwargs):
                info = original_lstat(path, *args, **kwargs)
                if path == transient and transient.exists():
                    transient.unlink()
                return info

            with patch.object(type(transient), "lstat", disappearing_lstat):
                self.assertFalse(handle._disk_pressure())

            await handle.close()
            await service.close()

    async def test_out_of_contract_settings_disable_capture_fail_soft(self) -> None:
        with TemporaryDirectory() as parent:
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                settings=capture.CaptureSettings(
                    buffer_seconds=241,
                    buffer_max_bytes=151 * 1024 * 1024,
                ),
            )

            result = await service.start(capture.UrlCaptureInput("https://valid"))

            self.assertFalse(service.capability.available)
            self.assertEqual(
                service.capability.reason, capture.CapabilityReason.CONFIG_ERROR
            )
            self.assertEqual(
                result.outcome.reason,
                capture.CaptureEndReason.CAPABILITY_UNAVAILABLE,
            )
            await service.close()

    async def test_unavailable_capability_and_invalid_input_are_typed(self) -> None:
        with TemporaryDirectory() as parent:
            unavailable = capture.CaptureService(
                root=Path(parent) / "unavailable",
                capability=capture.CaptureCapability(
                    available=False,
                    reason=capture.CapabilityReason.MISSING_EXECUTABLE,
                ),
            )
            missing = await unavailable.start(capture.UrlCaptureInput("https://x"))
            available = capture.CaptureService(
                root=Path(parent) / "available",
                capability=_available_capability(),
            )
            invalid = await available.start(capture.UrlCaptureInput(""))

            self.assertEqual(
                missing.outcome.reason, capture.CaptureEndReason.CAPABILITY_UNAVAILABLE
            )
            self.assertEqual(
                invalid.outcome.reason, capture.CaptureEndReason.INVALID_INPUT
            )
            await unavailable.close()
            await available.close()

    async def test_max_two_sessions_and_failure_a_does_not_affect_b(self) -> None:
        with TemporaryDirectory() as parent:
            first, second = FakeProcess(), FakeProcess()
            runner = FakeProcessRunner(first, second)
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=runner,
                sample_interval=10,
            )

            a = (await service.start(capture.UrlCaptureInput("https://a"))).handle
            b = (await service.start(capture.UrlCaptureInput("https://b"))).handle
            third = await service.start(capture.UrlCaptureInput("https://c"))
            first.finish(1)
            outcome_a = await asyncio.wait_for(a.wait(), 0.2)

            self.assertEqual(outcome_a.reason, capture.CaptureEndReason.PROCESS_EXIT)
            self.assertEqual(
                third.outcome.reason, capture.CaptureEndReason.CAPABILITY_UNAVAILABLE
            )
            self.assertEqual(b.state, capture.CaptureState.RUNNING)
            self.assertIsNone(b.outcome)
            self.assertIsNone(second.returncode)
            await a.close()
            await b.close()
            await service.close()

    async def test_ended_unclosed_handle_no_longer_consumes_capture_slot(self) -> None:
        with TemporaryDirectory() as parent:
            first, second, third = FakeProcess(), FakeProcess(), FakeProcess()
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(first, second, third),
                sample_interval=10,
            )
            a = (await service.start(capture.UrlCaptureInput("https://a"))).handle
            b = (await service.start(capture.UrlCaptureInput("https://b"))).handle
            first.finish(0)
            await asyncio.wait_for(a.wait(), 0.2)

            replacement = await service.start(
                capture.UrlCaptureInput("https://replacement")
            )

            self.assertTrue(replacement.started)
            self.assertEqual(replacement.handle.state, capture.CaptureState.RUNNING)
            await a.close()
            await b.close()
            await replacement.handle.close()
            await service.close()

    async def test_close_a_does_not_signal_or_delete_b(self) -> None:
        with TemporaryDirectory() as parent:
            first, second = FakeProcess(), FakeProcess()
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(first, second),
                sample_interval=10,
            )
            a = (await service.start(capture.UrlCaptureInput("https://a"))).handle
            b = (await service.start(capture.UrlCaptureInput("https://b"))).handle
            b_dir = b.session_dir

            await a.close()

            self.assertTrue(b_dir.exists())
            self.assertEqual(second.events, [])
            self.assertEqual(b.state, capture.CaptureState.RUNNING)
            await b.close()
            await service.close()

    async def test_close_waits_bounded_then_invalidates_snapshot_and_cleans(self) -> None:
        with TemporaryDirectory() as parent:
            process = FakeProcess()
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(process),
                sample_interval=10,
                snapshot_release_grace=0.01,
            )
            handle = (
                await service.start(capture.UrlCaptureInput("https://input"))
            ).handle
            segment = handle.session_dir / "segment-000000000.ts"
            segment.write_bytes((bytes([0x47]) + b"\0" * 187) * 2)
            (handle.session_dir / "index.m3u8").write_text(
                "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n"
                "#EXTINF:2,\nsegment-000000000.ts\n",
                encoding="utf-8",
            )
            handle.scan_manifest_now()
            snapshot = handle.acquire_snapshot().snapshot
            session_dir = handle.session_dir

            outcome = await handle.close()

            self.assertEqual(outcome.reason, capture.CaptureEndReason.SHUTDOWN)
            self.assertFalse(snapshot.valid)
            self.assertEqual(handle.active_pin_count, 0)
            self.assertEqual(handle.background_task_count, 0)
            self.assertFalse(session_dir.exists())
            await service.close()

    async def test_caller_cancellation_still_completes_handle_cleanup(self) -> None:
        with TemporaryDirectory() as parent:
            process = FakeProcess(exit_on_terminate=False)
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(process),
                sample_interval=10,
                terminate_timeout=0.01,
                kill_timeout=0.01,
            )
            handle = (
                await service.start(capture.UrlCaptureInput("https://input"))
            ).handle
            caller = asyncio.create_task(handle.close())
            await asyncio.sleep(0)
            caller.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await caller
            await handle.wait_closed()

            self.assertEqual(handle.background_task_count, 0)
            self.assertEqual(handle.active_pin_count, 0)
            self.assertIn("kill", process.events)
            await service.close()

    async def test_spawn_errors_are_typed_and_do_not_leak_input(self) -> None:
        secret = "https://video.example/live.m3u8?token=never-return"
        with TemporaryDirectory() as parent:
            denied_runner = AsyncMock()
            denied_runner.spawn.side_effect = PermissionError(secret)
            denied_service = capture.CaptureService(
                root=Path(parent) / "denied",
                capability=_available_capability(),
                process_runner=denied_runner,
            )
            failed_runner = AsyncMock()
            failed_runner.spawn.side_effect = OSError(secret)
            failed_service = capture.CaptureService(
                root=Path(parent) / "failed",
                capability=_available_capability(),
                process_runner=failed_runner,
            )

            denied = await denied_service.start(capture.UrlCaptureInput(secret))
            failed = await failed_service.start(capture.UrlCaptureInput(secret))

            self.assertEqual(
                denied.outcome.reason, capture.CaptureEndReason.PERMISSION_DENIED
            )
            self.assertEqual(
                failed.outcome.reason, capture.CaptureEndReason.START_FAILED
            )
            self.assertNotIn("never-return", repr(denied))
            self.assertNotIn("never-return", repr(failed))
            self.assertEqual(denied_service.active_handle_count, 0)
            self.assertEqual(failed_service.active_handle_count, 0)

    async def test_actual_spawn_argv_is_fixed_copy_mode(self) -> None:
        with TemporaryDirectory() as parent:
            process = FakeProcess()
            runner = FakeProcessRunner(process)
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=runner,
                sample_interval=10,
            )
            url = "https://video.example/live.m3u8?token=one-element"

            handle = (await service.start(capture.UrlCaptureInput(url))).handle
            argv, cwd = runner.calls[0]

            self.assertEqual(argv[argv.index("-i") + 1], url)
            self.assertEqual(argv[argv.index("-c") + 1], "copy")
            self.assertNotIn("-r", argv)
            self.assertEqual(cwd, handle.session_dir)
            await handle.close()
            await service.close()

    async def test_session_partial_pressure_stops_only_that_capture(self) -> None:
        high_free = shutil.disk_usage(Path.cwd())
        safe_usage = type(high_free)(
            total=high_free.total,
            used=0,
            free=capture.FREE_DISK_RESERVE_BYTES + 1,
        )
        with TemporaryDirectory() as parent:
            first, second = FakeProcess(), FakeProcess()
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=FakeProcessRunner(first, second),
                disk_usage=lambda _path: safe_usage,
                sample_interval=0.005,
            )
            a = (await service.start(capture.UrlCaptureInput("https://a"))).handle
            b = (await service.start(capture.UrlCaptureInput("https://b"))).handle
            partial = a.session_dir / "segment-000000000.ts.tmp"
            with partial.open("wb") as stream:
                stream.truncate(capture.PARTIAL_FILE_MAX_BYTES + 1)

            outcome_a = await asyncio.wait_for(a.wait(), 0.5)

            self.assertEqual(
                outcome_a.reason, capture.CaptureEndReason.DISK_PRESSURE
            )
            self.assertEqual(b.state, capture.CaptureState.RUNNING)
            self.assertIsNone(second.returncode)
            await b.close()
            await service.close()

    async def test_concurrent_start_never_exceeds_two_reserved_slots(self) -> None:
        with TemporaryDirectory() as parent:
            processes = (FakeProcess(), FakeProcess(), FakeProcess())
            runner = BlockingProcessRunner(*processes)
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=runner,
                sample_interval=10,
            )
            starts = [
                asyncio.create_task(
                    service.start(capture.UrlCaptureInput(f"https://{index}"))
                )
                for index in range(3)
            ]
            await asyncio.wait_for(runner.all_entered.wait(), 0.2)
            runner.release.set()

            results = await asyncio.gather(*starts)

            self.assertEqual(sum(result.started for result in results), 2)
            self.assertEqual(service.active_handle_count, 2)
            await service.close()

    async def test_unexpected_spawn_exception_maps_to_internal_error(self) -> None:
        with TemporaryDirectory() as parent:
            runner = AsyncMock()
            runner.spawn.side_effect = RuntimeError("opaque internal failure")
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=runner,
            )

            result = await service.start(capture.UrlCaptureInput("https://valid"))

            self.assertEqual(
                result.outcome.reason, capture.CaptureEndReason.INTERNAL_ERROR
            )
            self.assertEqual(service.active_handle_count, 0)

    async def test_cancellation_during_start_finalization_owns_and_closes_handle(self) -> None:
        with TemporaryDirectory() as parent:
            process = FakeProcess()
            runner = BlockingProcessRunner(process)
            service = capture.CaptureService(
                root=Path(parent) / "root",
                capability=_available_capability(),
                process_runner=runner,
                sample_interval=10,
            )
            start_task = asyncio.create_task(
                service.start(capture.UrlCaptureInput("https://valid"))
            )
            while not runner.calls:
                await asyncio.sleep(0)
            await service._start_lock.acquire()
            runner.release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            start_task.cancel()
            service._start_lock.release()

            with self.assertRaises(asyncio.CancelledError):
                await start_task

            leaked_starting = service._starting
            leaked_idle = service._starts_idle.is_set()
            leaked_handles = len(service._handles)
            leaked_process = process.returncode is None
            leaked_sessions = tuple(service.root_manager.root.glob("capture-*"))
            # Repair only the pre-fix RED state so unittest loop shutdown cannot
            # hide the actual leaked resources behind automatic task cancellation.
            if leaked_process:
                process.finish(0)
            if leaked_starting:
                service._starting = 0
                service._starts_idle.set()
            for session in leaked_sessions:
                service.root_manager.remove_session(session)

            self.assertEqual(leaked_starting, 0)
            self.assertTrue(leaked_idle)
            self.assertEqual(leaked_handles, 0)
            self.assertFalse(leaked_process)
            self.assertEqual(leaked_sessions, ())
            await asyncio.wait_for(service.close(), 0.5)


class OutcomeSafetyTests(unittest.TestCase):
    def test_outcome_contains_only_typed_sanitized_diagnostics(self) -> None:
        outcome = capture.CaptureOutcome(
            reason=capture.CaptureEndReason.PROCESS_EXIT,
            exit_code=1,
            diagnostic_code="input_error",
            cleanup_deferred=False,
        )

        self.assertEqual(
            set(outcome.__dataclass_fields__),
            {"reason", "exit_code", "diagnostic_code", "cleanup_deferred"},
        )
        self.assertNotIn("url", repr(outcome).lower())
        self.assertNotIn("stderr", repr(outcome).lower())


if __name__ == "__main__":
    unittest.main()
