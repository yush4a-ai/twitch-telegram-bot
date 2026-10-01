"""Closed R5 billing drill on a disposable DB in the pinned Railway staging container."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider, VerifiedPaymentEvent
from bot.database import Database


TARGET = Path(__file__).with_name("staging_target.json")
EXPECTED_OWNER_ID = "425785231"
SYNTHETIC_TELEGRAM_ID = 999000111
SYNTHETIC_BROADCASTER_ID = "999000111"


def verify_staging_runtime(environ: Mapping[str, str]) -> None:
    try:
        target = json.loads(TARGET.read_text(encoding="utf-8"))
        expected = {
            "RAILWAY_PROJECT_ID": target["project_id"],
            "RAILWAY_ENVIRONMENT_ID": target["staging_environment_id"],
            "RAILWAY_SERVICE_ID": target["service_id"],
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "DB_PATH": "/data/bot.db",
            "OWNER_CHAT_ID": EXPECTED_OWNER_ID,
        }
        if any(not value or environ.get(key) != value for key, value in expected.items()):
            raise PermissionError("not the pinned Railway staging runtime")
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise PermissionError("staging target cannot be verified") from error


def _event(
    provider: MockPaymentProvider, order_id: str, event_id: str,
    payment_id: str, event_type: str, at: float,
) -> tuple[bytes, dict[str, str]]:
    return provider.sign_test_event(
        VerifiedPaymentEvent(
            provider="mock", event_id=event_id, order_id=order_id,
            payment_id=payment_id, event_type=event_type, units=1, currency="TEST",
        ),
        now=at,
    )


async def run_drill(
    environ: Mapping[str, str], *, temp_root: Path | None = None,
) -> dict[str, str | int | bool]:
    verify_staging_runtime(environ)
    root = Path(tempfile.gettempdir()) if temp_root is None else Path(temp_root)
    root = root.resolve()
    active_db = Path(environ["DB_PATH"]).resolve()
    volume = Path(environ["RAILWAY_VOLUME_MOUNT_PATH"]).resolve()
    if (
        not root.is_dir() or root.is_relative_to(volume)
        or active_db.is_relative_to(root)
    ):
        raise PermissionError("disposable DB must be outside the active Volume")
    with tempfile.TemporaryDirectory(prefix="twitchsignal-r5-", dir=root) as directory:
        db = Database(str(Path(directory) / "drill.db"))
        await db.connect()
        try:
            await db.link_streamer_identity(
                SYNTHETIC_TELEGRAM_ID, SYNTHETIC_BROADCASTER_ID,
                "synthetic_r5", verified_at=900,
            )
            provider = MockPaymentProvider(secrets.token_bytes(32))
            service = BillingService(db, provider)

            paid = await service.create_checkout(
                SYNTHETIC_TELEGRAM_ID, "r5-paid", duration_seconds=60, now=1000,
            )
            body, headers = _event(provider, paid.order_id, "r5-capture", "r5-payment", "captured", 1010)
            if await service.handle_webhook(body, headers, now=1010) != "paid":
                raise AssertionError("mock capture did not pay")
            paid_observed = (await db.get_billing_order(paid.order_id)).status == "paid"
            if await service.handle_webhook(body, headers, now=1011) != "paid":
                raise AssertionError("mock capture replay changed status")
            if not await db.has_streamer_plus(SYNTHETIC_TELEGRAM_ID, now=1011):
                raise AssertionError("mock grant was not issued")
            await service.request_refund(
                SYNTHETIC_TELEGRAM_ID, paid.order_id, "r5-refund", now=1020,
            )
            if not await db.has_streamer_plus(SYNTHETIC_TELEGRAM_ID, now=1021):
                raise AssertionError("refund request revoked access early")
            refund, refund_headers = _event(
                provider, paid.order_id, "r5-refunded", "r5-payment", "refunded", 1030,
            )
            if await service.handle_webhook(refund, refund_headers, now=1030) != "refunded":
                raise AssertionError("mock refund did not settle")
            if await db.has_streamer_plus(SYNTHETIC_TELEGRAM_ID, now=1031):
                raise AssertionError("refund did not revoke mock grant")

            cancelled = await service.create_checkout(
                SYNTHETIC_TELEGRAM_ID, "r5-cancelled", duration_seconds=60, now=1100,
            )
            if not await service.cancel_order(SYNTHETIC_TELEGRAM_ID, cancelled.order_id, now=1101):
                raise AssertionError("mock cancellation did not close order")
            expired = await service.create_checkout(
                SYNTHETIC_TELEGRAM_ID, "r5-expired", duration_seconds=60, now=1200,
            )
            if await service.expire_pending(now=2100) != 1:
                raise AssertionError("mock checkout expiry did not close order")

            rollback = await service.create_checkout(
                SYNTHETIC_TELEGRAM_ID, "r5-rollback", duration_seconds=60, now=2200,
            )
            await db.conn.execute(
                "CREATE TEMP TRIGGER r5_fail_grant BEFORE INSERT ON entitlement_events "
                "BEGIN SELECT RAISE(ABORT, 'r5 drill rollback'); END"
            )
            broken, broken_headers = _event(
                provider, rollback.order_id, "r5-rollback-event", "r5-rollback-payment", "captured", 2210,
            )
            try:
                await service.handle_webhook(broken, broken_headers, now=2210)
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("injected DB failure was not raised")
            cursor = await db.conn.execute("SELECT COUNT(*) FROM billing_payments")
            if (await cursor.fetchone())[0] != 1:
                raise AssertionError("failed capture left a payment")
            cursor = await db.conn.execute("SELECT COUNT(*) FROM billing_webhook_events")
            if (await cursor.fetchone())[0] != 2:
                raise AssertionError("failed capture left a webhook event")
            if (await db.get_billing_order(rollback.order_id)).status != "pending":
                raise AssertionError("failed capture changed order state")

            cursor = await db.conn.execute(
                "SELECT status,COUNT(*) FROM billing_orders GROUP BY status"
            )
            counts = dict(await cursor.fetchall())
            cursor = await db.conn.execute(
                "SELECT COUNT(*) FROM entitlement_grants WHERE source='mock'"
            )
            mock_grants = (await cursor.fetchone())[0]
            cursor = await db.conn.execute("PRAGMA integrity_check")
            integrity = (await cursor.fetchone())[0]
            report: dict[str, str | int | bool] = {
                "integrity": integrity,
                "paid": int(paid_observed),
                "refunded": counts.get("refunded", 0),
                "cancelled": counts.get("cancelled", 0),
                "expired": counts.get("expired", 0),
                "rollback_verified": True,
                "mock_grants": mock_grants,
            }
            if report != {
                "integrity": "ok", "paid": 1, "refunded": 1,
                "cancelled": 1, "expired": 1,
                "rollback_verified": True, "mock_grants": 1,
            }:
                raise AssertionError("mock billing drill result differs from expected")
            return report
        finally:
            await db.close()


def main() -> int:
    try:
        report = asyncio.run(run_drill(os.environ))
    except (AssertionError, PermissionError, ValueError, OSError, sqlite3.Error) as error:
        print(f"R5 staging drill stopped: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
