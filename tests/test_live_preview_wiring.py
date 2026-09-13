from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import main as application
from bot.config import PreviewCaptureConfig, PreviewRuntimeConfig
from bot.live_preview_provider import LivePreviewArtifactProvider
from bot.preview_analysis import HighlightAnalyzer
from bot.preview_capture import (
    CapabilityReason,
    CaptureCapability,
    CaptureSettings,
)
from bot.preview_render import RenderCapability, RenderCapabilityReason
from bot.preview_runtime import (
    DisabledPreviewObserver,
    NoopPreviewArtifactProvider,
    PreviewManager,
)
from bot.preview_source import (
    StreamlinkCapability,
    StreamlinkCapabilityReason,
    TwitchCaptureSource,
    TwitchPlaybackResolver,
)


def _streamlink_available() -> StreamlinkCapability:
    return StreamlinkCapability(
        available=True,
        version="8.5.0",
        twitch_plugin_available=True,
    )


def _streamlink_unavailable() -> StreamlinkCapability:
    return StreamlinkCapability(
        available=False,
        reason=StreamlinkCapabilityReason.EXECUTABLE_MISSING,
    )


def _render_available() -> RenderCapability:
    return RenderCapability(
        available=True,
        ffmpeg_executable="ffmpeg",
        ffprobe_executable="ffprobe",
        major_version=9,
    )


class FakeCaptureService:
    def __init__(
        self,
        *,
        available: bool = True,
        close_effects: tuple[BaseException | None, ...] = (),
    ) -> None:
        self.capability = CaptureCapability(
            available=available,
            reason=None if available else CapabilityReason.MISSING_EXECUTABLE,
            ffmpeg_executable="ffmpeg" if available else None,
        )
        self.close_calls = 0
        self._close_effects = list(close_effects)

    async def close(self) -> None:
        self.close_calls += 1
        if self._close_effects:
            effect = self._close_effects.pop(0)
            if effect is not None:
                raise effect


class FakeRenderer:
    def __init__(self, capability: RenderCapability | BaseException) -> None:
        self._capability = capability
        self.capability_calls = 0

    async def capability(self) -> RenderCapability:
        self.capability_calls += 1
        if isinstance(self._capability, BaseException):
            raise self._capability
        return self._capability


class LivePreviewWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runtime_enabled = PreviewRuntimeConfig(enabled=True)
        self.capture_config = PreviewCaptureConfig(
            enabled=True,
            buffer_seconds=120,
            buffer_max_bytes=150 * 1024 * 1024,
        )

    async def _build(
        self,
        *,
        runtime: PreviewRuntimeConfig | None = None,
        capture: PreviewCaptureConfig | None = None,
        capture_owner=None,
    ):
        kwargs = {}
        if capture_owner is not None:
            kwargs["capture_owner"] = capture_owner
        return await application._build_preview_runtime(
            object(),
            object(),
            preview_config=runtime or self.runtime_enabled,
            capture_config=capture or self.capture_config,
            poll_interval_seconds=60,
            build_content=lambda _observation, _destination: None,
            **kwargs,
        )

    async def test_runtime_disabled_uses_noop_without_capability_or_capture_work(self) -> None:
        streamlink_probe = AsyncMock(side_effect=AssertionError("probe must not run"))
        capture_create = AsyncMock(side_effect=AssertionError("capture must not be created"))
        renderer_create = Mock(side_effect=AssertionError("renderer must not be created"))
        source_create = Mock(side_effect=AssertionError("source must not be created"))
        provider_create = Mock(side_effect=AssertionError("provider must not be created"))

        with (
            patch.object(application, "probe_streamlink", streamlink_probe, create=True),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=capture_create),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=renderer_create),
                create=True,
            ),
            patch.object(application, "TwitchCaptureSource", source_create, create=True),
            patch.object(
                application,
                "LivePreviewArtifactProvider",
                provider_create,
                create=True,
            ),
            patch.object(application, "PreviewManager", wraps=PreviewManager) as manager_create,
        ):
            manager, capture_service = await self._build(runtime=PreviewRuntimeConfig())

        self.assertIsNone(capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertFalse(manager.health_snapshot()["enabled"])
        self.assertEqual(manager.health_snapshot()["disabled_reason"], "config_disabled")
        self.assertEqual(streamlink_probe.await_count, 0)
        self.assertEqual(capture_create.await_count, 0)
        self.assertEqual(renderer_create.call_count, 0)
        self.assertEqual(source_create.call_count, 0)
        self.assertEqual(provider_create.call_count, 0)
        self.assertEqual(manager_create.call_count, 1)
        self.assertIsNone(manager.start())
        await manager.shutdown()

    async def test_enabled_success_builds_exact_real_provider_graph_once_from_path_defaults(self) -> None:
        capture_service = FakeCaptureService()
        renderer = FakeRenderer(_render_available())
        streamlink_probe = AsyncMock(return_value=_streamlink_available())
        capture_create = AsyncMock(return_value=capture_service)
        renderer_create = Mock(return_value=renderer)

        with (
            patch.object(application, "probe_streamlink", streamlink_probe, create=True),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=capture_create),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=renderer_create),
                create=True,
            ),
            patch.object(application, "PreviewManager", wraps=PreviewManager) as manager_create,
            patch.object(TwitchCaptureSource, "open", new_callable=AsyncMock) as source_open,
        ):
            manager, owned_capture_service = await self._build()

        provider = manager._provider
        self.assertIsInstance(provider, LivePreviewArtifactProvider)
        self.assertIsInstance(provider._source, TwitchCaptureSource)
        self.assertIsInstance(provider._source.resolver, TwitchPlaybackResolver)
        self.assertIs(provider._source.capture_service, capture_service)
        self.assertIsInstance(provider._analyzer, HighlightAnalyzer)
        self.assertIs(provider._renderer, renderer)
        self.assertIs(owned_capture_service, capture_service)
        self.assertTrue(manager.health_snapshot()["enabled"])
        streamlink_probe.assert_awaited_once_with()
        capture_create.assert_awaited_once_with(
            settings=CaptureSettings(
                buffer_seconds=120,
                buffer_max_bytes=150 * 1024 * 1024,
            )
        )
        renderer_create.assert_called_once_with()
        self.assertEqual(renderer.capability_calls, 1)
        self.assertEqual(provider._source.resolver.executable, "streamlink")
        self.assertEqual(provider._analyzer._ffmpeg_executable, "ffmpeg")
        self.assertEqual(source_open.await_count, 0)
        self.assertEqual(manager_create.call_count, 1)
        await application._shutdown_preview_runtime(manager, capture_service)
        self.assertEqual(capture_service.close_calls, 1)

    async def test_streamlink_unavailable_is_sanitized_noop_and_stops_later_probes(self) -> None:
        capture_create = AsyncMock(side_effect=AssertionError("capture must not be created"))
        renderer_create = Mock(side_effect=AssertionError("renderer must not be created"))
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_unavailable()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=capture_create),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=renderer_create),
                create=True,
            ),
        ):
            manager, capture_service = await self._build()

        self.assertIsNone(capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertFalse(manager.health_snapshot()["enabled"])
        self.assertEqual(
            manager.health_snapshot()["disabled_reason"], "streamlink_unavailable"
        )
        self.assertEqual(capture_create.await_count, 0)
        self.assertEqual(renderer_create.call_count, 0)
        await manager.shutdown()

    async def test_capture_unavailable_closes_partial_service_and_fails_open(self) -> None:
        capture_service = FakeCaptureService(available=False)
        renderer_create = Mock(side_effect=AssertionError("renderer must not be created"))
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=renderer_create),
                create=True,
            ),
        ):
            manager, owned_capture_service = await self._build()

        self.assertIsNone(owned_capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(
            manager.health_snapshot()["disabled_reason"], "capture_unavailable"
        )
        self.assertEqual(capture_service.close_calls, 1)
        self.assertEqual(renderer_create.call_count, 0)
        await manager.shutdown()

    async def test_renderer_unavailable_closes_capture_service_and_fails_open(self) -> None:
        capture_service = FakeCaptureService()
        renderer = FakeRenderer(
            RenderCapability(
                available=False,
                reason=RenderCapabilityReason.MISSING_EXECUTABLE,
            )
        )
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
                create=True,
            ),
        ):
            manager, owned_capture_service = await self._build()

        self.assertIsNone(owned_capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(
            manager.health_snapshot()["disabled_reason"], "render_unavailable"
        )
        self.assertEqual(renderer.capability_calls, 1)
        self.assertEqual(capture_service.close_calls, 1)
        await manager.shutdown()

    async def test_preview_config_error_uses_noop_without_any_probe(self) -> None:
        streamlink_probe = AsyncMock(side_effect=AssertionError("probe must not run"))
        capture_create = AsyncMock(side_effect=AssertionError("capture must not be created"))
        with (
            patch.object(application, "probe_streamlink", streamlink_probe, create=True),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=capture_create),
                create=True,
            ),
        ):
            manager, capture_service = await self._build(
                runtime=PreviewRuntimeConfig(
                    enabled=False,
                    disabled_reason="config_error",
                )
            )

        self.assertIsNone(capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(manager.health_snapshot()["disabled_reason"], "config_error")
        self.assertEqual(streamlink_probe.await_count, 0)
        self.assertEqual(capture_create.await_count, 0)
        await manager.shutdown()

    async def test_capture_config_error_uses_noop_without_any_probe(self) -> None:
        streamlink_probe = AsyncMock(side_effect=AssertionError("probe must not run"))
        with patch.object(
            application, "probe_streamlink", streamlink_probe, create=True
        ):
            manager, capture_service = await self._build(
                capture=PreviewCaptureConfig(
                    enabled=False,
                    disabled_reason="config_error",
                )
            )

        self.assertIsNone(capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(manager.health_snapshot()["disabled_reason"], "config_error")
        self.assertEqual(streamlink_probe.await_count, 0)
        await manager.shutdown()

    async def test_unexpected_startup_error_is_sanitized_and_closes_partial_service(self) -> None:
        capture_service = FakeCaptureService()
        renderer = FakeRenderer(RuntimeError("do-not-log-this-secret"))
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
                create=True,
            ),
            self.assertLogs("main", "ERROR") as logs,
        ):
            manager, owned_capture_service = await self._build()

        self.assertIsNone(owned_capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(manager.health_snapshot()["disabled_reason"], "startup_error")
        self.assertEqual(capture_service.close_calls, 1)
        self.assertNotIn("do-not-log-this-secret", "\n".join(logs.output))
        await manager.shutdown()

    async def test_capture_create_exception_fails_open_without_phantom_cleanup(self) -> None:
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(
                    create=AsyncMock(side_effect=RuntimeError("capture create failed"))
                ),
                create=True,
            ),
            self.assertLogs("main", "ERROR"),
        ):
            manager, capture_service = await self._build()

        self.assertIsNone(capture_service)
        self.assertIsInstance(manager._provider, NoopPreviewArtifactProvider)
        self.assertEqual(manager.health_snapshot()["disabled_reason"], "startup_error")
        await manager.shutdown()

    async def test_startup_cancellation_closes_created_capture_service(self) -> None:
        capture_service = FakeCaptureService()
        capability_started = asyncio.Event()

        async def wait_for_cancellation() -> RenderCapability:
            capability_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        renderer = SimpleNamespace(capability=wait_for_cancellation)
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
                create=True,
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
                create=True,
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
                create=True,
            ),
        ):
            task = asyncio.create_task(self._build())
            await asyncio.sleep(0)
            if task.done():
                await task
            await capability_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(capture_service.close_calls, 1)

    async def test_cancellation_close_failure_keeps_owner_for_outer_shutdown_retry(self) -> None:
        capture_service = FakeCaptureService(
            close_effects=(RuntimeError("cancel-cleanup-secret"), None)
        )
        capability_started = asyncio.Event()
        owned_capture_service = None

        def capture_owner(service) -> None:
            nonlocal owned_capture_service
            owned_capture_service = service

        async def wait_for_cancellation() -> RenderCapability:
            capability_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        renderer = SimpleNamespace(capability=wait_for_cancellation)
        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
            ),
            self.assertLogs("main", "ERROR") as logs,
        ):
            task = asyncio.create_task(self._build(capture_owner=capture_owner))
            await asyncio.wait_for(capability_started.wait(), timeout=0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertIs(owned_capture_service, capture_service)
            await application._shutdown_preview_runtime(
                None, owned_capture_service
            )

        self.assertEqual(capture_service.close_calls, 2)
        self.assertNotIn("cancel-cleanup-secret", "\n".join(logs.output))

    async def test_shutdown_waits_for_manager_before_closing_capture_once(self) -> None:
        events: list[str] = []

        class Manager:
            async def shutdown(self) -> None:
                events.append("manager-start")
                await asyncio.sleep(0)
                events.append("manager-done")

        class Capture:
            close_calls = 0

            async def close(self) -> None:
                self.close_calls += 1
                events.append("capture-close")

        capture_service = Capture()
        await application._shutdown_preview_runtime(Manager(), capture_service)

        self.assertEqual(
            events,
            ["manager-start", "manager-done", "capture-close"],
        )
        self.assertEqual(capture_service.close_calls, 1)

    async def test_partial_close_exception_retains_owner_for_final_sanitized_retry(self) -> None:
        capture_service = FakeCaptureService(
            close_effects=(RuntimeError("secret-path-and-token"), None)
        )
        renderer = FakeRenderer(
            RenderCapability(
                available=False,
                reason=RenderCapabilityReason.MISSING_EXECUTABLE,
            )
        )

        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
            ),
            self.assertLogs("main", "ERROR") as logs,
        ):
            manager, owned_capture_service = await self._build()
            self.assertIs(owned_capture_service, capture_service)
            await application._shutdown_preview_runtime(
                manager, owned_capture_service
            )

        self.assertEqual(capture_service.close_calls, 2)
        self.assertNotIn("secret-path-and-token", "\n".join(logs.output))

    async def test_partial_close_timeout_retains_owner_for_final_retry(self) -> None:
        class TimeoutOnceCaptureService(FakeCaptureService):
            async def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    await asyncio.Event().wait()

        capture_service = TimeoutOnceCaptureService()
        renderer = FakeRenderer(
            RenderCapability(
                available=False,
                reason=RenderCapabilityReason.MISSING_EXECUTABLE,
            )
        )

        with (
            patch.object(
                application,
                "probe_streamlink",
                AsyncMock(return_value=_streamlink_available()),
            ),
            patch.object(
                application,
                "CaptureService",
                SimpleNamespace(create=AsyncMock(return_value=capture_service)),
            ),
            patch.object(
                application,
                "PreviewRenderer",
                SimpleNamespace(create=Mock(return_value=renderer)),
            ),
            patch.object(application, "SHUTDOWN_STEP_TIMEOUT_SECONDS", 0.01),
            self.assertLogs("main", "ERROR"),
        ):
            manager, owned_capture_service = await self._build()
            self.assertIs(owned_capture_service, capture_service)
            await application._shutdown_preview_runtime(
                manager, owned_capture_service
            )

        self.assertEqual(capture_service.close_calls, 2)

    async def test_fail_soft_manager_start_replaces_observer_and_closes_capture(self) -> None:
        events: list[str] = []

        class FailSoftManager:
            def start(self):
                events.append("manager-start")
                return None

            async def shutdown(self) -> None:
                events.append("manager-shutdown")

        class Capture:
            async def close(self) -> None:
                events.append("capture-close")

        with self.assertLogs("main", "ERROR"):
            observer, owned_capture_service = await application._start_preview_runtime(
                FailSoftManager(), Capture()
            )

        self.assertIsInstance(observer, DisabledPreviewObserver)
        self.assertIsNone(owned_capture_service)
        self.assertEqual(
            events,
            ["manager-start", "manager-shutdown", "capture-close"],
        )

    async def test_main_wires_one_manager_through_start_poller_dispatcher_and_shutdown(self) -> None:
        events: list[str] = []
        config = SimpleNamespace(
            db_path=":memory:",
            token_encryption_key=None,
            telegram_bot_token="unit-test-token-never-used",
            twitch_client_id="client",
            twitch_client_secret="secret",
            poll_interval_seconds=60,
            owner_chat_id=None,
            oauth_public_base_url="https://example.test",
            oauth_host="127.0.0.1",
            oauth_port=0,
            auto_track=(),
            preview=self.runtime_enabled,
            preview_capture=self.capture_config,
        )
        db = SimpleNamespace(
            connect=AsyncMock(),
            invalidate_live_follow_counts_after_restart=AsyncMock(),
            invalidate_live_chat_stats_after_restart=AsyncMock(),
            all_telegram_channels=AsyncMock(return_value=[]),
            all_distinct_group_chat_ids=AsyncMock(return_value=[]),
            close=AsyncMock(),
        )
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )

        async def wait_forever(*_args, **_kwargs):
            await asyncio.Event().wait()

        dispatcher = SimpleNamespace(start_polling=AsyncMock(side_effect=wait_forever))
        dispatcher_state: dict[str, object] = {}

        class FakeDispatcher(dict):
            def __setitem__(self, key, value):
                dispatcher_state[key] = value
                super().__setitem__(key, value)

            start_polling = dispatcher.start_polling

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        follow_listener = SimpleNamespace(
            run=AsyncMock(side_effect=wait_forever),
            wait_initial_ready=AsyncMock(),
            stop=AsyncMock(),
        )
        manager = SimpleNamespace(start=Mock(return_value=object()))
        capture_service = object()

        async def build_runtime(*_args, **kwargs):
            self.assertTrue(callable(kwargs["capture_owner"]))
            kwargs["capture_owner"](capture_service)
            events.append("build")
            return manager, capture_service

        async def start_runtime(received_manager, received_capture):
            self.assertIs(received_manager, manager)
            self.assertIs(received_capture, capture_service)
            events.append("start")
            return received_manager, received_capture

        async def shutdown_runtime(received_manager, received_capture):
            self.assertIs(received_manager, manager)
            self.assertIs(received_capture, capture_service)
            events.append("shutdown")

        poller = SimpleNamespace(
            run=AsyncMock(side_effect=RuntimeError("poller crash")),
            stop=Mock(),
            shutdown=AsyncMock(),
        )

        def create_poller(*_args, **kwargs):
            self.assertIs(kwargs["preview_observer"], manager)
            events.append("poller")
            return poller

        oauth_server = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            set_health_provider=Mock(),
        )

        async def execute(factory, _description):
            return await factory()

        build_mock = AsyncMock(side_effect=build_runtime)
        start_mock = AsyncMock(side_effect=start_runtime)
        shutdown_mock = AsyncMock(side_effect=shutdown_runtime)

        with (
            patch.object(application, "load_config", return_value=config),
            patch.object(application, "Database", return_value=db),
            patch.object(application, "Bot", return_value=bot),
            patch.object(application, "Dispatcher", side_effect=FakeDispatcher),
            patch.object(application, "setup_middlewares"),
            patch.object(application, "register_all_handlers"),
            patch.object(application, "_with_startup_retry", side_effect=execute),
            patch.object(application.aiohttp, "ClientSession", return_value=FakeSession()),
            patch.object(application, "TwitchClient", return_value=SimpleNamespace()),
            patch.object(application, "TokenStore", return_value=SimpleNamespace()),
            patch.object(application, "ChatListener", return_value=SimpleNamespace()),
            patch.object(application, "FollowEventListener", return_value=follow_listener),
            patch.object(application, "StreamPoller", side_effect=create_poller),
            patch.object(application, "OAuthCallbackServer", return_value=oauth_server),
            patch.object(application, "_build_preview_runtime", new=build_mock),
            patch.object(
                application,
                "_start_preview_runtime",
                new=start_mock,
                create=True,
            ),
            patch.object(application, "_shutdown_preview_runtime", new=shutdown_mock),
        ):
            with self.assertRaisesRegex(RuntimeError, "poller crash"):
                await application.main()

        build_mock.assert_awaited_once()
        start_mock.assert_awaited_once_with(manager, capture_service)
        shutdown_mock.assert_awaited_once_with(manager, capture_service)
        self.assertIs(dispatcher_state["preview_manager"], manager)
        self.assertEqual(events, ["build", "poller", "start", "shutdown"])


if __name__ == "__main__":
    unittest.main()
