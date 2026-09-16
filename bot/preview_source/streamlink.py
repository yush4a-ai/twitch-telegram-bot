from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from bot.deep_links import normalize_twitch_login

from .models import (
    PlaybackResolveResult,
    PlaybackResolveStatus,
    ResolvedPlayback,
    RetryDisposition,
    StreamlinkCapability,
    StreamlinkCapabilityReason,
)
from .process import (
    ResolverProcessExecutor,
    ResolverProcessTimeout,
    STDOUT_MAX_BYTES,
    StreamlinkProcessRunner,
)


LOGGER = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0
RESOLVE_TIMEOUT_SECONDS = 20.0
PROBE_OUTPUT_MAX_BYTES = 64 * 1024
MAX_RESOLVED_URL_CHARS = 16 * 1024
REQUIRED_OPTIONS = frozenset(
    {
        "--no-config",
        "--no-plugin-sideloading",
        "--no-plugin-cache",
        "--webbrowser",
        "--loglevel",
        "--stream-url",
        "--stream-sorting-excludes",
        "--twitch-supported-codecs",
        "--plugins",
    }
)
_VERSION_RE = re.compile(r"\bStreamlink\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
_OPTION_RE = re.compile(r"--[a-z0-9-]+", re.IGNORECASE)


def _unavailable(
    reason: StreamlinkCapabilityReason,
    *,
    version: str | None = None,
    twitch_plugin_available: bool = False,
) -> StreamlinkCapability:
    return StreamlinkCapability(
        available=False,
        reason=reason,
        version=version,
        twitch_plugin_available=twitch_plugin_available,
    )


async def probe_streamlink(
    *,
    executable: str | None = None,
    runner: Any | None = None,
    command_timeout: float = PROBE_TIMEOUT_SECONDS,
) -> StreamlinkCapability:
    resolved_executable = executable or shutil.which("streamlink")
    if not resolved_executable:
        return _unavailable(StreamlinkCapabilityReason.EXECUTABLE_MISSING)
    process_runner = runner or StreamlinkProcessRunner()

    async def run(argv: tuple[str, ...]):
        executor = ResolverProcessExecutor(
            runner=process_runner,
            timeout=command_timeout,
            stdout_max_bytes=PROBE_OUTPUT_MAX_BYTES,
        )
        return await executor.run(argv)

    try:
        version_result = await run((resolved_executable, "--version"))
    except ResolverProcessTimeout:
        return _unavailable(StreamlinkCapabilityReason.PROBE_TIMEOUT)
    except Exception:
        return _unavailable(StreamlinkCapabilityReason.PROBE_FAILED)
    if version_result.exit_code != 0 or version_result.stdout_overflowed:
        return _unavailable(StreamlinkCapabilityReason.VERSION_PROBE_FAILED)
    try:
        version_text = version_result.stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _unavailable(StreamlinkCapabilityReason.VERSION_PROBE_FAILED)
    version_match = _VERSION_RE.search(version_text)
    if version_match is None:
        return _unavailable(StreamlinkCapabilityReason.VERSION_PROBE_FAILED)
    major, minor, patch = (int(part) for part in version_match.groups())
    version = f"{major}.{minor}.{patch}"
    if major != 8:
        return _unavailable(
            StreamlinkCapabilityReason.VERSION_UNSUPPORTED, version=version
        )

    try:
        plugin_result = await run(
            (
                resolved_executable,
                "--no-config",
                "--no-plugin-sideloading",
                "--plugins",
            )
        )
    except ResolverProcessTimeout:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_TIMEOUT, version=version
        )
    except Exception:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED, version=version
        )
    if plugin_result.exit_code != 0 or plugin_result.stdout_overflowed:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED, version=version
        )
    try:
        plugin_text = plugin_result.stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED, version=version
        )
    plugin_tokens = {token.lower() for token in _TOKEN_RE.findall(plugin_text)}
    twitch_available = "twitch" in plugin_tokens
    if not twitch_available:
        return _unavailable(
            StreamlinkCapabilityReason.TWITCH_PLUGIN_MISSING,
            version=version,
        )

    try:
        help_result = await run((resolved_executable, "--help"))
    except ResolverProcessTimeout:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_TIMEOUT,
            version=version,
            twitch_plugin_available=True,
        )
    except Exception:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED,
            version=version,
            twitch_plugin_available=True,
        )
    if help_result.exit_code != 0 or help_result.stdout_overflowed:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED,
            version=version,
            twitch_plugin_available=True,
        )
    try:
        help_text = help_result.stdout_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _unavailable(
            StreamlinkCapabilityReason.PROBE_FAILED,
            version=version,
            twitch_plugin_available=True,
        )
    help_options = {option.lower() for option in _OPTION_RE.findall(help_text)}
    if not REQUIRED_OPTIONS.issubset(help_options):
        return _unavailable(
            StreamlinkCapabilityReason.CLI_CONTRACT_UNSUPPORTED,
            version=version,
            twitch_plugin_available=True,
        )
    return StreamlinkCapability(
        available=True,
        version=version,
        twitch_plugin_available=True,
    )


