from __future__ import annotations

import asyncio
import importlib
import unittest
from unittest.mock import patch

from test_preview_source_process import FakeProcess, QueueRunner


SECRET_URL = "https://video.example/live.m3u8?token=resolver-secret"
REQUIRED_HELP = b"""
--no-config
--no-plugin-sideloading
--no-plugin-cache
--webbrowser
--loglevel
--stream-url
--stream-sorting-excludes
--twitch-supported-codecs
--plugins
"""


def _streamlink_module():
    try:
        return importlib.import_module("bot.preview_source.streamlink")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B Streamlink resolver module must exist") from error


def _source_module():
    try:
        return importlib.import_module("bot.preview_source")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B bot.preview_source package must exist") from error


def _successful_probe_runner(
    *,
    version: bytes = b"Streamlink 8.5.1\n",
    plugins: bytes = b"Available plugins: twitch, youtube\n",
    help_output: bytes = REQUIRED_HELP,
) -> QueueRunner:
    return QueueRunner(
        FakeProcess(stdout=version, returncode=0),
        FakeProcess(stdout=plugins, returncode=0),
        FakeProcess(stdout=help_output, returncode=0),
    )


def _available_capability():
    source = _source_module()
    return source.StreamlinkCapability(
        available=True,
        version="8.5.1",
        twitch_plugin_available=True,
    )


class CapabilityProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_executable_returns_typed_unavailable_without_spawn(self) -> None:
        source = _source_module()
        runner = QueueRunner()

        with patch("bot.preview_source.streamlink.shutil.which", return_value=None):
            result = await source.probe_streamlink(runner=runner)

        self.assertFalse(result.available)
        self.assertEqual(
            result.reason, source.StreamlinkCapabilityReason.EXECUTABLE_MISSING
        )
        self.assertEqual(runner.calls, [])

    async def test_supported_8x_version_and_required_options_are_available(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner(version=b"Streamlink 8.17.3+local\n")

        with patch(
            "bot.preview_source.streamlink.shutil.which",
            return_value="C:/secret/tools/streamlink.exe",
        ):
            result = await source.probe_streamlink(runner=runner)

        self.assertTrue(result.available)
        self.assertEqual(result.version, "8.17.3")
        self.assertTrue(result.twitch_plugin_available)
        self.assertNotIn("C:/secret", repr(result))

    async def test_plugin_probe_uses_plain_plugins_contract_not_json_schema(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner()

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertTrue(result.available)
        plugin_argv = runner.calls[1]
        self.assertEqual(
            plugin_argv,
            (
                "streamlink",
                "--no-config",
                "--no-plugin-sideloading",
                "--plugins",
            ),
        )
        self.assertNotIn("--json", plugin_argv)

    async def test_plugin_probe_exact_token_rejects_twitchfoo(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner(
            plugins=b"Available plugins:\n  twitchfoo, twitch-video\n"
        )

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertFalse(result.available)
        self.assertFalse(result.twitch_plugin_available)
        self.assertEqual(
            result.reason, source.StreamlinkCapabilityReason.TWITCH_PLUGIN_MISSING
        )

    async def test_plugin_probe_accepts_exact_token_across_formatting_changes(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner(
            plugins=b"Installed plugin set:\n [alpha] ; TWITCH\r\n youtube\n"
        )

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertTrue(result.available)
        self.assertTrue(result.twitch_plugin_available)

    async def test_major_9_is_unsupported_before_contract_review(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner(version=b"Streamlink 9.0.0\n")

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertFalse(result.available)
        self.assertEqual(
            result.reason, source.StreamlinkCapabilityReason.VERSION_UNSUPPORTED
        )
        self.assertEqual(result.version, "9.0.0")
        self.assertEqual(len(runner.calls), 1)

    async def test_supported_8x_missing_required_option_is_unsupported(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner(
            help_output=REQUIRED_HELP.replace(b"--webbrowser\n", b"")
        )

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertFalse(result.available)
        self.assertEqual(
            result.reason,
            source.StreamlinkCapabilityReason.CLI_CONTRACT_UNSUPPORTED,
        )

    async def test_probe_commands_contain_no_twitch_url_or_network_target(self) -> None:
        source = _source_module()
        runner = _successful_probe_runner()

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertTrue(result.available)
        rendered = " ".join(part for call in runner.calls for part in call)
        self.assertNotIn("twitch.tv", rendered)
        self.assertNotIn("http://", rendered)
        self.assertNotIn("https://", rendered)

    async def test_probe_timeout_is_typed_and_process_is_reaped(self) -> None:
        source = _source_module()
        process = FakeProcess()
        runner = QueueRunner(process)

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(
                runner=runner, command_timeout=0.001
            )

        self.assertFalse(result.available)
        self.assertEqual(result.reason, source.StreamlinkCapabilityReason.PROBE_TIMEOUT)
        self.assertTrue(process.exited.is_set())
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_malformed_version_is_sanitized_probe_failure(self) -> None:
        source = _source_module()
        secret = b"not-a-version C:/secret/location\n"
        runner = _successful_probe_runner(version=secret)

        with patch("bot.preview_source.streamlink.shutil.which", return_value="streamlink"):
            result = await source.probe_streamlink(runner=runner)

        self.assertFalse(result.available)
        self.assertEqual(
            result.reason, source.StreamlinkCapabilityReason.VERSION_PROBE_FAILED
        )
        self.assertIsNone(result.version)
        self.assertNotIn("secret", repr(result))


class PlaybackResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_login_resolves_one_strict_https_url(self) -> None:
        source = _source_module()
        runner = QueueRunner(
            FakeProcess(stdout=(SECRET_URL + "\n").encode(), returncode=0)
        )
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=runner, timeout=0.1
        )

        result = await resolver.resolve("Valid_Login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.RESOLVED)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.NONE)
        self.assertEqual(result.playback.to_capture_input().url, SECRET_URL)

    async def test_unavailable_capability_does_not_spawn(self) -> None:
        source = _source_module()
        runner = QueueRunner()
        capability = source.StreamlinkCapability(
            available=False,
            reason=source.StreamlinkCapabilityReason.EXECUTABLE_MISSING,
        )
        resolver = source.TwitchPlaybackResolver(capability, runner=runner)

        result = await resolver.resolve("valid_login")

        self.assertEqual(
            result.status, source.PlaybackResolveStatus.CAPABILITY_UNAVAILABLE
        )
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertEqual(runner.calls, [])

    async def test_invalid_login_is_rejected_before_process_spawn(self) -> None:
        source = _source_module()
        invalid_values = ("abc", "bad/name", "name;--help", "x" * 26, "тест")

        for value in invalid_values:
            with self.subTest(value=value):
                runner = QueueRunner()
                resolver = source.TwitchPlaybackResolver(
                    _available_capability(), runner=runner
                )
                result = await resolver.resolve(value)
                self.assertEqual(
                    result.status, source.PlaybackResolveStatus.INVALID_LOGIN
                )
                self.assertEqual(runner.calls, [])

    async def test_nonzero_exit_is_retryable_without_raw_output(self) -> None:
        source = _source_module()
        process = FakeProcess(
            stdout=b"error includes https://www.twitch.tv/private_login\n",
            stderr=b"token=stderr-secret",
            returncode=1,
        )
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=QueueRunner(process), timeout=0.1
        )

        with self.assertLogs("bot.preview_source", level="WARNING") as captured:
            result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.PROCESS_FAILED)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.RETRYABLE)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.diagnostic_code, "process_failed")
        rendered = "\n".join(captured.output)
        self.assertIn("reason=process_failed", rendered)
        self.assertNotIn("private_login", repr(result))
        self.assertNotIn("stderr-secret", repr(result))
        self.assertNotIn("private_login", rendered)
        self.assertNotIn("stderr-secret", rendered)

    async def test_nonzero_exit_uses_safe_stderr_classification(self) -> None:
        source = _source_module()
        process = FakeProcess(
            stderr=b"error: Failed acquiring client-integrity token token=secret-value",
            returncode=1,
        )
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=QueueRunner(process), timeout=0.1
        )

        with self.assertLogs("bot.preview_source", level="WARNING") as captured:
            result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.PROCESS_FAILED)
        self.assertEqual(result.diagnostic_code, "client_integrity")
        rendered = "\n".join(captured.output)
        self.assertIn("reason=client_integrity", rendered)
        self.assertNotIn("secret-value", rendered)

    async def test_timeout_is_retryable_and_reaped(self) -> None:
        source = _source_module()
        process = FakeProcess()
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=QueueRunner(process), timeout=0.001
        )

        result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.TIMEOUT)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.RETRYABLE)
        self.assertTrue(process.exited.is_set())
        self.assertGreaterEqual(process.wait_calls, 1)

    async def test_malformed_success_outputs_are_terminal(self) -> None:
        source = _source_module()
        malformed = (
            b"",
            b"https://one.example/a\nhttps://two.example/b\n",
            b"not-a-url\n",
            b"http://video.example/plain.m3u8\n",
            b"https://user:pass@video.example/a.m3u8\n",
            b"https:///missing-host\n",
            b"https://video.example/a.m3u8\x00tail\n",
            b"\xff\xfe\n",
        )

        for output in malformed:
            with self.subTest(output=output[:30]):
                resolver = source.TwitchPlaybackResolver(
                    _available_capability(),
                    runner=QueueRunner(FakeProcess(stdout=output, returncode=0)),
                    timeout=0.1,
                )
                result = await resolver.resolve("valid_login")
                self.assertEqual(
                    result.status, source.PlaybackResolveStatus.MALFORMED_OUTPUT
                )
                self.assertEqual(
                    result.retry_disposition, source.RetryDisposition.TERMINAL
                )

    async def test_unicode_control_and_format_characters_are_malformed(self) -> None:
        source = _source_module()
        malformed = (
            "https://video.example/a\u0080b.m3u8\n",
            "https://video.example/a\u202eb.m3u8\n",
            "https://video.example/a b.m3u8\n",
        )

        for output in malformed:
            with self.subTest(output=repr(output)):
                resolver = source.TwitchPlaybackResolver(
                    _available_capability(),
                    runner=QueueRunner(
                        FakeProcess(stdout=output.encode(), returncode=0)
                    ),
                    timeout=0.1,
                )
                result = await resolver.resolve("valid_login")
                self.assertEqual(
                    result.status, source.PlaybackResolveStatus.MALFORMED_OUTPUT
                )

    async def test_outer_whitespace_is_trimmed_without_changing_url(self) -> None:
        source = _source_module()
        runner = QueueRunner(
            FakeProcess(stdout=(" \r\n" + SECRET_URL + "\r\n ").encode(), returncode=0)
        )
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=runner, timeout=0.1
        )

        result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.RESOLVED)
        self.assertEqual(result.playback.to_capture_input().url, SECRET_URL)

    async def test_oversized_stdout_is_malformed_after_full_drain(self) -> None:
        source = _source_module()
        process = FakeProcess(stdout=b"x" * (16 * 1024 + 1), returncode=0)
        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=QueueRunner(process), timeout=0.1
        )

        result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.MALFORMED_OUTPUT)
        self.assertGreaterEqual(process.stdout.read_calls, 2)

    async def test_unexpected_runner_error_is_sanitized_internal_error(self) -> None:
        source = _source_module()

        class BrokenRunner:
            async def spawn(self, _argv):
                raise RuntimeError(SECRET_URL)

        resolver = source.TwitchPlaybackResolver(
            _available_capability(), runner=BrokenRunner(), timeout=0.1
        )

        result = await resolver.resolve("valid_login")

        self.assertEqual(result.status, source.PlaybackResolveStatus.INTERNAL_ERROR)
        self.assertEqual(result.retry_disposition, source.RetryDisposition.TERMINAL)
        self.assertNotIn(SECRET_URL, repr(result))

    async def test_concurrent_resolvers_keep_secret_results_isolated(self) -> None:
        source = _source_module()
        first_url = "https://a.example/live.m3u8?token=first"
        second_url = "https://b.example/live.m3u8?token=second"
        resolver_a = source.TwitchPlaybackResolver(
            _available_capability(),
            runner=QueueRunner(FakeProcess(stdout=first_url.encode(), returncode=0)),
            timeout=0.1,
        )
        resolver_b = source.TwitchPlaybackResolver(
            _available_capability(),
            runner=QueueRunner(FakeProcess(stdout=second_url.encode(), returncode=0)),
            timeout=0.1,
        )

        result_a, result_b = await asyncio.gather(
            resolver_a.resolve("channel_a"), resolver_b.resolve("channel_b")
        )

        self.assertEqual(result_a.playback.to_capture_input().url, first_url)
        self.assertEqual(result_b.playback.to_capture_input().url, second_url)
        self.assertNotIn("first", repr(result_a))
        self.assertNotIn("second", repr(result_b))


if __name__ == "__main__":
    unittest.main()
