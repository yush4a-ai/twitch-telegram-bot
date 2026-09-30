import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer


KEY = "staging-test-key-with-at-least-32-chars-123"


class AdminUiRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False),
        )

        async def snapshot():
            return {"environment": "staging", "audience": None}

        self.server.set_admin_snapshot_provider(snapshot)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def test_login_and_panel_routes_have_distinct_access(self):
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertIn("Вход", body)
            self.assertNotIn("id=\"health-grid\"", body)
        for path in ("/admin/panel.css", "/admin/panel.js"):
            async with self.session.get(self.base + path) as response:
                self.assertEqual(response.status, 401)
        async with self.session.post(self.base + "/admin/login", data={"access_key": KEY}, allow_redirects=False):
            pass
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn('id="health-grid"', body)
            self.assertIn('id="live-list"', body)
            self.assertIn('<table', body)
            self.assertIn('href="#main-content"', body)
            self.assertIn('src="/admin/panel.js"', body)
        async with self.session.get(self.base + "/admin/panel.css") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/css", response.headers["Content-Type"])
        async with self.session.get(self.base + "/admin/panel.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