def _resolver_argv(executable: str, login: str) -> tuple[str, ...]:
    return (
        executable,
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
        f"https://www.twitch.tv/{login}",
        "best,best-unfiltered",
    )


def _parse_playback_url(output: bytes) -> ResolvedPlayback | None:
    try:
        text = output.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return None
    nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(nonempty_lines) != 1:
        return None
    url = nonempty_lines[0]
    if len(url) > MAX_RESOLVED_URL_CHARS or any(
        character.isspace()
        or unicodedata.category(character).startswith("C")
        for character in url
    ):
        return None
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or username is not None
        or password is not None
    ):
        return None
    return ResolvedPlayback(url)


class TwitchPlaybackResolver:
    def __init__(
        self,
        capability: StreamlinkCapability,
        *,
        runner: Any | None = None,
        timeout: float = RESOLVE_TIMEOUT_SECONDS,
        executable: str = "streamlink",
    ) -> None:
        self.capability = capability
        self.runner = runner or StreamlinkProcessRunner()
        self.timeout = timeout
        self.executable = executable

    async def resolve(self, twitch_login: str) -> PlaybackResolveResult:
        login = normalize_twitch_login(twitch_login)
        if login is None:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.INVALID_LOGIN,
                retry_disposition=RetryDisposition.TERMINAL,
                diagnostic_code="invalid_login",
            )
        if not self.capability.available:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.CAPABILITY_UNAVAILABLE,
                retry_disposition=RetryDisposition.TERMINAL,
                diagnostic_code="capability_unavailable",
            )
        executor = ResolverProcessExecutor(
            runner=self.runner,
            timeout=self.timeout,
            stdout_max_bytes=STDOUT_MAX_BYTES,
        )
        try:
            execution = await executor.run(_resolver_argv(self.executable, login))
        except ResolverProcessTimeout:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.TIMEOUT,
                retry_disposition=RetryDisposition.RETRYABLE,
                diagnostic_code="resolve_timeout",
            )
        except FileNotFoundError:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.CAPABILITY_UNAVAILABLE,
                retry_disposition=RetryDisposition.TERMINAL,
                diagnostic_code="executable_unavailable",
            )
        except Exception:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.INTERNAL_ERROR,
                retry_disposition=RetryDisposition.TERMINAL,
                diagnostic_code="resolver_internal_error",
            )
        if execution.exit_code != 0:
            diagnostic_code = execution.failure_code or "process_failed"
            LOGGER.warning("Streamlink resolver failed: reason=%s", diagnostic_code)
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.PROCESS_FAILED,
                retry_disposition=RetryDisposition.RETRYABLE,
                exit_code=execution.exit_code,
                diagnostic_code=diagnostic_code,
            )
        if execution.stdout_overflowed:
            playback = None
        else:
            playback = _parse_playback_url(execution.stdout_bytes)
        if playback is None:
            return PlaybackResolveResult(
                status=PlaybackResolveStatus.MALFORMED_OUTPUT,
                retry_disposition=RetryDisposition.TERMINAL,
                diagnostic_code="malformed_output",
            )
        return PlaybackResolveResult(
            status=PlaybackResolveStatus.RESOLVED,
            playback=playback,
            retry_disposition=RetryDisposition.NONE,
        )
