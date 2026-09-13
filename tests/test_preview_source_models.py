from __future__ import annotations

import importlib
import unittest


SECRET_URL = "https://video.example/live.m3u8?token=never-expose-this"


def _source_module():
    try:
        return importlib.import_module("bot.preview_source")
    except ModuleNotFoundError as error:
        raise AssertionError("P4B bot.preview_source package must exist") from error


class ResolvedPlaybackModelTests(unittest.TestCase):
    def test_resolved_playback_has_redacted_repr_and_str(self) -> None:
        source = _source_module()

        playback = source.ResolvedPlayback(SECRET_URL)

        self.assertEqual(repr(playback), "ResolvedPlayback(<redacted>)")
        self.assertEqual(str(playback), "ResolvedPlayback(<redacted>)")
        self.assertNotIn(SECRET_URL, repr(playback))
        self.assertNotIn("never-expose-this", str(playback))

    def test_resolved_playback_exposes_url_only_as_capture_input(self) -> None:
        source = _source_module()

        playback = source.ResolvedPlayback(SECRET_URL)
        capture_input = playback.to_capture_input()

        self.assertEqual(capture_input.url, SECRET_URL)
        self.assertEqual(repr(capture_input), "UrlCaptureInput(<redacted>)")
        self.assertNotIn("url", playback.__dataclass_fields__)
        self.assertIn("_secret_url", playback.__dataclass_fields__)

    def test_resolve_result_repr_never_contains_playback_or_diagnostic_secret(self) -> None:
        source = _source_module()
        playback = source.ResolvedPlayback(SECRET_URL)

        success = source.PlaybackResolveResult(
            status=source.PlaybackResolveStatus.RESOLVED,
            playback=playback,
            retry_disposition=source.RetryDisposition.NONE,
        )
        failure = source.PlaybackResolveResult(
            status=source.PlaybackResolveStatus.MALFORMED_OUTPUT,
            retry_disposition=source.RetryDisposition.TERMINAL,
            diagnostic_code=SECRET_URL,
        )

        for value in (success, failure):
            rendered = f"{value!r} {value}"
            self.assertNotIn(SECRET_URL, rendered)
            self.assertNotIn("never-expose-this", rendered)

    def test_resolve_result_enforces_success_payload_invariants(self) -> None:
        source = _source_module()

        with self.assertRaises(ValueError):
            source.PlaybackResolveResult(
                status=source.PlaybackResolveStatus.RESOLVED,
                retry_disposition=source.RetryDisposition.NONE,
            )
        with self.assertRaises(ValueError):
            source.PlaybackResolveResult(
                status=source.PlaybackResolveStatus.PROCESS_FAILED,
                playback=source.ResolvedPlayback(SECRET_URL),
                retry_disposition=source.RetryDisposition.RETRYABLE,
            )

    def test_playback_status_model_has_only_reliable_categories(self) -> None:
        source = _source_module()

        self.assertEqual(
            {item.name for item in source.PlaybackResolveStatus},
            {
                "RESOLVED",
                "INVALID_LOGIN",
                "CAPABILITY_UNAVAILABLE",
                "TIMEOUT",
                "PROCESS_FAILED",
                "MALFORMED_OUTPUT",
                "INTERNAL_ERROR",
            },
        )
        self.assertNotIn("OFFLINE", source.PlaybackResolveStatus.__members__)
        self.assertNotIn("NETWORK", source.PlaybackResolveStatus.__members__)
        self.assertNotIn("CI_REQUIRED", source.PlaybackResolveStatus.__members__)


class CaptureBridgeModelTests(unittest.TestCase):
    def test_twitch_capture_key_repr_redacts_both_identity_fields(self) -> None:
        source = _source_module()

        key = source.TwitchCaptureKey("sensitive_login", "physical-secret-42")

        self.assertEqual(repr(key), "TwitchCaptureKey(<redacted>)")
        self.assertNotIn("sensitive_login", str(key))
        self.assertNotIn("physical-secret-42", str(key))

    def test_capture_start_result_repr_redacts_key_handle_and_diagnostic(self) -> None:
        source = _source_module()
        key = source.TwitchCaptureKey("sensitive_login", "physical-secret-42")
        outcome = importlib.import_module("bot.preview_capture").CaptureOutcome(
            importlib.import_module("bot.preview_capture").CaptureEndReason.START_FAILED,
            diagnostic_code=SECRET_URL,
        )

        result = source.TwitchCaptureStartResult(
            status=source.TwitchCaptureStartStatus.CAPTURE_NOT_STARTED,
            key=key,
            capture_outcome=outcome,
            retry_disposition=source.RetryDisposition.RETRYABLE,
        )

        rendered = f"{result!r} {result}"
        self.assertNotIn("sensitive_login", rendered)
        self.assertNotIn("physical-secret-42", rendered)
        self.assertNotIn(SECRET_URL, rendered)


if __name__ == "__main__":
    unittest.main()
