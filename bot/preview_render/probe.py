from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .models import (
    RenderCapability,
    RenderCapabilityReason,
    RenderConfig,
    RenderedPreviewMetadata,
)
from .process import RenderCommandOutputError, RenderProcessRunner


MAX_PROBE_OUTPUT = 256 * 1024
SUPPORTED_MAJOR_VERSIONS = frozenset({9})
REQUIRED_FILTERS = frozenset(
    {"trim", "setpts", "concat", "scale", "pad", "setsar", "format", "fps"}
)
_VERSION = re.compile(r"\bversion\s+(\d+)(?:\.|\s)", re.IGNORECASE)
_UNSUPPORTED_TRANSFER = frozenset({"smpte2084", "arib-std-b67"})
_SUPPORTED_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
_PROGRESSIVE_FIELD_ORDERS = frozenset({"progressive", "unknown"})


class SourceProbeError(ValueError):
    def __init__(self, diagnostic_code: str) -> None:
        super().__init__(diagnostic_code)
        self.diagnostic_code = diagnostic_code


class OutputProbeError(ValueError):
    pass


class ProbeTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class SourceVideoInfo:
    fps: Fraction
    width: int
    height: int
    pixel_format: str


def _decode_json(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid probe JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid probe document")
    return parsed


def _positive_fraction(value: object) -> Fraction:
    if not isinstance(value, str):
        raise ValueError("frame rate is not textual")
    try:
        parsed = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("frame rate is malformed") from exc
    if parsed <= 0:
        raise ValueError("frame rate is not positive")
    return parsed


def _rate_tolerance(rate: Fraction) -> float:
    return max(0.05, float(rate) * 0.005)


def _rates_compatible(left: Fraction, right: Fraction) -> bool:
    return abs(float(left - right)) <= max(
        _rate_tolerance(left), _rate_tolerance(right)
    )


async def _bounded_command(
    runner: Any,
    argv: tuple[str, ...],
    *,
    timeout: float,
) -> tuple[int, bytes] | None:
    try:
        return await runner.run_command(
            argv, timeout=timeout, max_output=MAX_PROBE_OUTPUT
        )
    except TimeoutError as exc:
        raise ProbeTimeoutError("probe timed out") from exc
    except (FileNotFoundError, OSError, RenderCommandOutputError):
        return None


async def _capability_command(
    runner: Any,
    argv: tuple[str, ...],
    *,
    timeout: float,
) -> tuple[int, bytes] | None:
    try:
        return await _bounded_command(runner, argv, timeout=timeout)
    except ProbeTimeoutError:
        return None


def _major(output: bytes) -> int | None:
    try:
        match = _VERSION.search(output.decode("utf-8", errors="strict"))
    except UnicodeError:
        return None
    return int(match.group(1)) if match else None


def _table_names(output: str, flag_lengths: frozenset[int]) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 2 or len(columns[0]) not in flag_lengths:
            continue
        names.update(name for name in columns[1].split(",") if name)
    return names


async def probe_capability(
    ffmpeg_executable: str,
    ffprobe_executable: str,
    *,
    runner: Any | None = None,
    timeout: float = 5.0,
) -> RenderCapability:
    command_runner = runner or RenderProcessRunner()
    ffmpeg_version = await _capability_command(
        command_runner, (ffmpeg_executable, "-version"), timeout=timeout
    )
    ffprobe_version = await _capability_command(
        command_runner, (ffprobe_executable, "-version"), timeout=timeout
    )
    if ffmpeg_version is None or ffprobe_version is None:
        return RenderCapability(
            False, RenderCapabilityReason.MISSING_EXECUTABLE
        )
    if ffmpeg_version[0] != 0 or ffprobe_version[0] != 0:
        return RenderCapability(False, RenderCapabilityReason.PROBE_FAILED)
    ffmpeg_major = _major(ffmpeg_version[1])
    ffprobe_major = _major(ffprobe_version[1])
    if ffmpeg_major is None or ffprobe_major is None:
        return RenderCapability(False, RenderCapabilityReason.PROBE_FAILED)
    if ffmpeg_major != ffprobe_major:
        return RenderCapability(False, RenderCapabilityReason.MAJOR_MISMATCH)
    if ffmpeg_major not in SUPPORTED_MAJOR_VERSIONS:
        return RenderCapability(
            False, RenderCapabilityReason.UNSUPPORTED_VERSION
        )

    inspections = (
        ("-hide_banner", "-encoders"),
        ("-hide_banner", "-muxers"),
        ("-hide_banner", "-filters"),
    )
    outputs: list[str] = []
    for options in inspections:
        result = await _capability_command(
            command_runner,
            (ffmpeg_executable, *options),
            timeout=timeout,
        )
        if result is None or result[0] != 0:
            return RenderCapability(False, RenderCapabilityReason.PROBE_FAILED)
        outputs.append(result[1].decode("utf-8", errors="replace"))
    encoders, muxers, filters = outputs
    encoder_names = _table_names(encoders, frozenset({6}))
    muxer_names = _table_names(muxers, frozenset({1, 2}))
    filter_names = _table_names(filters, frozenset({2, 3}))
    if (
        "libx264" not in encoder_names
        or "mp4" not in muxer_names
        or not REQUIRED_FILTERS.issubset(filter_names)
    ):
        return RenderCapability(
            False, RenderCapabilityReason.UNSUPPORTED_CAPABILITY
        )
    return RenderCapability(
        True,
        ffmpeg_executable=ffmpeg_executable,
        ffprobe_executable=ffprobe_executable,
        major_version=ffmpeg_major,
    )


def build_source_probe_argv(
    ffprobe_executable: str, manifest_path: str | Path
) -> tuple[str, ...]:
    entries = (
        "stream=index,codec_type,width,height,pix_fmt,field_order,"
        "sample_aspect_ratio,avg_frame_rate,r_frame_rate,color_transfer:"
        "stream_tags=rotate:stream_side_data=rotation"
    )
    return (
        ffprobe_executable,
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-protocol_whitelist",
        "file",
        "-show_entries",
        entries,
        "-of",
        "json",
        str(manifest_path),
    )


def _rotation(stream: dict[str, Any]) -> float:
    values: list[object] = []
    tags = stream.get("tags")
    if isinstance(tags, dict):
        values.append(tags.get("rotate", 0))
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict):
                values.append(item.get("rotation", 0))
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SourceProbeError("source_unsupported") from exc
        if not math.isfinite(number) or not math.isclose(number, 0.0, abs_tol=1e-6):
            return number
    return 0.0


