from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path


SECRET_URL = "https://video.example/live.m3u8?token=bridge-secret"


def _source_module():
    try:
        return importlib.import_module("bot.preview_source")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B bot.preview_source package must exist") from error


def _capture_module():
    return importlib.import_module("bot.preview_capture")


def _resolved(url: str = SECRET_URL):
    source = _source_module()
    return source.PlaybackResolveResult(
        status=source.PlaybackResolveStatus.RESOLVED,
        playback=source.ResolvedPlayback(url),
        retry_disposition=source.RetryDisposition.NONE,
    )


class FakeResolver:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[str] = []

    async def resolve(self, login: str):
        self.calls.append(login)
        if not self.results:
            raise AssertionError("unexpected resolve")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeCaptureService:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[object] = []

    async def start(self, capture_input):
        self.calls.append(capture_input)
        if not self.results:
            raise AssertionError("unexpected capture start")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class BlockingCaptureRunner:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def spawn(self, _argv, *, cwd):
        self.entered.set()
        await asyncio.Event().wait()


class CaptureBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_resolver_return_fails_open_as_internal_error(self) -> None:
        source = _source_module()
        service = FakeCaptureService()

        result = await source.TwitchCaptureSource(
            FakeResolver(None), service
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.RESOLVE_FAILED
        )
        self.assertEqual(result.resolve_status, source.PlaybackResolveStatus.INTERNAL_ERROR)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertEqual(service.calls, [])

    async def test_malformed_capture_return_fails_open_as_internal_error(self) -> None:
        source = _source_module()
        capture = _capture_module()

        result = await source.TwitchCaptureSource(
            FakeResolver(_resolved()), FakeCaptureService(None)
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.CAPTURE_NOT_STARTED
        )
        self.assertEqual(result.capture_outcome.reason, capture.CaptureEndReason.INTERNAL_ERROR)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)

    async def test_open_resolves_converts_and_starts_exactly_once(self) -> None:
        source = _source_module()
        capture = _capture_module()
        handle = object()
        resolver = FakeResolver(_resolved())
        service = FakeCaptureService(capture.CaptureStartResult(handle=handle))
        bridge = source.TwitchCaptureSource(resolver, service)

        result = await bridge.open("Valid_Login", "physical-1")

        self.assertEqual(result.status, source.TwitchCaptureStartStatus.STARTED)
        self.assertIs(result.handle, handle)
        self.assertEqual(resolver.calls, ["valid_login"])
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0].url, SECRET_URL)

    async def test_invalid_login_stops_before_resolver_or_capture(self) -> None:
        source = _source_module()
        resolver = FakeResolver()
        service = FakeCaptureService()
        bridge = source.TwitchCaptureSource(resolver, service)

        result = await bridge.open("bad/login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.INVALID_IDENTITY
        )
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(service.calls, [])

    async def test_invalid_physical_stream_id_stops_before_side_effects(self) -> None:
        source = _source_module()
        invalid_ids = (
            "",
            "line\nbreak",
            "unicode\u0080control",
            "format\u202econtrol",
            "contains space",
            "x" * 129,
            42,
            None,
        )

        for invalid in invalid_ids:
            with self.subTest(invalid=invalid):
                resolver = FakeResolver()
                service = FakeCaptureService()
                result = await source.TwitchCaptureSource(resolver, service).open(
                    "valid_login", invalid
                )
                self.assertEqual(
                    result.status, source.TwitchCaptureStartStatus.INVALID_IDENTITY
                )
                self.assertEqual(resolver.calls, [])
                self.assertEqual(service.calls, [])

    async def test_physical_id_is_ownership_only_and_never_enters_url(self) -> None:
        source = _source_module()
        capture = _capture_module()
        resolver = FakeResolver(_resolved())
        service = FakeCaptureService(capture.CaptureStartResult(handle=object()))
        physical_id = "physical-secret-42"

        result = await source.TwitchCaptureSource(resolver, service).open(
            "valid_login", physical_id
        )

        self.assertEqual(result.key.physical_stream_id, physical_id)
        self.assertNotIn(physical_id, service.calls[0].url)
        self.assertNotIn(physical_id, repr(result))

    async def test_resolver_failure_is_typed_and_does_not_start_capture(self) -> None:
        source = _source_module()
        resolve_failure = source.PlaybackResolveResult(
            status=source.PlaybackResolveStatus.PROCESS_FAILED,
            retry_disposition=source.RetryDisposition.RETRYABLE,
            exit_code=1,
            diagnostic_code="process_failed",
        )
        service = FakeCaptureService()

        result = await source.TwitchCaptureSource(
            FakeResolver(resolve_failure), service
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.RESOLVE_FAILED
        )
        self.assertEqual(result.resolve_status, source.PlaybackResolveStatus.PROCESS_FAILED)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.RETRYABLE)
        self.assertEqual(service.calls, [])

    async def test_capture_failure_is_typed_without_secret_in_result(self) -> None:
        source = _source_module()
        capture = _capture_module()
        outcome = capture.CaptureOutcome(
            capture.CaptureEndReason.START_FAILED,
            diagnostic_code=SECRET_URL,
        )
        service = FakeCaptureService(capture.CaptureStartResult(outcome=outcome))

        result = await source.TwitchCaptureSource(
            FakeResolver(_resolved()), service
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.CAPTURE_NOT_STARTED
        )
        self.assertEqual(result.capture_outcome.reason, capture.CaptureEndReason.START_FAILED)
        self.assertEqual(
            result.retry_disposition, source.RetryDisposition.RETRYABLE
        )
        self.assertNotIn(SECRET_URL, repr(result))

    async def test_each_open_freshly_resolves_and_never_reuses_old_url(self) -> None:
        source = _source_module()
        capture = _capture_module()
        first = "https://one.example/live.m3u8?token=first"
        second = "https://two.example/live.m3u8?token=second"
        resolver = FakeResolver(_resolved(first), _resolved(second))
        service = FakeCaptureService(
            capture.CaptureStartResult(handle=object()),
            capture.CaptureStartResult(handle=object()),
        )
        bridge = source.TwitchCaptureSource(resolver, service)

        await bridge.open("valid_login", "physical-1")
        await bridge.open("valid_login", "physical-1")

        self.assertEqual(resolver.calls, ["valid_login", "valid_login"])
        self.assertEqual([item.url for item in service.calls], [first, second])

    async def test_two_physical_streams_keep_independent_keys_and_resolves(self) -> None:
        source = _source_module()
        capture = _capture_module()
        resolver = FakeResolver(_resolved(), _resolved())
        service = FakeCaptureService(
            capture.CaptureStartResult(handle=object()),
            capture.CaptureStartResult(handle=object()),
        )
        bridge = source.TwitchCaptureSource(resolver, service)

        a, b = await asyncio.gather(
            bridge.open("channel_a", "stream-A"),
            bridge.open("channel_b", "stream-B"),
        )

        self.assertNotEqual(a.key, b.key)
        self.assertEqual({a.key.physical_stream_id, b.key.physical_stream_id}, {"stream-A", "stream-B"})
        self.assertEqual(len(resolver.calls), 2)
        self.assertEqual(len(service.calls), 2)

    async def test_unexpected_resolver_exception_fails_open_as_internal_error(self) -> None:
        source = _source_module()
        service = FakeCaptureService()

        result = await source.TwitchCaptureSource(
            FakeResolver(RuntimeError(SECRET_URL)), service
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.RESOLVE_FAILED
        )
        self.assertEqual(result.resolve_status, source.PlaybackResolveStatus.INTERNAL_ERROR)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertEqual(service.calls, [])
        self.assertNotIn(SECRET_URL, repr(result))

    async def test_unexpected_capture_exception_fails_open_as_internal_error(self) -> None:
        source = _source_module()
        capture = _capture_module()

        result = await source.TwitchCaptureSource(
            FakeResolver(_resolved()), FakeCaptureService(RuntimeError(SECRET_URL))
        ).open("valid_login", "physical-1")

        self.assertEqual(
            result.status, source.TwitchCaptureStartStatus.CAPTURE_NOT_STARTED
        )
        self.assertEqual(result.capture_outcome.reason, capture.CaptureEndReason.INTERNAL_ERROR)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertNotIn(SECRET_URL, repr(result))

    async def test_cancellation_in_capture_start_preserves_p4a_cleanup(self) -> None:
        source = _source_module()
        capture = _capture_module()
        runner = BlockingCaptureRunner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "capture-root"
            service = capture.CaptureService(
                root=root,
                capability=capture.CaptureCapability(
                    available=True,
                    ffmpeg_executable="ffmpeg",
                    ffmpeg_version="test",
                ),
                process_runner=runner,
            )
            bridge = source.TwitchCaptureSource(FakeResolver(_resolved()), service)
            task = asyncio.create_task(
                bridge.open("valid_login", "physical-1")
            )
            await asyncio.wait_for(runner.entered.wait(), 0.2)
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertEqual(service._starting, 0)
            self.assertTrue(service._starts_idle.is_set())
            self.assertEqual(service.active_handle_count, 0)
            self.assertEqual(tuple(root.glob("capture-*")), ())
            await service.close()


