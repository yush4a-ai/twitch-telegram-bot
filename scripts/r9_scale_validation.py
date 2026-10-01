"""CLI and fail-closed Railway guard for isolated R9 scale profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
from typing import Mapping

from scripts.r9_mixed_load import CapacityLimit, run_mixed_profile


TARGET = Path(__file__).with_name("staging_target.json")


def validate_r9_runtime(environ: Mapping[str, str]) -> None:
    target = json.loads(TARGET.read_text(encoding="utf-8"))
    if (
        environ.get("RAILWAY_ENVIRONMENT_NAME") != "staging"
        or environ.get("RAILWAY_PROJECT_ID") != target["project_id"]
        or environ.get("RAILWAY_ENVIRONMENT_ID") != target["staging_environment_id"]
        or environ.get("RAILWAY_SERVICE_ID") != target["service_id"]
        or environ.get("NOTIFICATION_QUEUE_ENABLED") != "1"
        or environ.get("ADMIN_TELEGRAM_BOT_USERNAME") != "TwitchSignalTestbot"
    ):
        raise ValueError("Pinned staging R9 runtime mismatch")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R9 synthetic temp DB scale validation")
    parser.add_argument("--destinations", type=int, choices=(20_000, 30_000, 40_000), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--staging", action="store_true")
    args = parser.parse_args(argv)
    railway = bool(os.getenv("RAILWAY_ENVIRONMENT_NAME"))
    try:
        if railway or args.staging:
            validate_r9_runtime(os.environ)
        output = args.output.resolve()
        if output.exists() or not output.parent.is_dir():
            raise ValueError("R9 output must be a new file in an existing directory")
        if railway and not output.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError("Railway R9 output must be inside OS temp")
        profile = await run_mixed_profile(args.destinations)
        result = {
            "synthetic": True,
            "runtime": {
                "environment": "staging" if railway else "local",
                "python": sys.version.split()[0],
                "sqlite": sqlite3.sqlite_version,
                "os": platform.system(),
            },
            "profile": profile,
        }
        with output.open("x", encoding="utf-8") as target:
            json.dump(result, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (CapacityLimit, ValueError, OSError, RuntimeError) as error:
        print(f"r9_profile_stopped={type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
