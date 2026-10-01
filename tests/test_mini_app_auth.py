"""The new app accepts only a fresh Telegram identity on API routes."""

import os
import tempfile
import time
import unittest

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class MiniAppAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(202, "beta")
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()
        await self.db.close()
        self.directory.cleanup()

    async def test_bootstrap_requires_signed_fresh_unique_init_data(self):
        for body, expected in (
            ({}, 401),
            ({"init_data": signed_webapp(101).replace("Owner", "Other")}, 403),
            ({"init_data": signed_webapp(101, auth_date=int(time.time()) - 601)}, 403),
            ({"init_data": signed_webapp(101) + "&user=evil"}, 403),
        ):
            with self.subTest(expected=expected):
                async with self.session.post(self.base + "/app/api/bootstrap", json=body) as response:
                    self.assertEqual(response.status, expected)
        async with self.session.post(
            self.base + "/app/api/bootstrap",
            data='{"init_data":"first","init_data":"second"}',
        ) as response:
            self.assertEqual(response.status, 400)

    async def test_identity_is_derived_and_admin_is_not_in_bootstrap(self):
        async with self.session.post(
            self.base + "/app/api/bootstrap",
            json={"init_data": signed_webapp(202), "user_id": 101, "mode": "streamer"},
        ) as response:
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data["user"]["id"], 202)
            self.assertEqual(data["capabilities"]["viewer_channel_limit"], 50)
            self.assertEqual(data["capabilities"]["viewer_video_slots"], 0)
            self.assertNotIn("admin", str(data).lower())
            self.assertNotIn("alpha", str(data))
            self.assertNotIn("beta", str(data))
        async with self.session.get(self.base + "/admin") as response:
            self.assertEqual(response.status, 404)

    async def test_public_shell_has_no_private_data_and_unmounted_app_is_404(self):
        async with self.session.get(self.base + "/app") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertIn("Content-Security-Policy", response.headers)
            html = await response.text()
            self.assertNotIn("alpha", html)
            self.assertNotIn(BOT_TOKEN, html)
        await self.server.stop()
        disabled = OAuthCallbackServer("https://example.test/twitch/callback", "127.0.0.1", 0)
        await disabled.start()
        try:
            base = f"http://127.0.0.1:{disabled._runner.addresses[0][1]}"
            async with self.session.get(base + "/app") as response:
                self.assertEqual(response.status, 404)
            async with self.session.post(base + "/app/api/bootstrap", json={"init_data": signed_webapp(101)}) as response:
                self.assertEqual(response.status, 404)
        finally:
            await disabled.stop()
