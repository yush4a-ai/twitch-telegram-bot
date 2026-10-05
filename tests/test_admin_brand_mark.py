"""Знак панели: тот же маскот, что в боте и приложении, и только для своих.

Монограмма «TS» была третьим разным знаком в проекте. Проверка держит два
свойства: знак отдаётся как картинка, и посторонний его не получит.
"""
import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, OWNER_ID


KEY = "staging-test-key-with-at-least-32-chars-123"


class AdminBrandMarkTests(unittest.IsolatedAsyncioTestCase):
    async def server(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, bot_token=BOT_TOKEN,
                             bot_username="TwitchSignalBot", owner_id=OWNER_ID)
        server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0, admin_access=access,
        )

        async def snapshot():
            return {"environment": "staging", "audience": {"private_users": 1}}

        server.set_admin_snapshot_provider(snapshot)
        await server.start()
        self.addAsyncCleanup(server.stop)
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.addAsyncCleanup(session.close)
        return access, session, f"http://127.0.0.1:{server._runner.addresses[0][1]}"

    async def test_mark_is_private_and_is_a_picture(self):
        _access, session, base = await self.server()
        async with session.get(base + "/admin/mark.png") as response:
            self.assertEqual(response.status, 401)
        async with session.post(base + "/admin/emergency/login", data={"access_key": KEY},
                                allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
        async with session.get(base + "/admin/mark.png") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "image/png")
            body = await response.read()
        self.assertGreater(len(body), 1000)
        self.assertTrue(body.startswith(b"\x89PNG"))

    async def test_panel_markup_uses_the_picture_not_the_monogram(self):
        from pathlib import Path

        markup = (Path(__file__).resolve().parents[1] / "bot" / "admin_ui" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="brand-mark" src="mark.png"', markup)
        self.assertNotIn(">TS<", markup)
        self.assertIn("object-fit", (
            Path(__file__).resolve().parents[1] / "bot" / "admin_ui" / "panel.css"
        ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
