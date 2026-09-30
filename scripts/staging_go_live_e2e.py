"""Guarded testbot go-live, live update and cleanup using disposable SQLite.

Run only inside the pinned Railway staging service. The outgoing synthetic post
is deleted after all three durable queue acknowledgements have been verified.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Mapping

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.database import Database
from bot.notification_queue import NotificationQueue
from bot.notification_worker import NotificationWorker
from bot.poller import StreamPoller


TARGET = Path(__file__).with_name("staging_target.json")
CONFIRMED_OWNER_ID = 425785231
EXPECTED_BOT_USERNAME = "TwitchSignalTestbot"


def validate_runtime(environ: Mapping[str, str]) -> int:
    target = json.loads(TARGET.read_text(encoding="utf-8"))
    if (
        environ.get("RAILWAY_ENVIRONMENT_NAME") != "staging"
        or environ.get("RAILWAY_PROJECT_ID") != target["project_id"]
        or environ.get("RAILWAY_ENVIRONMENT_ID") != target["staging_environment_id"]
        or environ.get("RAILWAY_SERVICE_ID") != target["service_id"]
        or environ.get("NOTIFICATION_QUEUE_ENABLED") != "1"
        or environ.get("OWNER_CHAT_ID") != str(CONFIRMED_OWNER_ID)
    ):
        raise ValueError("Pinned staging runtime or confirmed owner mismatch")
    return CONFIRMED_OWNER_ID


async def run_smoke(bot, owner_id: int) -> dict[str, object]:
    if owner_id != CONFIRMED_OWNER_ID:
        raise ValueError("Unconfirmed owner ID")
    identity = await bot.get_me()
    if identity.username != EXPECTED_BOT_USERNAME:
        raise ValueError("Unexpected bot identity")

    message_id: int | None = None
    deleted = False
    with tempfile.TemporaryDirectory(prefix="twitchsignal-staging-e2e-") as directory:
        db = Database(str(Path(directory) / "smoke.db"))
        await db.connect()
        try:
            login = "twitchsignal_stage_smoke"
            stream_id = "synthetic-r3-e2e"
            now = time.time()
            await db.add_channel(owner_id, login)
            await db.set_live_state(
                owner_id, login, True, stream_id, None,
                "[STAGING E2E] Проверка очереди live-поста",
                stream_started_at="2026-09-30T00:00:00Z",
                last_seen_live_at=now,
                queued_go_live=True,
            )
            await db.record_stream_observation(
                login, stream_id, now + 0.001, 42,
                "[STAGING E2E] Проверка очереди live-поста", "Тест",
                [owner_id],
            )
            poller = StreamPoller(
                bot, db, SimpleNamespace(), 60,
                notification_queue_enabled=True,
            )
            queue = NotificationQueue(db)
            worker = NotificationWorker(
                queue, poller.send_queued_job,
                max_concurrency=1, per_chat_interval=0,
                group_chat_interval=0, global_interval=0,
            )
            processed = await worker.run_once()
            cursor = await db.conn.execute(
                "SELECT status, attempt_count, last_error_class FROM notification_jobs "
                "WHERE kind = 'go_live' AND chat_id = ? AND twitch_login = ?",
                (owner_id, login),
            )
            row = await cursor.fetchone()
            state = await db.get_live_post_state(owner_id, login)
            message_id = state.message_id if state is not None else None
            if processed != 1 or row is None or row[0] != "done" or message_id is None:
                reason = row[2] if row is not None else "missing_job"
                raise RuntimeError(f"Staging go-live job did not complete: {reason}")
            await db.record_stream_observation(
                login, stream_id, now + 0.002, 99,
                "[STAGING E2E] Проверка обновления live-поста", "Тест",
                [owner_id],
            )
            await queue.request_live_updates(
                [(owner_id, login, stream_id, message_id, None)],
                now=time.time(),
            )
            update_processed = await worker.run_once()
            cursor = await db.conn.execute(
                "SELECT status, last_error_class FROM notification_jobs "
                "WHERE kind = 'live_update' AND chat_id = ? AND twitch_login = ? "
                "AND payload_version = ?",
                (owner_id, login, message_id),
            )
            update_row = await cursor.fetchone()
            if update_processed != 1 or update_row is None or update_row[0] != "done":
                reason = update_row[1] if update_row is not None else "missing_job"
                raise RuntimeError(f"Staging live update job did not complete: {reason}")
            await db.set_live_state(
                owner_id, login, False, stream_id, message_id,
                "[STAGING E2E] Проверка очереди live-поста",
                offline_since=time.time() - 400,
            )
            await poller._cleanup_offline_posts()
            offline_processed = await worker.run_once()
            cursor = await db.conn.execute(
                "SELECT status, last_error_class FROM notification_jobs "
                "WHERE kind = 'offline_cleanup' AND chat_id = ? AND twitch_login = ? "
                "AND payload_version = ?",
                (owner_id, login, message_id),
            )
            offline_row = await cursor.fetchone()
            post_ended = await db.get_live_post_ended(owner_id, login)
            if offline_processed != 1 or offline_row is None or offline_row[0] != "done" or not post_ended:
                reason = offline_row[1] if offline_row is not None else "missing_job"
                raise RuntimeError(f"Staging offline cleanup job did not complete: {reason}")
            await bot.delete_message(owner_id, message_id)
            deleted = True
            return {
                "bot_username": identity.username,
                "job_status": row[0],
                "attempt_count": row[1],
                "update_job_status": update_row[0],
                "offline_job_status": offline_row[0],
                "post_ended": post_ended,
                "message_deleted": deleted,
            }
        finally:
            if message_id is not None and not deleted:
                try:
                    await bot.delete_message(owner_id, message_id)
                except Exception:
                    pass
            await db.close()


async def _main() -> int:
    try:
        owner_id = validate_runtime(os.environ)
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            result = await run_smoke(bot, owner_id)
        finally:
            await bot.session.close()
    except Exception as error:
        print(f"staging_go_live_e2e_failed={type(error).__name__}", file=sys.stderr)
        return 1
    print("bot_username=" + str(result["bot_username"]))
    print("job_status=" + str(result["job_status"]))
    print("attempt_count=" + str(result["attempt_count"]))
    print("update_job_status=" + str(result["update_job_status"]))
    print("offline_job_status=" + str(result["offline_job_status"]))
    print("post_ended=" + str(result["post_ended"]))
    print("message_deleted=" + str(result["message_deleted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
