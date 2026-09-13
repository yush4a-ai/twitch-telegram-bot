from __future__ import annotations

import importlib
import inspect
import unittest

from test_preview_source_process import FakeProcess, QueueRunner


PLAYBACK_URL = "https://video.example/live.m3u8?token=twitch-policy"


def _source_module():
    try:
        return importlib.import_module("bot.preview_source")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B bot.preview_source package must exist") from error


def _available_capability():
    source = _source_module()
    return source.StreamlinkCapability(
        available=True,
        version="8.5.1",
        twitch_plugin_available=True,
    )


async def _resolve_and_argv(login: str = "Valid_Login"):
    source = _source_module()
    runner = QueueRunner(
        FakeProcess(stdout=(PLAYBACK_URL + "\n").encode(), returncode=0)
    )
    resolver = source.TwitchPlaybackResolver(
        _available_capability(), runner=runner, timeout=0.1
    )
    result = await resolver.resolve(login)
    return source, result, runner.calls[0]


class TwitchResolverCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_resolver_argv_is_fixed_and_ordered(self) -> None:
        source, result, argv = await _resolve_and_argv()

        self.assertEqual(result.status, source.PlaybackResolveStatus.RESOLVED)
        self.assertEqual(
            argv,
            (
                "streamlink",
                "--no-config",
                "--no-plugin-sideloading",
                "--no-plugin-cache",
                "--webbrowser=no",
                "--loglevel",
                "none",
                "--stream-url",
                "--twitch-supported-codecs=h264",
                "--stream-sorting-excludes",
                ">720p60",
                "https://www.twitch.tv/valid_login",
                "best,best-unfiltered",
            ),
        )

    async def test_quality_filter_is_one_literal_argv_not_shell_syntax(self) -> None:
        _source, _result, argv = await _resolve_and_argv()

        filter_index = argv.index("--stream-sorting-excludes")
        self.assertEqual(argv[filter_index + 1], ">720p60")
        self.assertEqual(argv.count(">720p60"), 1)
        self.assertNotIn("shell", " ".join(argv).lower())

    async def test_strict_login_is_normalized_before_url_construction(self) -> None:
        _source, _result, argv = await _resolve_and_argv("Mixed_Case")

        self.assertIn("https://www.twitch.tv/mixed_case", argv)
        self.assertNotIn("https://www.twitch.tv/Mixed_Case", argv)

    async def test_browser_is_disabled_without_any_caller_option(self) -> None:
        source, _result, argv = await _resolve_and_argv()

        self.assertIn("--webbrowser=no", argv)
        parameters = inspect.signature(source.TwitchPlaybackResolver).parameters
        self.assertNotIn("browser", parameters)

    async def test_config_cache_and_plugin_sideloading_are_always_disabled(self) -> None:
        _source, _result, argv = await _resolve_and_argv()

        self.assertIn("--no-config", argv)
        self.assertIn("--no-plugin-cache", argv)
        self.assertIn("--no-plugin-sideloading", argv)

    async def test_codec_policy_is_h264_only(self) -> None:
        _source, _result, argv = await _resolve_and_argv()

        codec_args = [part for part in argv if "supported-codecs" in part]
        self.assertEqual(codec_args, ["--twitch-supported-codecs=h264"])
        rendered = " ".join(argv).lower()
        self.assertNotIn("h265", rendered)
        self.assertNotIn("av1", rendered)
        self.assertNotIn("aac", rendered)

    async def test_resolve_is_anonymous_and_contains_no_auth_flags(self) -> None:
        _source, _result, argv = await _resolve_and_argv()

        rendered = " ".join(argv).lower()
        for forbidden in ("oauth", "cookie", "authorization", "header", "token"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    async def test_fixed_policy_has_no_extra_args_or_custom_url_surface(self) -> None:
        source = _source_module()
        parameters = inspect.signature(source.TwitchPlaybackResolver).parameters

        self.assertNotIn("extra_args", parameters)
        self.assertNotIn("url", parameters)
        self.assertNotIn("options", parameters)


class StreamlinkQualityContractCharacterizationTests(unittest.TestCase):
    def test_720p60_weight_is_at_the_inclusive_filter_boundary(self) -> None:
        # Streamlink 8.5 generic weights: pixels + frame-rate multiplier.
        weights = {"720p": 720, "720p30": 750, "720p60": 780, "1080p": 1080}
        boundary = weights["720p60"]

        self.assertLessEqual(weights["720p"], boundary)
        self.assertLessEqual(weights["720p30"], boundary)
        self.assertLessEqual(weights["720p60"], boundary)

    def test_higher_weighted_renditions_are_excluded_from_normal_best(self) -> None:
        weights = {"720p60": 780, "900p": 900, "1080p": 1080, "1080p60": 1140}
        boundary = weights["720p60"]

        self.assertEqual(
            [name for name, weight in weights.items() if weight > boundary],
            ["900p", "1080p", "1080p60"],
        )

    def test_selector_preserves_unfiltered_fallback_when_all_are_excluded(self) -> None:
        selector = "best,best-unfiltered"

        self.assertEqual(selector.split(","), ["best", "best-unfiltered"])


if __name__ == "__main__":
    unittest.main()
