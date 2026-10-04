import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer


KEY = "staging-test-key-with-at-least-32-chars-123"

VIEWS = ("overview", "users", "access", "system", "growth", "payments")


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

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def test_login_and_panel_routes_have_distinct_access(self):
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertIn("Вход", body)
            self.assertNotIn('id="app-nav"', body)
        for path in ("/admin/panel.css", "/admin/panel.js"):
            async with self.session.get(self.base + path) as response:
                self.assertEqual(response.status, 401)
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn('href="#main-content"', body)
            self.assertIn('src="/admin/panel.js"', body)
        async with self.session.get(self.base + "/admin/panel.css") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/css", response.headers["Content-Type"])
        async with self.session.get(self.base + "/admin/panel.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers["Content-Type"])

    async def test_panel_shell_has_navigation_and_every_view(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="app-nav"', html)
        for view in VIEWS:
            self.assertIn(f'id="view-{view}"', html)
        for anchor in ("#/overview", "#/users", "#/access", "#/system", "#/growth", "#/payments"):
            self.assertIn(anchor, html)

    async def test_overview_renders_metrics_and_attention_region(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        for field in ("stat-users", "stat-plus", "stat-deliveries", "stat-active-today", "stat-new-7d"):
            self.assertIn(f'id="{field}"', html)
        self.assertIn('id="attention-list"', html)
        self.assertIn('id="health-line"', html)
        for key in ("active_total", "deliveries", "attention"):
            self.assertIn(key, script)

    async def test_access_screen_has_tabs_and_empty_states(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="tab-active"', html)
        self.assertIn('id="tab-history"', html)
        self.assertIn('id="access-active-body"', html)
        self.assertIn('id="access-history-body"', html)
        self.assertIn("Ничего не найдено", html)

    async def test_system_screen_shows_queue_and_backup_honestly(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        for field in ("queue-pending", "queue-failed", "queue-oldest", "backup-state", "restore-verified"):
            self.assertIn(f'id="{field}"', html)
        for key in ("pending_jobs", "failed_jobs", "oldest_due_age_seconds", "restore_verified"):
            self.assertIn(key, script)

    async def test_placeholders_explain_why_data_is_missing(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn("Нет сквозных данных", html)
        self.assertIn("Приём платежей не подключён", html)
        self.assertIn("Нет данных", html)


if __name__ == "__main__":
    unittest.main()
