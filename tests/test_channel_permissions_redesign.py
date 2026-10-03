"""Channel-only new intents, explicit permission outcomes, existing group continuity."""

import asyncio
import time
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiogram.enums import ChatType

from bot import streamer_community as permissions
from bot.database import Database
from bot.mini_app_streamer import complete_community_intent
from bot.mini_app_web import install_mini_app_routes
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


def fake_bot(*, chat_type="channel", user="administrator", member="administrator", post=True, edit=False):
    async def get_member(_chat_id, user_id):
        if user_id == 101:
            return SimpleNamespace(status=user)
        if member == "creator":
            return SimpleNamespace(status=member)
        return SimpleNamespace(status=member, can_post_messages=post, can_edit_messages=edit)
    return SimpleNamespace(
        id=999, get_chat=AsyncMock(return_value=SimpleNamespace(
            id=-1001, type=chat_type, title="Verified channel", username="verified_channel",
        )), get_chat_member=AsyncMock(side_effect=get_member),
        save_prepared_keyboard_button=AsyncMock(return_value=SimpleNamespace(id="prepared-channel")),
        send_message=AsyncMock(),
    )


class StructuredPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, bot, chat_id=-1001, user_id=101):
        checker = getattr(permissions, "check_community_permission", None)
        self.assertTrue(callable(checker), "structured permission checker is required")
        return await checker(bot, chat_id, user_id)

    async def test_post_only_and_creator_are_ready_without_foreign_edit_rights(self):
        for bot in (fake_bot(), fake_bot(member="creator"), fake_bot(user="creator")):
            with self.subTest(member=bot):
                result = await self.check(bot)
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.community.chat_id, -1001)
                self.assertEqual(result.public_url, "https://t.me/verified_channel")
                self.assertIsNotNone(await permissions.verify_community_permission(bot, -1001, 101))

    async def test_denial_reasons_and_missing_permission_fail_closed(self):
        for bot, status in ((fake_bot(member="left"), "bot_absent"),
                            (fake_bot(member="kicked"), "bot_absent"),
                            (fake_bot(member="member"), "bot_member"),
                            (fake_bot(post=False), "missing_post_right"),
                            (fake_bot(post=None), "missing_post_right"),
                            (fake_bot(post=1), "missing_post_right"),
                            (fake_bot(user="member"), "user_denied"),
                            (fake_bot(chat_type="private"), "wrong_chat_type")):
            with self.subTest(status=status):
                result = await self.check(bot)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.community)
                self.assertIsNone(result.public_url)

    async def test_network_and_cancellation_do_not_become_missing_rights(self):
        for error in (TimeoutError(), RuntimeError("temporary Telegram failure")):
            bot = fake_bot(); bot.get_chat.side_effect = error
            result = await self.check(bot)
            self.assertEqual(result.status, "network_error")
            self.assertIsNone(result.community)
        bot = fake_bot(); bot.get_chat.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.check(bot)

    async def test_verified_url_rejects_foreign_chat_and_unsafe_username(self):
        bot = fake_bot(); bot.get_chat.return_value.id = -2002
        self.assertEqual((await self.check(bot)).status, "network_error")
        for username in (None, "javascript:alert(1)", "verified/path", "@verified", "x" * 33):
            bot = fake_bot(); bot.get_chat.return_value.username = username
            result = await self.check(bot)
            self.assertEqual(result.status, "ready")
            self.assertIsNone(result.public_url)

    async def test_legacy_group_keeps_existing_permission_semantics(self):
        result = await self.check(fake_bot(chat_type="supergroup", post=False))
        self.assertEqual((result.status, result.community.chat_type), ("ready", "supergroup"))
        self.assertIsNone(result.public_url)
        for chat_id, user_id in ((101, 101), (-1001, True), (-1001, -1)):
            result = await self.check(fake_bot(), chat_id, user_id)
            self.assertIsNone(result.community)


class ChannelIntentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:"); await self.db.connect(); self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=time.time())
        self.bot = fake_bot()
        app = web.Application(); install_mini_app_routes(app, self.db, BOT_TOKEN, bot=self.bot,
                                                        bot_username="TwitchSignalTestbot")
        self.client = TestClient(TestServer(app)); await self.client.start_server(); self.addAsyncCleanup(self.client.close)

    async def post(self, path, **values):
        return await self.client.post(f"/app/api/streamer/{path}", json={"init_data": signed_webapp(101), **values})

    async def test_verifying_oauth_expires_at_same_deadline_as_result_and_store(self):
        at = time.time()
        await self.db.create_streamer_connect_intent("deadline-intent-101", 101, "f" * 64, now=at-601)
        await self.db.conn.execute("UPDATE streamer_connect_intents SET status='verifying' WHERE intent_id=?",
                                   ("deadline-intent-101",))
        await self.db.conn.commit()
        response = await self.post("connect-intent/status", intent_id="deadline-intent-101")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "expired")

    async def test_migration_restores_old_intent_copy_with_null_reason_idempotently(self):
        await self.db.create_community_intent("migration-intent-101", 101, 123, "channel", now=time.time())
        await self.db.conn.execute("ALTER TABLE streamer_community_intents DROP COLUMN permission_reason")
        await self.db.conn.commit()
        await self.db._migrate_streamer_intents_schema()
        await self.db._migrate_streamer_intents_schema()
        row = await self.db.get_community_intent("migration-intent-101")
        self.assertEqual((len(row), row[6], row[9]), (10, "pending", None))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))

    async def test_waiting_for_other_connection_lock_cannot_connect_after_expiry(self):
        with tempfile.TemporaryDirectory(prefix="ts-p13-expiry-") as directory:
            path = str(Path(directory) / "copy.db")
            first, second = Database(path), Database(path)
            await first.connect(); await second.connect()
            try:
                now = time.time()
                await first.link_streamer_identity(101, "11", "alpha", verified_at=now)
                await first.create_community_intent("locked-expiry-intent", 101, 17, "channel", now=now)
                await first.claim_community_intent(101, 17, -1001, now=now)
                expires = time.time() + .2
                await first.conn.execute("UPDATE streamer_community_intents SET expires_at=? WHERE intent_id=?",
                                         (expires, "locked-expiry-intent"))
                await first.conn.commit()
                await second.conn.execute("BEGIN IMMEDIATE")
                task = asyncio.create_task(first.add_streamer_community(101, -1001, "Channel", "channel",
                                         intent_id="locked-expiry-intent"))
                await asyncio.sleep(.05)
                self.assertFalse(task.done(), "other connection holds writer lock")
                while time.time() <= expires:
                    await asyncio.sleep(.01)
                await second.conn.rollback()
                self.assertFalse(await asyncio.wait_for(task, 3))
                self.assertEqual(await first.list_streamer_communities(101), [])
                self.assertEqual((await first.get_community_intent("locked-expiry-intent"))[6], "verifying")
            finally:
                await second.conn.rollback(); await first.close(); await second.close()

    async def test_new_group_is_rejected_at_api_and_store_and_prepared_is_channel_only(self):
        response = await self.post("community-intent", chat_type="group")
        self.assertEqual(response.status, 400)
        self.bot.save_prepared_keyboard_button.assert_not_awaited()
        with self.assertRaises(ValueError):
            await self.db.create_community_intent("old-group-intent-1111", 101, 77, "group", now=time.time())
        created = await (await self.post("community-intent", chat_type="channel")).json()
        self.assertEqual(created["prepared_id"], "prepared-channel")
        button = self.bot.save_prepared_keyboard_button.await_args.kwargs["button"]
        self.assertTrue(button.request_chat.chat_is_channel)
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        self.bot.send_message.assert_not_awaited()

    async def test_old_pending_group_cannot_create_a_new_group_via_fallback_or_shared(self):
        from bot.handlers.streams import cmd_start_link
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage
        now = time.time(); intent_id = "legacy-group-intent-1111"
        await self.db.conn.execute("INSERT INTO streamer_community_intents "
                                   "(intent_id,telegram_user_id,request_id,created_at,expires_at,chat_type,status) "
                                   "VALUES (?,101,77,?,?,'group','pending')", (intent_id, now, now + 600))
        await self.db.conn.commit()
        message = SimpleNamespace(chat=SimpleNamespace(id=101, type=ChatType.PRIVATE),
                                  from_user=SimpleNamespace(id=101), answer=AsyncMock())
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        await cmd_start_link(message, SimpleNamespace(args=f"tscommunity_{intent_id}"),
                             state, self.db, None)
        self.assertEqual(await state.get_data(),{})
        self.assertNotIn("reply_markup", message.answer.await_args.kwargs)
        self.bot.get_chat.return_value.type = "supergroup"
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, 77, -1001, now=now))
        self.assertEqual(await self.db.list_streamer_communities(101), [])

    async def test_legacy_group_stays_usable_for_free_and_network_is_not_permission_denied(self):
        await self.db.add_streamer_community(101, -1001, "Existing group", "supergroup", now=time.time())
        self.bot.get_chat.return_value.type = "supergroup"
        self.assertEqual((await self.post("communities/toggle", chat_id=-1001, enabled=True)).status, 200)
        self.bot.get_chat.side_effect = TimeoutError()
        profile = await (await self.post("profile")).json()
        self.assertEqual(profile["communities"][0]["permission_status"], "network_error")
        self.assertTrue(profile["communities"][0]["publishing"])
        response = await self.post("communities/toggle", chat_id=-1001, enabled=True)
        self.assertEqual(response.status, 503)
        self.assertEqual((await response.json())["error"], "verification_unavailable")
        self.assertEqual(len(await self.db.list_streamer_communities(101)), 1)
        self.bot.send_message.assert_not_awaited()

    async def test_network_failure_reason_persists_and_does_not_connect(self):
        created = await (await self.post("community-intent", chat_type="channel")).json()
        row = await self.db.get_community_intent(created["intent_id"])
        self.bot.get_chat.side_effect = TimeoutError()
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, row[2], -1001, now=time.time()))
        result = await (await self.post("community-intent/status", intent_id=created["intent_id"])).json()
        self.assertEqual((result["status"], result["permission_reason"]), ("failed", "network_error"))
        self.assertIsNone(result["chat_id"])
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        self.bot.send_message.assert_not_awaited()

    async def test_cancel_during_check_fences_late_verified_result(self):
        created = await (await self.post("community-intent", chat_type="channel")).json()
        row = await self.db.get_community_intent(created["intent_id"])
        entered, release = asyncio.Event(), asyncio.Event()
        chat = self.bot.get_chat.return_value
        async def delayed(_chat_id):
            entered.set(); await release.wait(); return chat
        self.bot.get_chat.side_effect = delayed
        task = asyncio.create_task(complete_community_intent(self.db, self.bot, 101, row[2], -1001, now=time.time()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual((await self.post("community-intent/cancel", intent_id=created["intent_id"])).status, 200)
        finally:
            release.set()
        self.assertFalse(await task)
        self.assertEqual((await self.db.get_community_intent(created["intent_id"]))[6], "cancelled")
        self.assertEqual(await self.db.list_streamer_communities(101), [])

    async def test_expiry_during_check_does_not_create_placement(self):
        created = await (await self.post("community-intent", chat_type="channel")).json()
        row = await self.db.get_community_intent(created["intent_id"])
        chat = self.bot.get_chat.return_value
        async def expire(_chat_id):
            await self.db.conn.execute("UPDATE streamer_community_intents SET expires_at=0 WHERE intent_id=?",
                                       (created["intent_id"],))
            await self.db.conn.commit()
            return chat
        self.bot.get_chat.side_effect = expire
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, row[2], -1001, now=time.time()))
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        result = await (await self.post("community-intent/status", intent_id=created["intent_id"])).json()
        self.assertEqual(result["status"], "expired")

    async def test_injected_epoch_is_preserved_while_measuring_check_duration(self):
        intent_id = "controlled-clock-intent-1111"
        await self.db.create_community_intent(intent_id, 101, 88, "channel", now=100)
        self.assertTrue(await complete_community_intent(self.db, self.bot, 101, 88, -1001, now=101))
        self.assertEqual((await self.db.get_community_intent(intent_id))[6], "connected")
        recorded = (await (await self.db.conn.execute(
            "SELECT verified_at FROM streamer_communities WHERE chat_id=-1001"
        )).fetchone())[0]
        self.assertGreaterEqual(recorded, 101)
        self.assertLess(recorded, 103)
