"""Signed streamer onboarding and single-use community selection."""

import os
import asyncio
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiogram.enums import ChatType

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from bot.database import Database
from bot.oauth import OAuthCallbackServer, UserTokenResult
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class MiniAppStreamerConnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=100)
        self.user_admin = True

        async def member(_chat_id, user_id):
            return SimpleNamespace(status="administrator" if user_id != 101 or self.user_admin else "member", can_post_messages=True, can_edit_messages=False)

        self.bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=SimpleNamespace(id=-1001, type="channel", title="Free channel")),
            get_chat_member=AsyncMock(side_effect=member),
            save_prepared_keyboard_button=AsyncMock(return_value=SimpleNamespace(id="prepared-test")),
        )
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
            streamer_bot=self.bot,
            mini_app_oauth_client_id="client", mini_app_oauth_client_secret="secret",
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.addAsyncCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    def request(self, path, actor_id=101, **fields):
        return self.session.post(
            self.base + path,
            json={"init_data": signed_webapp(actor_id), **fields},
        )

    async def test_free_profile_and_prepared_chat_request_do_not_send_post(self):
        async with self.request("/app/api/streamer/profile") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["twitch_login"], "alpha")
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual(payload["prepared_id"], "prepared-test")
            intent_id = payload["intent_id"]
        self.bot.save_prepared_keyboard_button.assert_awaited_once()
        button = self.bot.save_prepared_keyboard_button.await_args.kwargs["button"]
        self.assertTrue(button.request_chat.chat_is_channel)
        async with self.request("/app/api/streamer/community-intent/status", intent_id=intent_id) as response:
            self.assertEqual((await response.json())["status"], "pending")
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        self.assertFalse(hasattr(self.bot, "send_message"))

    async def test_cached_channel_photo_is_hidden_after_owner_rights_revoked(self):
        await self.db.add_streamer_community(101,-1001,'Free channel','channel')
        self.bot.get_chat.return_value.photo=SimpleNamespace(small_file_id='photo-id')
        self.bot.get_file=AsyncMock(return_value=SimpleNamespace(file_path='photos/0.jpg'))
        async def download(_path,*,destination,timeout):destination.write(b'\xff\xd8\xffimage')
        self.bot.download_file=AsyncMock(side_effect=download)
        async with self.request('/app/api/streamer/profile') as response:
            self.assertTrue((await response.json())['communities'][0]['avatar_url'].startswith('data:image/jpeg;base64,'))
        self.user_admin=False
        async with self.request('/app/api/streamer/profile') as response:
            community=(await response.json())['communities'][0]
            self.assertFalse(community['permission_ok'])
            self.assertIsNone(community['avatar_url'])
        self.bot.get_file.assert_awaited_once()

    async def test_callback_is_not_permission_and_intent_is_single_use(self):
        from bot.mini_app_streamer import complete_community_intent
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            intent_id = (await response.json())["intent_id"]
        row = await self.db.get_community_intent(intent_id)
        request_id = row[2]
        self.assertFalse(await complete_community_intent(self.db, self.bot, 202, request_id, -1001, now=time.time()))
        self.user_admin = False
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, request_id, -1001, now=time.time()))
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        async with self.request("/app/api/streamer/community-intent/status", intent_id=intent_id, actor_id=202) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/streamer/community-intent/status", intent_id=intent_id) as response:
            self.assertEqual((await response.json())["status"], "denied")
        self.user_admin = True
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, request_id, -1001, now=time.time()))
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            second_id = (await response.json())["intent_id"]
        second = await self.db.get_community_intent(second_id)
        self.assertTrue(await complete_community_intent(self.db, self.bot, 101, second[2], -1001, now=time.time()))
        self.assertFalse(await complete_community_intent(self.db, self.bot, 101, second[2], -1001, now=time.time()))
        self.assertEqual(len(await self.db.list_streamer_communities(101)), 1)

    async def test_expired_intent_and_cancel_leave_no_connected_community(self):
        from bot.mini_app_streamer import complete_community_intent
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            intent_id = (await response.json())["intent_id"]
        row = await self.db.get_community_intent(intent_id)
        self.assertFalse(await complete_community_intent(
            self.db, self.bot, 101, row[2], -1001, now=row[4] + 1,
        ))
        self.assertEqual(await self.db.list_streamer_communities(101), [])
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            cancelled_id = (await response.json())["intent_id"]
        cancelled = await self.db.get_community_intent(cancelled_id)
        async with self.request(
            "/app/api/streamer/community-intent/cancel", actor_id=202,
            intent_id=cancelled_id,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/streamer/community-intent/cancel", intent_id=cancelled_id,
        ) as response:
            self.assertEqual(response.status, 200)
        async with self.request(
            "/app/api/streamer/community-intent/status", intent_id=cancelled_id,
        ) as response:
            self.assertEqual((await response.json())["status"], "cancelled")
        self.assertFalse(await complete_community_intent(
            self.db, self.bot, 101, cancelled[2], -1001, now=time.time(),
        ))

    async def test_older_client_deep_link_and_chat_shared_use_same_intent(self):
        from bot.handlers.streams import cmd_start_link
        from bot.handlers.auth import on_streamer_community_shared
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            intent_id = (await response.json())["intent_id"]
        row = await self.db.get_community_intent(intent_id)
        message = SimpleNamespace(
            chat=SimpleNamespace(id=101, type=ChatType.PRIVATE),
            from_user=SimpleNamespace(id=101), bot=self.bot,
            answer=AsyncMock(),
        )
        await cmd_start_link(
            message, SimpleNamespace(args=f"tscommunity_{intent_id}"),
            FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101)), self.db, None,
        )
        keyboard = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.keyboard[0][0].request_chat.request_id, row[2])
        message.chat_shared = SimpleNamespace(request_id=row[2], chat_id=-1001)
        await on_streamer_community_shared(message, self.db)
        self.assertEqual(len(await self.db.list_streamer_communities(101)), 1)
        self.assertEqual((await self.db.get_community_intent(intent_id))[6], "connected")
        before = message.answer.await_count
        await on_streamer_community_shared(message, self.db)
        self.assertEqual(message.answer.await_count, before)

    async def test_explicit_free_publication_toggle_rechecks_placement_rights(self):
        from bot.mini_app_streamer import complete_community_intent
        async with self.request(
            "/app/api/streamer/communities/toggle", chat_id=-1001, enabled=True,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            intent_id = (await response.json())["intent_id"]
        request_id = (await self.db.get_community_intent(intent_id))[2]
        self.assertTrue(await complete_community_intent(
            self.db, self.bot, 101, request_id, -1001, now=time.time(),
        ))
        self.assertEqual(await self.db.list_channels(-1001), [])
        self.user_admin = False
        async with self.request(
            "/app/api/streamer/communities/toggle", chat_id=-1001, enabled=True,
        ) as response:
            self.assertEqual(response.status, 403)
        self.user_admin = True
        async with self.request(
            "/app/api/streamer/communities/toggle", chat_id=-1001, enabled=True,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.list_channels(-1001), ["alpha"])
        async with self.request("/app/api/streamer/profile") as response:
            self.assertTrue((await response.json())["communities"][0]["publishing"])
        async with self.request(
            "/app/api/streamer/communities/toggle", chat_id=-1001, enabled=False,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse(await self.db.get_notify_enabled(-1001, "alpha"))

    async def test_twitch_intent_binds_only_verified_user_after_verified_exchange(self):
        async with self.request("/app/api/streamer/connect-intent", actor_id=202) as response:
            self.assertEqual(response.status, 200)
            payload = await response.json()
        state = parse_qs(urlparse(payload["authorize_url"]).query)["state"][0]
        async with self.request(
            "/app/api/streamer/connect-intent/status", intent_id=payload["intent_id"],
        ) as response:
            self.assertEqual(response.status, 403)
        result = UserTokenResult("beta", "22", "access-test", "refresh-test", time.time() + 3600)
        with patch("bot.oauth._exchange_code", new_callable=AsyncMock, return_value=result) as exchange:
            self.server._pending[state].set_result("fake-code")
            for _ in range(30):
                row = await self.db.get_streamer_connect_intent(payload["intent_id"])
                if row[4] == "connected":
                    break
                await asyncio.sleep(0.02)
        exchange.assert_awaited_once()
        self.assertEqual(await self.db.get_streamer_identity(202), ("22", "beta"))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        async with self.request(
            "/app/api/streamer/connect-intent/status", actor_id=202,
            intent_id=payload["intent_id"],
        ) as response:
            self.assertEqual((await response.json())["status"], "connected")

    async def test_expired_or_cancelled_twitch_intent_never_links_identity(self):
        async with self.request("/app/api/streamer/connect-intent", actor_id=202) as response:
            payload = await response.json()
        state = parse_qs(urlparse(payload["authorize_url"]).query)["state"][0]
        await self.db.conn.execute(
            "UPDATE streamer_connect_intents SET expires_at=0 WHERE intent_id=?",
            (payload["intent_id"],),
        )
        await self.db.conn.commit()
        with patch("bot.oauth._exchange_code", new_callable=AsyncMock) as exchange:
            self.server._pending[state].set_result("fake-code")
            await asyncio.sleep(0.05)
            exchange.assert_not_awaited()
        self.assertIsNone(await self.db.get_streamer_identity(202))
        async with self.request("/app/api/streamer/connect-intent", actor_id=202) as response:
            second = await response.json()
        async with self.request(
            "/app/api/streamer/connect-intent/cancel", actor_id=202,
            intent_id=second["intent_id"],
        ) as response:
            self.assertEqual(response.status, 200)
        async with self.request(
            "/app/api/streamer/connect-intent/status", actor_id=202,
            intent_id=second["intent_id"],
        ) as response:
            self.assertEqual((await response.json())["status"], "cancelled")
        self.assertIsNone(await self.db.get_streamer_identity(202))

    async def test_interrupted_verification_expires_in_status(self):
        async with self.request("/app/api/streamer/community-intent", chat_type="channel") as response:
            community_id = (await response.json())["intent_id"]
        await self.db.conn.execute(
            "UPDATE streamer_community_intents SET status='verifying',expires_at=0 "
            "WHERE intent_id=?", (community_id,),
        )
        await self.db.conn.commit()
        async with self.request(
            "/app/api/streamer/community-intent/status", intent_id=community_id,
        ) as response:
            self.assertEqual((await response.json())["status"], "expired")

        async with self.request("/app/api/streamer/connect-intent", actor_id=202) as response:
            connect_id = (await response.json())["intent_id"]
        await self.db.conn.execute(
            "UPDATE streamer_connect_intents SET status='verifying',expires_at=0 "
            "WHERE intent_id=?", (connect_id,),
        )
        await self.db.conn.commit()
        async with self.request(
            "/app/api/streamer/connect-intent/status", actor_id=202,
            intent_id=connect_id,
        ) as response:
            self.assertEqual((await response.json())["status"], "expired")


if __name__ == "__main__":
    unittest.main()
