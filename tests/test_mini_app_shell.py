"""The installed shell is reachable without exposing private state in assets."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aiogram.enums import ChatType

from bot.database import Database
from bot.config import load_config
from bot.handlers.streams import _viewer_url
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN


class MiniAppShellTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_flag_is_off_outside_pinned_staging(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "123456:test", "TWITCH_CLIENT_ID": "client",
            "TWITCH_CLIENT_SECRET": "secret", "TOKEN_ENCRYPTION_KEY": "test-key",
            "PUBLIC_URL": "https://example.test", "DB_PATH": "/data/bot.db",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
            "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
            "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
            "RAILWAY_ENVIRONMENT_NAME": "staging",
        }
        with patch.dict(os.environ, base, clear=True):
            self.assertTrue(load_config().mini_app_enabled)
        with patch.dict(os.environ, {**base, "RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            self.assertFalse(load_config().mini_app_enabled)
        with patch.dict(os.environ, {**base, "RAILWAY_PROJECT_ID": "other"}, clear=True):
            self.assertFalse(load_config().mini_app_enabled)

    def test_private_menu_opens_new_app_when_enabled(self):
        config = SimpleNamespace(
            mini_app_enabled=True, viewer_plus_enabled=True,
            oauth_public_base_url="https://staging.example.test",
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(type=ChatType.PRIVATE, id=101),
            from_user=SimpleNamespace(id=101),
        )
        self.assertEqual(_viewer_url(message, config), "https://staging.example.test/app")

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
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

    async def test_shell_loads_every_module_with_strict_security_headers(self):
        async with self.session.get(self.base + "/app") as response:
            self.assertEqual(response.status, 200)
            html = await response.text()
            self.assertIn('src="/app/app.js"', html)
            self.assertIn('id="mode-switch"', html)
            self.assertIn('id="tab-bar"', html)
        for asset in ("app.css", "app.js", "telegram.js", "router.js", "api.js", "components.js"):
            with self.subTest(asset=asset):
                async with self.session.get(self.base + "/app/" + asset) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertNotIn(BOT_TOKEN, await response.text())
        async with self.session.get(self.base + "/app/secret.js") as response:
            self.assertEqual(response.status, 404)
