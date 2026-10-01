"""Eight synthetic Streamer Plus journeys on a disposable staging database."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.billing import BillingService
from bot.billing_provider import MockPaymentProvider, VerifiedPaymentEvent
from bot.database import Database
from bot.live_post import LivePostContent
from bot.streamer_post import compose_streamer_post
from scripts.staging_mock_billing import verify_staging_runtime


STREAMER_COUNT = 8


def _signed_event(
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


async def run_simulation(
    environ: Mapping[str, str], *, temp_root: Path | None = None,
) -> dict[str, int | str]:
    verify_staging_runtime(environ)
    root = (Path(tempfile.gettempdir()) if temp_root is None else Path(temp_root)).resolve()
    active_db = Path(environ["DB_PATH"]).resolve()
    volume = Path(environ["RAILWAY_VOLUME_MOUNT_PATH"]).resolve()
    if not root.is_dir() or root.is_relative_to(volume) or active_db.is_relative_to(root):
        raise PermissionError("pilot DB must be outside the active Volume")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="twitchsignal-r6-", dir=root) as directory:
        db_path = Path(directory) / "pilot.db"
        db = Database(str(db_path))
        await db.connect()
        try:
            provider = MockPaymentProvider(secrets.token_bytes(32))
            billing = BillingService(db, provider)
            identities: list[tuple[int, str, str, int, str, str]] = []
            for index in range(STREAMER_COUNT):
                telegram_id = 999001000 + index
                broadcaster_id = str(999002000 + index)
                login = f"synthetic_pilot_{index}"
                chat_id = -999003000 - index
                if not await db.link_streamer_identity(
                    telegram_id, broadcaster_id, login, verified_at=900,
                ):
                    raise AssertionError("synthetic identity was not linked")
                if not await db.add_channel(chat_id, login):
                    raise AssertionError("synthetic destination was not created")
                checkout = await billing.create_checkout(
                    telegram_id, f"r6-order-{index}", duration_seconds=3600, now=1000,
                )
                body, headers = _signed_event(
                    provider, checkout.order_id, f"r6-capture-{index}",
                    f"r6-payment-{index}", "captured", 1010,
                )
                if await billing.handle_webhook(body, headers, now=1010) != "paid":
                    raise AssertionError("synthetic checkout did not capture")
                if not await db.has_streamer_plus(telegram_id, now=1011):
                    raise AssertionError("synthetic Plus is absent")
                identities.append((telegram_id, broadcaster_id, login, chat_id,
                                   checkout.order_id, f"r6-payment-{index}"))

            expected_denials = 0
            for index, (telegram_id, broadcaster_id, login, chat_id, _order_id, _payment_id) in enumerate(identities):
                if not await db.add_streamer_community(
                    telegram_id, chat_id, f"Synthetic group {index}", "supergroup", now=1012,
                ):
                    raise AssertionError("synthetic community was not linked")
                foreign_id = identities[(index + 1) % STREAMER_COUNT][0]
                if await db.save_streamer_template(
                    foreign_id, chat_id, expected_version=0,
                    headline="Foreign", body="Denied", buttons=[], now=1013,
                ) is not None:
                    raise AssertionError("another streamer changed a template")
                expected_denials += 1
                if await db.save_streamer_template(
                    telegram_id, chat_id, expected_version=0,
                    headline="<Pilot & live>", body="Synthetic post",
                    buttons=[{"label": "Site", "url": "https://example.com/pilot"}],
                    now=1013,
                ) != 1:
                    raise AssertionError("synthetic template was not saved")
                stream_id = f"pilot-stream-{index}"
                wrong_broadcaster = identities[(index + 1) % STREAMER_COUNT][1]
                await db.set_live_state(
                    chat_id, login, True, stream_id, broadcaster_id=wrong_broadcaster,
                )
                if await db.get_active_streamer_template_for_destination(
                    chat_id, login, now=1014,
                ) is not None:
                    raise AssertionError("wrong broadcaster selected a template")
                expected_denials += 1
                await db.set_live_state(
                    chat_id, login, True, stream_id, broadcaster_id=broadcaster_id,
                )
                template = await db.get_active_streamer_template_for_destination(
                    chat_id, login, now=1014,
                )
                if template is None:
                    raise AssertionError("current broadcaster template is missing")
                base = LivePostContent(
                    html=f"<b>{login}</b> live",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="Twitch", url=f"https://twitch.tv/{login}"),
                    ]]),
                )
                composed = compose_streamer_post(base, template)
                if (
                    "&lt;Pilot &amp; live&gt;" not in composed.html
                    or composed.reply_markup.inline_keyboard[0][0].text != "Twitch"
                    or len(composed.reply_markup.inline_keyboard) != 2
                ):
                    raise AssertionError("synthetic live post composition changed")
                if not await db.set_live_message_if_current(
                    chat_id, login, stream_id, 80000 + index,
                ):
                    raise AssertionError("synthetic post was not recorded")
                if await db.set_live_message_if_current(
                    chat_id, login, stream_id, 90000 + index,
                ):
                    raise AssertionError("duplicate synthetic post was recorded")

            for telegram_id, _broadcaster_id, _login, _chat_id, _order_id, _payment_id in identities:
                stats = await db.get_streamer_delivery_stats(telegram_id, since=0)
                if stats["connected_communities"] != 1 or stats["published_posts"] != 1:
                    raise AssertionError("synthetic analytics are not owner-scoped")

            cancelled = await billing.create_checkout(
                identities[0][0], "r6-cancelled", duration_seconds=3600, now=1050,
            )
            if not await billing.cancel_order(identities[0][0], cancelled.order_id, now=1051):
                raise AssertionError("synthetic pending checkout did not cancel")

            refunds = 0
            for index in range(2):
                telegram_id, _broadcaster_id, login, chat_id, order_id, payment_id = identities[index]
                await billing.request_refund(telegram_id, order_id, f"r6-refund-{index}", now=1100)
                body, headers = _signed_event(
                    provider, order_id, f"r6-refunded-{index}", payment_id, "refunded", 1110,
                )
                if await billing.handle_webhook(body, headers, now=1110) != "refunded":
                    raise AssertionError("synthetic refund did not settle")
                if await db.has_streamer_plus(telegram_id, now=1111):
                    raise AssertionError("refunded streamer retained Plus")
                if await db.get_active_streamer_template_for_destination(
                    chat_id, login, now=1111,
                ) is not None:
                    raise AssertionError("refunded streamer retained active template")
                refunds += 1

            expired = 0
            for telegram_id, _broadcaster_id, login, chat_id, _order_id, _payment_id in identities[2:]:
                if not await db.has_streamer_plus(telegram_id, now=1111):
                    raise AssertionError("active synthetic Plus disappeared early")
                if await db.has_streamer_plus(telegram_id, now=4610):
                    raise AssertionError("synthetic Plus did not expire")
                if await db.get_active_streamer_template_for_destination(
                    chat_id, login, now=4610,
                ) is not None:
                    raise AssertionError("expired streamer retained active template")
                expired += 1

            for telegram_id, _broadcaster_id, _login, _chat_id, _order_id, _payment_id in identities:
                historical = await db.get_streamer_delivery_stats(telegram_id, since=0)
                if historical["connected_communities"] != 1 or historical["published_posts"] != 1:
                    raise AssertionError("historical analytics changed after Plus ended")

            cursor = await db.conn.execute("SELECT COUNT(*) FROM streamer_post_templates")
            templates = (await cursor.fetchone())[0]
            cursor = await db.conn.execute("SELECT COUNT(*) FROM streamer_post_events")
            published_events = (await cursor.fetchone())[0]
            cursor = await db.conn.execute("PRAGMA integrity_check")
            integrity = (await cursor.fetchone())[0]
            community_counts = [
                len(await db.list_streamer_communities(identity[0]))
                for identity in identities
            ]
            report: dict[str, int | str] = {
                "streamers": STREAMER_COUNT,
                "communities": sum(community_counts),
                "templates": templates,
                "published_events": published_events,
                "refunds": refunds,
                "expired_grants": expired,
                "cancelled_orders": int((await db.get_billing_order(cancelled.order_id)).status == "cancelled"),
                "expected_denials": expected_denials,
                "unexpected_errors": 0,
                "telegram_calls": 0,
                "twitch_calls": 0,
                "payment_network_calls": 0,
                "external_cost_units": 0,
                "integrity": integrity,
            }
            expected = {
                "streamers": 8, "communities": 8, "templates": 8,
                "published_events": 8, "refunds": 2, "expired_grants": 6,
                "cancelled_orders": 1, "expected_denials": 16,
                "unexpected_errors": 0, "telegram_calls": 0,
                "twitch_calls": 0, "payment_network_calls": 0,
                "external_cost_units": 0, "integrity": "ok",
            }
            if report != expected:
                raise AssertionError("synthetic pilot counters differ from expected")
        finally:
            await db.close()
        report["db_bytes"] = db_path.stat().st_size
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return report


def main() -> int:
    try:
        report = asyncio.run(run_simulation(os.environ))
    except (AssertionError, PermissionError, ValueError, OSError, sqlite3.Error) as error:
        print(f"R6 staging simulation stopped: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