def _valid_sar(value: object) -> bool:
    if value in (None, "N/A"):
        return True
    if not isinstance(value, str):
        return False
    separator = ":" if ":" in value else "/"
    try:
        numerator, denominator = value.split(separator, 1)
        left = int(numerator)
        right = int(denominator)
        return left > 0 and right > 0 and left == right
    except (ValueError, ZeroDivisionError):
        return False


def parse_source_probe(payload: bytes) -> SourceVideoInfo:
    try:
        document = _decode_json(payload)
        streams = document["streams"]
        if not isinstance(streams, list):
            raise ValueError("streams are invalid")
        videos = [
            item
            for item in streams
            if isinstance(item, dict) and item.get("codec_type") == "video"
        ]
        if len(videos) != 1:
            raise SourceProbeError("source_unsupported")
        stream = videos[0]
        average = _positive_fraction(stream.get("avg_frame_rate"))
        nominal = _positive_fraction(stream.get("r_frame_rate"))
    except SourceProbeError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SourceProbeError("source_fps_unreliable") from exc
    if not _rates_compatible(average, nominal):
        raise SourceProbeError("source_fps_unreliable")

    try:
        width = stream.get("width")
        height = stream.get("height")
        pixel_format = stream.get("pix_fmt")
        field_order = stream.get("field_order", "unknown")
        transfer = stream.get("color_transfer", "unknown")
        unsupported = (
            type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
            or pixel_format not in _SUPPORTED_PIXEL_FORMATS
            or field_order not in _PROGRESSIVE_FIELD_ORDERS
            or transfer in _UNSUPPORTED_TRANSFER
            or not _valid_sar(stream.get("sample_aspect_ratio"))
            or not math.isclose(_rotation(stream), 0.0, abs_tol=1e-6)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceProbeError("source_unsupported") from exc
    if unsupported:
        raise SourceProbeError("source_unsupported")
    return SourceVideoInfo(average, width, height, pixel_format)


def resolve_common_fps(infos: tuple[SourceVideoInfo, ...]) -> Fraction:
    if not infos:
        raise SourceProbeError("source_fps_unreliable")
    reference = infos[0].fps
    if any(not _rates_compatible(reference, item.fps) for item in infos[1:]):
        raise SourceProbeError("source_fps_unreliable")
    return reference


def build_cadence_probe_argv(
    ffprobe_executable: str, manifest_path: str | Path
) -> tuple[str, ...]:
    return (
        ffprobe_executable,
        "-v",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-protocol_whitelist",
        "file",
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+1",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "json",
        str(manifest_path),
    )


def confirm_high_fps_cadence(payload: bytes, declared_fps: Fraction) -> None:
    try:
        document = _decode_json(payload)
        frames = document["frames"]
        if not isinstance(frames, list) or len(frames) < 8:
            raise ValueError("too few frames")
        timestamps = tuple(
            Fraction(frame["best_effort_timestamp_time"])
            for frame in frames
            if isinstance(frame, dict)
            and "best_effort_timestamp_time" in frame
        )
        if len(timestamps) != len(frames):
            raise ValueError("frame timestamps are incomplete")
        intervals = tuple(
            right - left for left, right in zip(timestamps, timestamps[1:])
        )
        if any(interval <= 0 for interval in intervals):
            raise ValueError("frame timestamps are not increasing")
        observed = Fraction(len(intervals), 1) / (timestamps[-1] - timestamps[0])
        if not _rates_compatible(observed, declared_fps):
            raise ValueError("observed cadence differs")
        expected_interval = Fraction(1, 1) / declared_fps
        interval_tolerance = max(0.002, float(expected_interval) * 0.25)
        if any(
            abs(float(interval - expected_interval)) > interval_tolerance
            for interval in intervals
        ):
            raise ValueError("frame cadence varies")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise SourceProbeError("source_fps_unreliable") from exc


async def probe_source(
    ffprobe_executable: str,
    manifest_path: Path,
    *,
    runner: Any,
    timeout: float,
) -> SourceVideoInfo:
    result = await _bounded_command(
        runner,
        build_source_probe_argv(ffprobe_executable, manifest_path),
        timeout=timeout,
    )
    if result is None or result[0] != 0:
        raise SourceProbeError("source_probe_failed")
    info = parse_source_probe(result[1])
    if info.fps > 60:
        cadence = await _bounded_command(
            runner,
            build_cadence_probe_argv(ffprobe_executable, manifest_path),
            timeout=timeout,
        )
        if cadence is None or cadence[0] != 0:
            raise SourceProbeError("source_probe_failed")
        confirm_high_fps_cadence(cadence[1], info.fps)
    return info


def build_output_probe_argv(
    ffprobe_executable: str, output_path: Path
) -> tuple[str, ...]:
    entries = (
        "stream=index,codec_type,codec_name,width,height,pix_fmt,"
        "sample_aspect_ratio,avg_frame_rate,r_frame_rate:format=duration,size"
    )
    return (
        ffprobe_executable,
        "-v",
        "error",
        "-show_entries",
        entries,
        "-of",
        "json",
        str(output_path),
    )


async def probe_output(
    ffprobe_executable: str,
    output_path: Path,
    *,
    runner: Any,
    timeout: float,
) -> bytes:
    result = await _bounded_command(
        runner,
        build_output_probe_argv(ffprobe_executable, output_path),
        timeout=timeout,
    )
    if result is None or result[0] != 0:
        raise OutputProbeError("invalid_output")
    return result[1]


def validate_output_probe(
    payload: bytes,
    *,
    expected_duration: float,
    source_fps: Fraction,
    config: RenderConfig,
    actual_size: int,
    window_count: int = 1,
) -> RenderedPreviewMetadata:
    try:
        document = _decode_json(payload)
        streams = document["streams"]
        details = document["format"]
        if not isinstance(streams, list) or not isinstance(details, dict):
            raise ValueError("invalid output structure")
        videos = [x for x in streams if isinstance(x, dict) and x.get("codec_type") == "video"]
        non_video = [x for x in streams if not isinstance(x, dict) or x.get("codec_type") != "video"]
        if len(videos) != 1 or non_video:
            raise ValueError("output stream set is invalid")
        video = videos[0]
        average = _positive_fraction(video.get("avg_frame_rate"))
        nominal = _positive_fraction(video.get("r_frame_rate"))
        if not _rates_compatible(average, nominal):
            raise ValueError("output cadence is unreliable")
        duration = float(details["duration"])
        reported_size = int(details["size"])
        if (
            video.get("codec_name") != "h264"
            or video.get("width") != config.width
            or video.get("height") != config.height
            or video.get("pix_fmt") != "yuv420p"
            or video.get("sample_aspect_ratio") not in {"1:1", "1/1"}
        ):
            raise ValueError("output video contract failed")
        if (
            not math.isfinite(duration)
            or duration <= 0
            or type(actual_size) is not int
            or actual_size <= 0
            or reported_size != actual_size
            or actual_size > config.max_output_bytes
        ):
            raise ValueError("output size or duration is invalid")
        duration_tolerance = 0.05 + 2 * window_count / float(source_fps)
        if abs(duration - expected_duration) > duration_tolerance:
            raise ValueError("output duration differs")
        target_fps = min(source_fps, Fraction(60, 1))
        fps_tolerance = max(0.05, float(target_fps) * 0.005)
        if abs(float(average - target_fps)) > fps_tolerance:
            raise ValueError("output cadence differs")
        if source_fps <= 60 and float(average - source_fps) > _rate_tolerance(source_fps):
            raise ValueError("output cadence was upsampled")
        if average > Fraction(60, 1) + Fraction(1, 20):
            raise ValueError("output cadence exceeds cap")
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        raise OutputProbeError("invalid_output") from exc
    return RenderedPreviewMetadata(
        duration_seconds=duration,
        width=config.width,
        height=config.height,
        fps=average,
        size_bytes=actual_size,
    )
