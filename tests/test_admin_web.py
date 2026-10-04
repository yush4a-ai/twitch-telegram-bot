import unittest
import os
import time
from urllib.parse import urlencode
from unittest.mock import patch

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer
from bot.config import load_config
from tests.test_admin_telegram_auth import BOT_TOKEN, OWNER_ID, signed_login, signed_webapp


KEY = "staging-test-key-with-at-least-32-chars-123"


class AdminWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_spoofed_forwarded_ip_cannot_bypass_peer_quota_or_evict_valid_state(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, bot_token=BOT_TOKEN,
                             bot_username='SignalStreamsBot', owner_id=OWNER_ID)
        session, base = await self.start_server(access)
        states = []
        for i in range(5):
            session.cookie_jar.clear()
            async with session.get(base+'/admin', headers={'X-Forwarded-For': f'203.0.113.{i}',
                    'Forwarded': f'for=203.0.113.{i}'}) as response:
                self.assertEqual(response.status, 200 if i < 4 else 429)
                if i < 4:
                    states.append(response.cookies['ts_admin_state'].value)
        self.assertTrue(access.consume_login_state(states[0], states[0]))

    async def start_server(self, access):
        server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=access,
        )

        async def snapshot():
            return {"environment": "staging", "audience": {"private_users": 3}}

        server.set_admin_snapshot_provider(snapshot)
        await server.start()
        self.addAsyncCleanup(server.stop)
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        self.addAsyncCleanup(session.close)
        port = server._runner.addresses[0][1]
        return session, f"http://127.0.0.1:{port}"

    async def test_disabled_panel_has_no_routes(self):
        session, base = await self.start_server(AdminAccess("", enabled=False))
        for path in ("/admin", "/admin/api/snapshot", "/admin/emergency/login"):
            async with session.get(base + path) as response:
                self.assertEqual(response.status, 404)

    async def test_login_protects_snapshot_and_logout_revokes_session(self):
        session, base = await self.start_server(AdminAccess(KEY, enabled=True, secure_cookie=False))
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)
            self.assertNotIn("private_users", await response.text())
        async with session.post(base + "/admin/login", data={"access_key": KEY}, allow_redirects=False) as response:
            self.assertEqual(response.status, 404)
        async with session.post(base + "/admin/emergency/login", data={"access_key": "wrong"}) as response:
            self.assertEqual(response.status, 401)
        async with session.post(base + "/admin/emergency/login", data={"access_key": KEY}, allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
            cookie = response.cookies["ts_admin"]
            self.assertTrue(cookie["httponly"])
            self.assertEqual(cookie["samesite"], "Strict")
            self.assertEqual(cookie["path"], "/admin")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["audience"]["private_users"], 3)
        async with session.post(base + "/admin/logout", allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)

    async def test_headers_and_secret_are_not_exposed(self):
        session, base = await self.start_server(AdminAccess(KEY, enabled=True, secure_cookie=True))
        async with session.get(base + "/admin") as response:
            body = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn("Вход", body)
            self.assertNotIn(KEY, body)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        async with session.post(base + "/admin/emergency/login", data={"access_key": KEY}, allow_redirects=False) as response:
            self.assertTrue(response.cookies["ts_admin"]["secure"])

    async def test_expired_cookie_is_rejected(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, session_ttl=0.01)
        session, base = await self.start_server(access)
        async with session.post(base + "/admin/emergency/login", data={"access_key": KEY}, allow_redirects=False):
            pass
        await __import__("asyncio").sleep(0.02)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)

    async def test_failed_login_is_rate_limited(self):
        session, base = await self.start_server(AdminAccess(KEY, enabled=True, secure_cookie=False))
        for _ in range(5):
            async with session.post(base + "/admin/emergency/login", data={"access_key": "bad"}) as response:
                self.assertEqual(response.status, 401)
        async with session.post(base + "/admin/emergency/login", data={"access_key": KEY}) as response:
            self.assertEqual(response.status, 429)

    async def test_unicode_login_attempt_returns_denial_not_server_error(self):
        session, base = await self.start_server(AdminAccess(KEY, enabled=True, secure_cookie=False))
        async with session.post(base + "/admin/emergency/login", data={"access_key": "неверный-ключ"}) as response:
            self.assertEqual(response.status, 401)
            self.assertIn("Неверный ключ", await response.text())

    async def test_unicode_configured_key_can_login(self):
        unicode_key = "длинный-ключ-для-тестовой-панели-владельца"
        session, base = await self.start_server(AdminAccess(unicode_key, enabled=True, secure_cookie=False))
        async with session.post(base + "/admin/emergency/login", data={"access_key": unicode_key}, allow_redirects=False) as response:
            self.assertEqual(response.status, 303)

    async def test_direct_url_and_non_owner_signed_identity_are_denied(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=OWNER_ID, bot_token=BOT_TOKEN, bot_username="SignalStreamsBot")
        session, base = await self.start_server(access)
        async with session.get(base + "/admin") as response:
            html = await response.text()
            self.assertNotIn("health-grid", html)
            self.assertNotIn("access_key", html)
            self.assertNotIn("/admin/emergency", html)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)
        async with session.post(base + "/admin/telegram-webapp", data={"init_data": signed_webapp(OWNER_ID + 1)}, allow_redirects=False) as response:
            self.assertEqual(response.status, 403)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)

    async def test_owner_webapp_identity_opens_panel(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=OWNER_ID, bot_token=BOT_TOKEN)
        session, base = await self.start_server(access)
        async with session.post(base + "/admin/telegram-webapp", data={"init_data": signed_webapp(OWNER_ID)}, allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 200)

    async def test_widget_callback_requires_state_and_signed_owner(self):
        access = AdminAccess(KEY, enabled=True, secure_cookie=False, owner_id=OWNER_ID, bot_token=BOT_TOKEN, bot_username="SignalStreamsBot")
        session, base = await self.start_server(access)
        async with session.get(base + "/admin") as response:
            html = await response.text()
            self.assertIn("telegram-widget.js", html)
            state = response.cookies["ts_admin_state"].value
        async with session.get(base + "/admin/telegram-login?" + urlencode({**signed_login(OWNER_ID), "state": "wrong"}), allow_redirects=False) as response:
            self.assertEqual(response.status, 403)
        async with session.get(base + "/admin/telegram-login?" + urlencode({**signed_login(OWNER_ID + 1), "state": state}), allow_redirects=False) as response:
            self.assertEqual(response.status, 403)
        async with session.get(base + "/admin/telegram-login?" + urlencode({**signed_login(OWNER_ID), "state": state}), allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
        async with session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 200)


class AdminConfigTests(unittest.TestCase):
    def test_admin_key_is_enabled_only_on_staging_railway(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "test:token",
            "TWITCH_CLIENT_ID": "test-client",
            "TWITCH_CLIENT_SECRET": "test-secret",
            "TOKEN_ENCRYPTION_KEY": "test-encryption-key",
            "PUBLIC_URL": "https://example.test",
            "DB_PATH": "/data/bot.db",
            "RAILWAY_PROJECT_ID": "project-test",
            "RAILWAY_VOLUME_MOUNT_PATH": "/data",
            "ADMIN_PANEL_ACCESS_KEY": KEY,
        }
        with patch.dict(os.environ, {**base, "RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            self.assertIsNone(load_config().admin_panel_access_key)
        with patch.dict(os.environ, {**base, "RAILWAY_ENVIRONMENT_NAME": "staging"}, clear=True):
            self.assertEqual(load_config().admin_panel_access_key, KEY)


if __name__ == "__main__":
    unittest.main()
