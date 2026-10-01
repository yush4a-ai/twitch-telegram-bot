"""Synthetic preview coordinator and bounded local FFmpeg encoding probe."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from scripts.load_harness import run_preview_profile


async def run_preview_probe(
    duration_seconds: int = 6, *, coordinator_limits: tuple[int, ...] = (1, 2, 4),
) -> dict[str, object]:
    if (
        not isinstance(duration_seconds, int) or isinstance(duration_seconds, bool)
        or not 1 <= duration_seconds <= 24
        or not coordinator_limits or len(set(coordinator_limits)) != len(coordinator_limits)
        or any(type(limit) is not int or limit not in {1, 2, 4} for limit in coordinator_limits)
    ):
        raise ValueError("Unsupported synthetic preview profile")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe are required for R9 preview probe")

    coordinator = []
    for limit in coordinator_limits:
        result = await run_preview_profile(limit)
        if result["completed_previews"] != 4 or result["max_active_captures"] > limit:
            raise RuntimeError("Synthetic preview coordinator invariant failed")
        coordinator.append(result)

    with tempfile.TemporaryDirectory(prefix="twitchsignal-r9-preview-") as directory:
        output = Path(directory) / "synthetic-854x480.mp4"
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-benchmark",
            "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=30",
            "-t", str(duration_seconds), "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-threads", "1", "-movflags", "+faststart", "-n", str(output),
        ]
        started = time.perf_counter()
        rendered = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, text=True,
            timeout=120, check=False,
        )
        render_seconds = time.perf_counter() - started
        if rendered.returncode != 0:
            raise RuntimeError(f"Synthetic FFmpeg probe failed with code {rendered.returncode}")
        probed = await asyncio.to_thread(
            subprocess.run,
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
            capture_output=True, text=True, timeout=15, check=True,
        )
        info = json.loads(probed.stdout)
        streams = info["streams"]
        video_streams = [stream for stream in streams if stream["codec_type"] == "video"]
        audio_streams = [stream for stream in streams if stream["codec_type"] == "audio"]
        if (
            len(video_streams) != 1 or audio_streams
            or video_streams[0]["codec_name"] != "h264"
            or (video_streams[0]["width"], video_streams[0]["height"]) != (854, 480)
            or video_streams[0]["r_frame_rate"] != "30/1"
            or abs(float(info["format"]["duration"]) - duration_seconds) > 0.1
            or output.stat().st_size > 10 * 1024 * 1024
        ):
            raise RuntimeError("Synthetic FFmpeg output invariant failed")
        bench = rendered.stderr
        cpu_match = re.search(r"utime=([0-9.]+)s stime=([0-9.]+)s", bench)
        rss_match = re.search(r"maxrss=([0-9]+)kB", bench)
        video = {
            "width": 854,
            "height": 480,
            "codec": "h264",
            "fps": 30,
            "duration_seconds": float(info["format"]["duration"]),
            "audio_streams": 0,
            "bytes": output.stat().st_size,
            "encode_wall_seconds": round(render_seconds, 3),
            "encode_cpu_seconds": (
                round(float(cpu_match[1]) + float(cpu_match[2]), 3)
                if cpu_match else None
            ),
            "encoder_maxrss_bytes": int(rss_match[1]) * 1024 if rss_match else None,
        }
        return {
            "synthetic": True,
            "network_requests": 0,
            "coordinator": coordinator,
            "video": video,
        }
