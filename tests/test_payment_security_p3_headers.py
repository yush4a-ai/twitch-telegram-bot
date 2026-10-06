"""C7: приватные кабинеты запрещают встраивание, HSTS только на https-контуре.

Исключение для мини-аппа сохранено: он открывается во фрейме web.telegram.org
и это единственная страница с другим ``frame-ancestors``.
"""

import os
import tempfile
import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.database import Database
from bot.oauth import OAuthCallbackServer
from bot.streamer_auth import StreamerAccess
from tests.test_admin_telegram_auth import BOT_TOKEN, KEY, OWNER_ID


class PrivateCabinetHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "headers.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(
                KEY, enabled=True, secure_cookie=False, owner_id=OWNER_ID,
                bot_token=BOT_TOKEN),
            streamer_access=StreamerAccess(BOT_TOKEN, secure_cookie=False),
            streamer_db=self.db,
            viewer_db=self.db, viewer_bot_token=BOT_TOKEN,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
        )
        await self.server.start()
        self.addAsyncCleanup(self.server.stop)
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def test_private_cabins_forbid_framing_and_hsts_follows_the_https_contour(self):
        for path in ("/admin", "/admin/emergency", "/viewer", "/streamer"):
            with self.subTest(path=path):
                async with self.session.get(self.base + path) as response:
                    self.assertEqual(response.status, 200)
                    policy = response.headers["Content-Security-Policy"]
                    self.assertIn("frame-ancestors 'none'", policy)
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                    # Локальный контур работает по http: HSTS здесь не отдаётся.
                    self.assertNotIn("Strict-Transport-Security", response.headers)
        async with self.session.post(
            self.base + "/app/api/bootstrap", json={},
        ) as response:
            self.assertEqual(response.status, 401)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        # Railway терминирует TLS на прокси: признак защищённого контура приходит
        # заголовком X-Forwarded-Proto.
        async with self.session.get(
            self.base + "/admin", headers={"X-Forwarded-Proto": "https"},
        ) as response:
            self.assertEqual(response.headers["Strict-Transport-Security"], "max-age=31536000")

    async def test_mini_app_frame_ancestors_exception_for_telegram_web_is_untouched(self):
        async with self.session.get(self.base + "/app") as response:
            self.assertEqual(response.status, 200)
            policy = response.headers["Content-Security-Policy"]
            self.assertIn("frame-ancestors https://web.telegram.org", policy)
            self.assertNotIn("frame-ancestors 'none'", policy)
            ancestors = [part.strip() for part in policy.split(";")
                         if part.strip().startswith("frame-ancestors")]
            self.assertEqual(ancestors, ["frame-ancestors https://web.telegram.org"])


if __name__ == "__main__":
    unittest.main()
