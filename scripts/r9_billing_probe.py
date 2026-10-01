"""Network-free mock billing replay probe on an isolated in-memory DB."""

from __future__ import annotations

import asyncio

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider, PaymentVerificationError, VerifiedPaymentEvent
from bot.database import Database


async def run_billing_probe(replays: int = 100) -> dict[str, object]:
    if not isinstance(replays, int) or isinstance(replays, bool) or not 1 <= replays <= 1000:
        raise ValueError("R9 mock replay count must be 1..1000")
    database = Database(":memory:")
    await database.connect()
    try:
        await database.link_streamer_identity(101, "9001", "synthetic_r9", verified_at=1)
        provider = MockPaymentProvider(b"r9-synthetic-mock-secret-only-32-bytes")
        billing = BillingService(database, provider)
        checkout = await billing.create_checkout(101, "r9-capture", 3600, now=100)
        capture = VerifiedPaymentEvent(
            "mock", "r9-capture-event", checkout.order_id,
            "r9-payment", "captured", 1, "TEST",
        )
        body, headers = provider.sign_test_event(capture, now=110)
        statuses = await asyncio.gather(*(
            billing.handle_webhook(body, headers, now=110)
            for _ in range(replays)
        ))
        if statuses != ["paid"] * replays:
            raise RuntimeError("Concurrent mock capture replay changed state")
        bad_signature_rejected = False
        try:
            await billing.handle_webhook(body + b" ", headers, now=111)
        except PaymentVerificationError:
            bad_signature_rejected = True
        if not bad_signature_rejected:
            raise RuntimeError("Mock webhook signature was not enforced")

        await billing.request_refund(101, checkout.order_id, "r9-refund-request", now=121)
        refund = VerifiedPaymentEvent(
            "mock", "r9-refund-event", checkout.order_id,
            "r9-payment", "refunded", 1, "TEST",
        )
        refund_body, refund_headers = provider.sign_test_event(refund, now=122)
        if await billing.handle_webhook(refund_body, refund_headers, now=122) != "refunded":
            raise RuntimeError("Mock refund was not applied")
        if await billing.handle_webhook(refund_body, refund_headers, now=123) != "refunded":
            raise RuntimeError("Mock refund replay changed state")

        cancelled = await billing.create_checkout(101, "r9-cancel", 3600, now=100)
        if not await billing.cancel_order(101, cancelled.order_id, now=120):
            raise RuntimeError("Mock cancellation failed")
        await billing.create_checkout(101, "r9-expire", 3600, now=100)
        if await billing.expire_pending(now=1000) != 1:
            raise RuntimeError("Mock pending order did not expire")

        async def count(sql: str) -> int:
            return int((await (await database.conn.execute(sql)).fetchone())[0])

        result = {
            "synthetic": True,
            "provider": "mock",
            "network_requests": 0,
            "capture_replays": replays,
            "payments": await count("SELECT COUNT(*) FROM billing_payments"),
            "grants": await count("SELECT COUNT(*) FROM entitlement_grants WHERE source='mock'"),
            "webhook_events": await count("SELECT COUNT(*) FROM billing_webhook_events"),
            "active_mock_grants_after_refund": await count(
                "SELECT COUNT(*) FROM entitlement_grants WHERE source='mock' AND revoked_at IS NULL"
            ),
            "bad_signature_rejected": bad_signature_rejected,
            "cancelled_orders": await count("SELECT COUNT(*) FROM billing_orders WHERE status='cancelled'"),
            "expired_orders": await count("SELECT COUNT(*) FROM billing_orders WHERE status='expired'"),
            "integrity": (await (await database.conn.execute("PRAGMA integrity_check")).fetchone())[0],
        }
        if (
            result["payments"] != 1 or result["grants"] != 1
            or result["webhook_events"] != 2
            or result["active_mock_grants_after_refund"] != 0
            or result["cancelled_orders"] != 1 or result["expired_orders"] != 1
            or result["integrity"] != "ok"
        ):
            raise RuntimeError("Mock billing idempotency invariant failed")
        return result
    finally:
        await database.close()
