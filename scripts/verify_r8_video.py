"""Validate the checked-in synthetic R8 videos without rendering or network calls."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path


ASSETS = Path(__file__).resolve().parent.parent / "bot" / "growth_ui"
EXPECTED = {
    "demo-landscape.mp4": (1280, 720),
    "demo-portrait.mp4": (720, 1280),
}


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def verify() -> list[dict[str, object]]:
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe is required for the local R8 video check")
    results: list[dict[str, object]] = []
    for name, (width, height) in EXPECTED.items():
        path = ASSETS / name
        info = _probe(path)
        streams = info["streams"]
        assert len(streams) == 1 and streams[0]["codec_type"] == "video", name
        video = streams[0]
        assert video["codec_name"] == "h264" and video["pix_fmt"] in {"yuv420p", "yuvj420p"}, name
        assert (video["width"], video["height"]) == (width, height), name
        assert video["r_frame_rate"] == "30/1", name
        assert abs(float(info["format"]["duration"]) - 8.0) < 0.02, name
        assert path.stat().st_size <= 1_000_000, name
        results.append({"asset": name, "bytes": path.stat().st_size, "duration": float(info["format"]["duration"])})

    poster = ASSETS / "demo-poster.png"
    with poster.open("rb") as image:
        header = image.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", poster.name
    assert struct.unpack(">II", header[16:24]) == (1280, 720), poster.name
    assert poster.stat().st_size <= 200_000, poster.name
    results.append({"asset": poster.name, "bytes": poster.stat().st_size})
    return results


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