class CaptureRetryPolicyTests(unittest.TestCase):
    def test_transient_capture_endings_require_fresh_resolve_if_active(self) -> None:
        source = _source_module()
        capture = _capture_module()
        reasons = (
            capture.CaptureEndReason.STALLED,
            capture.CaptureEndReason.PROCESS_EXIT,
            capture.CaptureEndReason.CLEAN_EOF,
            capture.CaptureEndReason.START_FAILED,
        )

        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertEqual(
                    source.capture_retry_action(capture.CaptureOutcome(reason)),
                    source.CaptureRetryAction.FRESH_RESOLVE_IF_ACTIVE,
                )

    def test_capacity_condition_waits_for_resource_if_stream_is_active(self) -> None:
        source = _source_module()
        capture = _capture_module()
        outcome = capture.CaptureOutcome(
            capture.CaptureEndReason.CAPABILITY_UNAVAILABLE,
            diagnostic_code="capacity",
        )

        self.assertEqual(
            source.capture_retry_action(outcome),
            source.CaptureRetryAction.WAIT_FOR_RESOURCE_IF_ACTIVE,
        )

    def test_permanent_or_shutdown_outcomes_stop_without_supervisor(self) -> None:
        source = _source_module()
        capture = _capture_module()
        outcomes = (
            capture.CaptureOutcome(capture.CaptureEndReason.SHUTDOWN),
            capture.CaptureOutcome(capture.CaptureEndReason.INVALID_INPUT),
            capture.CaptureOutcome(capture.CaptureEndReason.PERMISSION_DENIED),
            capture.CaptureOutcome(capture.CaptureEndReason.INTERNAL_ERROR),
            capture.CaptureOutcome(capture.CaptureEndReason.CAPABILITY_UNAVAILABLE),
        )

        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    source.capture_retry_action(outcome),
                    source.CaptureRetryAction.STOP,
                )


if __name__ == "__main__":
    unittest.main()
