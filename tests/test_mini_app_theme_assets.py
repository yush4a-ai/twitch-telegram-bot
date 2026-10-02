"""Theme module is served through the same strict public asset allowlist."""

import unittest

from aiohttp.test_utils import TestClient, TestServer

from scripts.mini_app_browser_fixture import build_fixture


class ThemeAssets(unittest.IsolatedAsyncioTestCase):
    async def test_theme_module_is_allowlisted_without_inline_script_or_private_values(self):
        app, _db = await build_fixture("free-empty")
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.get("/app/theme.js")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        text = await response.text()
        self.assertIn("createThemeController", text)
        self.assertNotIn("test-telegram-token", text)
        for path in ("/app/theme.json", "/app/themes.js", "/app/secrets.js"):
            with self.subTest(path=path):
                self.assertEqual((await client.get(path)).status, 404)
        response = await client.get("/app")
        self.assertIn("script-src 'self' https://telegram.org", response.headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", response.headers["Content-Security-Policy"])
