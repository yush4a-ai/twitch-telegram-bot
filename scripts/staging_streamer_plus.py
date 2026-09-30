"""Issue or revoke only test Streamer Plus grants on the pinned staging service.

Invoke through railway ssh with explicit staging project/environment/service IDs.
No payment, provider, or production data is involved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from bot.database import Database, DatabaseConfigurationError

TARGET = Path(__file__).with_name("staging_target.json")
EXPECTED_OWNER_ID = 425785231


def verify_staging_runtime(environ: Mapping[str, str]) -> int:
    try:
        target = json.loads(TARGET.read_text(encoding="utf-8"))
        expected = {
            "RAILWAY_PROJECT_ID": target["project_id"],
            "RAILWAY_ENVIRONMENT_ID": target["staging_environment_id"],
            "RAILWAY_SERVICE_ID": target["service_id"],
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "DB_PATH": "/data/bot.db",
        }
        if any(not value or environ.get(key) != value for key, value in expected.items()):
            raise PermissionError("not the pinned Railway staging runtime")
        owner = environ.get("OWNER_CHAT_ID", "")
        if (
            not isinstance(owner, str) or not owner.isascii()
            or not owner.isdecimal() or int(owner) != EXPECTED_OWNER_ID
        ):
            raise PermissionError("owner ID is unavailable")
        return int(owner)
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise PermissionError("staging target cannot be verified") from error


async def apply_grant(
    db: Database, *, broadcaster_id: str, request_key: str,
    starts_at: float, expires_at: float, issued_by: int,
    now: float | None = None,
) -> str:
    return await db.issue_test_streamer_plus(
        broadcaster_id, request_key, starts_at=starts_at,
        expires_at=expires_at, issued_by=issued_by, now=now,
    )


async def apply_revoke(
    db: Database, *, grant_id: str, issued_by: int, now: float | None = None,
) -> bool:
    return await db.revoke_test_streamer_plus(
        grant_id, revoked_at=time.time() if now is None else now,
        issued_by=issued_by,
    )


async def _run(args: argparse.Namespace, owner_id: int) -> int:
    db = Database(os.environ["DB_PATH"], token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY"))
    await db.connect()
    try:
        if args.action == "grant":
            grant_id = await apply_grant(
                db, broadcaster_id=args.broadcaster_id,
                request_key=args.request_key, starts_at=args.starts_at,
                expires_at=args.expires_at, issued_by=owner_id,
            )
            print(f"test_grant_id={grant_id}")
        else:
            changed = await apply_revoke(db, grant_id=args.grant_id, issued_by=owner_id)
            print(f"test_revoke_changed={str(changed).lower()}")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pinned staging test-only Streamer Plus grant")
    actions = parser.add_subparsers(dest="action", required=True)
    grant = actions.add_parser("grant")
    grant.add_argument("--broadcaster-id", required=True)
    grant.add_argument("--request-key", required=True)
    grant.add_argument("--starts-at", type=float, required=True)
    grant.add_argument("--expires-at", type=float, required=True)
    revoke = actions.add_parser("revoke")
    revoke.add_argument("--grant-id", required=True)
    args = parser.parse_args(argv)
    try:
        owner_id = verify_staging_runtime(os.environ)
        return asyncio.run(_run(args, owner_id))
    except (PermissionError, ValueError, OSError, sqlite3.Error, DatabaseConfigurationError) as error:
        print(f"Test Plus operation stopped: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
