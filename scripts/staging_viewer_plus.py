"""Owner-only test Viewer Plus grant/revoke on the pinned staging runtime."""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import time

from bot.database import Database, DatabaseConfigurationError
from scripts.staging_mock_billing import verify_staging_runtime


CONFIRMED_OWNER_ID = 425785231


async def apply_grant(
    db: Database, *, telegram_user_id: int, request_key: str,
    starts_at: float, expires_at: float, now: float | None = None,
) -> str:
    return await db.issue_test_viewer_plus(
        telegram_user_id, request_key, starts_at=starts_at,
        expires_at=expires_at, issued_by=CONFIRMED_OWNER_ID, now=now,
    )


async def apply_revoke(
    db: Database, *, grant_id: str, now: float | None = None,
) -> bool:
    return await db.revoke_test_viewer_plus(
        grant_id, revoked_at=time.time() if now is None else now,
        issued_by=CONFIRMED_OWNER_ID,
    )


async def _run(args: argparse.Namespace) -> int:
    db = Database(
        os.environ["DB_PATH"], token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY"),
    )
    await db.connect()
    try:
        if args.action == "grant":
            grant_id = await apply_grant(
                db, telegram_user_id=args.telegram_user_id,
                request_key=args.request_key, starts_at=args.starts_at,
                expires_at=args.expires_at,
            )
            print(f"test_viewer_grant_id={grant_id}")
        else:
            changed = await apply_revoke(db, grant_id=args.grant_id)
            print(f"test_viewer_revoke_changed={str(changed).lower()}")
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pinned staging test Viewer Plus grant")
    actions = parser.add_subparsers(dest="action", required=True)
    grant = actions.add_parser("grant")
    grant.add_argument("--telegram-user-id", type=int, required=True)
    grant.add_argument("--request-key", required=True)
    grant.add_argument("--starts-at", type=float, required=True)
    grant.add_argument("--expires-at", type=float, required=True)
    revoke = actions.add_parser("revoke")
    revoke.add_argument("--grant-id", required=True)
    args = parser.parse_args(argv)
    try:
        verify_staging_runtime(os.environ)
        return asyncio.run(_run(args))
    except (PermissionError, ValueError, OSError, sqlite3.Error, DatabaseConfigurationError) as error:
        print(f"Test Viewer Plus operation stopped: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
