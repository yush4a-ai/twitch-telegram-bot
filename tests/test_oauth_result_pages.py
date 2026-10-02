"""An accepted OAuth code is not a verified Twitch binding or success screen."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer, UserTokenResult, OAuthFlowError
from tests.test_admin_telegram_auth import BOT_TOKEN


class OAuthResultTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:"); await self.db.connect(); self.addAsyncCleanup(self.db.close)
        self.server = OAuthCallbackServer("http://127.0.0.1/twitch/callback", "127.0.0.1", 0,
                                          mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
                                          mini_app_oauth_client_id="fixture-client", mini_app_oauth_client_secret="fixture-secret")
        await self.server.start(); self.addAsyncCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.addAsyncCleanup(self.session.close)

    async def create(self):
        intent_id, url, _expires = await self.server.create_streamer_connect_intent(101)
        return intent_id, parse_qs(urlparse(url).query)["state"][0]

    async def status(self, session=None):
        async with (session or self.session).get(self.base + "/twitch/result/status") as response:
            return response.status, await response.json()

    async def wait_terminal(self, expected):
        for _ in range(40):
            code, result = await self.status()
            if code == 200 and result["status"] == expected:
                return result
            await asyncio.sleep(.02)
        self.fail(f"Result did not reach {expected}")

    async def test_code_received_is_waiting_until_verified_exchange_and_binding(self):
        intent, state = await self.create(); entered, release = asyncio.Event(), asyncio.Event()
        async def exchange(*_args):
            entered.set(); await release.wait()
            return UserTokenResult("alpha", "11", "fixture-access", "fixture-refresh", time.time() + 3600)
        with patch("bot.oauth._exchange_code", side_effect=exchange):
            async with self.session.get(self.base + "/twitch/callback", params={"state": state, "code": "fixture-code"}, allow_redirects=False) as response:
                self.assertEqual(response.status, 303)
                self.assertEqual(response.headers["Location"], "/twitch/result")
                self.assertIn("HttpOnly", response.headers["Set-Cookie"])
            await asyncio.wait_for(entered.wait(), 2)
            self.assertIsNone(await self.db.get_streamer_identity(101))
            code, result = await self.status()
            self.assertEqual(code, 200)
            self.assertIn(result["status"], {"pending", "verifying"})
            self.assertNotIn("twitch_login", result)
            async with self.session.get(self.base + "/twitch/result") as response:
                text = await response.text()
                self.assertIn("Проверяем подключение", text)
                for secret in (state, "fixture-code", "fixture-access", "fixture-refresh", intent):
                    self.assertNotIn(secret, text)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            release.set()
            final = await self.wait_terminal("connected")
            self.assertEqual(final["twitch_login"], "alpha")
            self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        async with aiohttp.ClientSession() as stranger:
            self.assertEqual((await self.status(stranger))[0], 403)
        async with self.session.get(self.base + "/twitch/callback", params={"state": state, "code": "fixture-code"}) as replay:
            self.assertEqual(replay.status, 400)
            self.assertNotIn("успешно", await replay.text())

    async def test_exchange_failure_and_provider_error_never_show_success_or_unescaped_input(self):
        _intent, state = await self.create()
        with patch("bot.oauth._exchange_code", side_effect=OAuthFlowError("fixture-exchange-failure")):
            async with self.session.get(self.base + "/twitch/callback", params={"state": state, "code": "fixture-code"}, allow_redirects=False) as response:
                self.assertEqual(response.status, 303)
            await self.wait_terminal("failed")
        self.assertIsNone(await self.db.get_streamer_identity(101))
        self.server.register_state("legacy-error-state")
        response = await self.server._handle_callback(SimpleNamespace(query={
            "state": "legacy-error-state", "error": '<script>alert("secret")</script>',
        }))
        self.assertEqual(response.status, 400)
        self.assertNotIn('<script>alert("secret")</script>', response.text)
        self.assertNotIn("legacy-error-state", response.text)
        with self.assertRaises(OAuthFlowError):
            await self.server.wait_for_code("legacy-error-state", timeout=1)

    async def test_cancel_or_expiry_during_exchange_fences_late_binding(self):
        for action in ("cancel", "expire"):
            with self.subTest(action=action):
                intent, state = await self.create(); entered, release = asyncio.Event(), asyncio.Event()
                async def exchange(*_args):
                    entered.set(); await release.wait()
                    return UserTokenResult("alpha", "11", "fixture-access", "fixture-refresh", time.time() + 3600)
                with patch("bot.oauth._exchange_code", side_effect=exchange):
                    async with self.session.get(self.base + "/twitch/callback", params={"state": state, "code": "fixture-code"}, allow_redirects=False):
                        pass
                    await asyncio.wait_for(entered.wait(), 2)
                    if action == "cancel":
                        self.assertTrue(await self.server.cancel_streamer_connect_intent(101, intent))
                    else:
                        await self.db.conn.execute("UPDATE streamer_connect_intents SET expires_at=0 WHERE intent_id=?", (intent,))
                        await self.db.conn.commit()
                    release.set()
                    await self.wait_terminal("cancelled" if action == "cancel" else "expired")
                    self.assertIsNone(await self.db.get_streamer_identity(101))

    async def test_legacy_code_acceptance_does_not_claim_verified_success(self):
        self.server.register_state("legacy-state")
        response = await self.server._handle_callback(SimpleNamespace(query={"state": "legacy-state", "code": "legacy-code"}))
        self.assertEqual(response.status, 200)
        self.assertIn("Вернитесь в бот, чтобы проверить подключение", response.text)
        self.assertNotIn("успешно", response.text)
        self.assertEqual(await self.server.wait_for_code("legacy-state", timeout=1), "legacy-code")
