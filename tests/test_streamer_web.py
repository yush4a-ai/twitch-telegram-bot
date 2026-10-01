import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

import aiohttp

from bot.database import Database
from bot.config import load_config
from bot.oauth import OAuthCallbackServer
from bot.streamer_auth import StreamerAccess
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_login, signed_webapp


class StreamerWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=100)
        self.access = StreamerAccess(BOT_TOKEN, bot_username="TwitchSignalTestbot", secure_cookie=False)
        self.user_admin = True
        async def member(_chat_id, user_id):
            return SimpleNamespace(
                status="administrator" if user_id != 101 or self.user_admin else "member"
            )
        self.bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup", title="Group")),
            get_chat_member=AsyncMock(side_effect=member),
        )
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            streamer_access=self.access, streamer_db=self.db, streamer_bot=self.bot,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()
        await self.db.close()
        self.directory.cleanup()

    async def test_direct_url_denies_data_and_admin_cookie_does_not_authenticate(self):
        async with self.session.get(self.base + "/streamer") as response:
            self.assertEqual(response.status, 200)
            html = await response.text()
            self.assertIn("Telegram", html)
            self.assertNotIn("alpha", html)
            self.assertNotIn("Админ-панель", html)
        async with self.session.get(self.base + "/streamer/api/profile", cookies={"ts_admin": "anything"}) as response:
            self.assertEqual(response.status, 401)
            self.assertNotIn("alpha", await response.text())

    async def test_signed_linked_user_sees_only_own_profile_and_expiry(self):
        await self.db.issue_test_streamer_plus(
            "11", "grant-1", starts_at=time.time() - 5,
            expires_at=time.time() + 600, issued_by=425785231,
        )
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(101)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
            self.assertEqual(response.cookies["ts_streamer"]["path"], "/streamer")
        async with self.session.get(self.base + "/streamer/api/profile") as response:
            self.assertEqual(response.status, 200)
            profile = await response.json()
            self.assertEqual(profile["twitch_login"], "alpha")
            self.assertTrue(profile["plus_active"])
            self.assertGreater(profile["plus_expires_at"], time.time())
            self.assertNotIn("telegram_user_id", profile)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        async with self.session.post(self.base + "/streamer/logout", allow_redirects=False) as response:
            self.assertEqual(response.status, 303)
        async with self.session.get(self.base + "/streamer/api/profile") as response:
            self.assertEqual(response.status, 401)

    async def test_unlinked_and_forged_signed_identity_are_denied(self):
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(202)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 403)
        forged = signed_webapp(101).replace("Owner", "Someone")
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": forged}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.session.get(self.base + "/streamer/api/profile") as response:
            self.assertEqual(response.status, 401)

    async def test_widget_requires_signed_user_and_matching_one_time_state(self):
        async with self.session.get(self.base + "/streamer") as response:
            html = await response.text()
            self.assertIn("telegram-widget.js", html)
            state = response.cookies["ts_streamer_state"].value
        async with self.session.get(
            self.base + "/streamer/telegram-login?" + urlencode({**signed_login(101), "state": "wrong"}),
            allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 403)

        async with self.session.get(
            self.base + "/streamer/telegram-login?" + urlencode({**signed_login(202), "state": state}),
            allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.session.get(
            self.base + "/streamer/telegram-login?" + urlencode({**signed_login(101), "state": state}),
            allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
        async with self.session.get(self.base + "/streamer/api/profile") as response:
            self.assertEqual(response.status, 200)
        async with self.session.get(
            self.base + "/streamer/telegram-login?" + urlencode({**signed_login(101), "state": state}),
            allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 403)

    async def test_community_connect_requires_signed_linked_free_and_fresh_telegram_rights(self):
        async with self.session.post(self.base + "/streamer/api/communities", json={"chat_id": -1001}) as response:
            self.assertEqual(response.status, 401)
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(101)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
        async with self.session.post(self.base + "/streamer/api/communities", json={"chat_id": -1001}) as response:
            self.assertEqual(response.status, 201)
        async with self.session.post(self.base + "/streamer/api/communities", json={"chat_id": 101}) as response:
            self.assertEqual(response.status, 400)
        self.user_admin = False
        async with self.session.post(self.base + "/streamer/api/communities", json={"chat_id": -1001}) as response:
            self.assertEqual(response.status, 403)
        self.user_admin = True
        async with self.session.post(self.base + "/streamer/api/communities", json={"chat_id": -1001}) as response:
            self.assertEqual(response.status, 201)
        async with self.session.get(self.base + "/streamer/api/communities") as response:
            self.assertEqual((await response.json())["communities"], [{"chat_id": -1001, "title": "Group", "chat_type": "supergroup"}])
        self.user_admin = False
        async with self.session.get(self.base + "/streamer/api/communities") as response:
            self.assertEqual((await response.json())["communities"], [])

    async def test_template_requires_signed_owner_plus_and_fresh_community_rights(self):
        template_url = self.base + "/streamer/api/templates/-1001"
        payload = {"version": 0, "headline": "Live", "body": "Join",
                   "buttons": [{"label": "Site", "url": "https://example.com/"}]}
        async with self.session.get(template_url) as response:
            self.assertEqual(response.status, 401)
        async with self.session.put(template_url, json=payload) as response:
            self.assertEqual(response.status, 401)
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(101)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
        async with self.session.put(template_url, json=payload) as response:
            self.assertEqual(response.status, 403)
        await self.db.issue_test_streamer_plus(
            "11", "grant-templates", starts_at=time.time() - 5,
            expires_at=time.time() + 600, issued_by=425785231,
        )
        await self.db.add_streamer_community(101, -1001, "Group", "supergroup")
        self.user_admin = False
        async with self.session.put(template_url, json=payload) as response:
            self.assertEqual(response.status, 403)
        self.user_admin = True
        async with self.session.put(template_url, json=payload) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["version"], 1)
        async with self.session.get(template_url) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["headline"], "Live")
        async with self.session.put(template_url, json=payload) as response:
            self.assertEqual(response.status, 409)
        async with self.session.put(template_url, json={**payload, "version": 1},
                                    headers={"Origin": "https://other.example"}) as response:
            self.assertEqual(response.status, 403)
        async with self.session.put(template_url, json={**payload, "version": 1,
                                                        "buttons": [{"label": "Bad", "url": "http://bad.example"}]}) as response:
            self.assertEqual(response.status, 400)
        async with self.session.put(template_url, json={**payload, "body": "x" * 5000}) as response:
            self.assertEqual(response.status, 413)
        self.user_admin = False
        async with self.session.get(template_url) as response:
            self.assertEqual(response.status, 403)
        self.user_admin = True
        async with self.session.post(self.base + "/streamer/logout", allow_redirects=False):
            pass
        async with self.session.get(template_url) as response:
            self.assertEqual(response.status, 401)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=100)
        await self.db.issue_test_streamer_plus(
            "22", "grant-other", starts_at=time.time() - 5,
            expires_at=time.time() + 600, issued_by=425785231,
        )
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(202)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
        async with self.session.get(template_url) as response:
            self.assertEqual(response.status, 403)

    async def test_stats_expose_only_signed_streamers_own_published_posts(self):
        await self.db.add_channel(-1001, "alpha")
        await self.db.set_live_state(-1001, "alpha", True, "s1", broadcaster_id="11")
        await self.db.set_live_message_if_current(-1001, "alpha", "s1", 701)
        stats_url = self.base + "/streamer/api/stats"
        async with self.session.get(stats_url) as response:
            self.assertEqual(response.status, 401)
        async with self.session.post(
            self.base + "/streamer/telegram-webapp",
            data={"init_data": signed_webapp(101)}, allow_redirects=False,
        ) as response:
            self.assertEqual(response.status, 303)
        async with self.session.get(stats_url) as response:
            self.assertEqual(response.status, 403)
        await self.db.issue_test_streamer_plus(
            "11", "grant-stats", starts_at=time.time() - 5,
            expires_at=time.time() + 600, issued_by=425785231,
        )
        async with self.session.get(stats_url) as response:
            self.assertEqual(response.status, 200)
            stats = await response.json()
            self.assertEqual(stats["published_posts"], 1)
            self.assertEqual(stats["period_days"], 30)
            self.assertNotIn("telegram_user_id", stats)


class StreamerRuntimeGateTests(unittest.TestCase):
    def test_only_pinned_staging_enables_streamer_web_on_railway(self):
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
            self.assertTrue(load_config().streamer_plus_enabled)
            self.assertTrue(load_config().viewer_plus_enabled)
            self.assertTrue(load_config().growth_enabled)
        with patch.dict(os.environ, {**base, "RAILWAY_ENVIRONMENT_NAME": "production"}, clear=True):
            self.assertFalse(load_config().streamer_plus_enabled)
            self.assertFalse(load_config().viewer_plus_enabled)
            self.assertFalse(load_config().growth_enabled)
        with patch.dict(os.environ, {**base, "RAILWAY_PROJECT_ID": "other"}, clear=True):
            self.assertFalse(load_config().streamer_plus_enabled)
            self.assertFalse(load_config().viewer_plus_enabled)
            self.assertFalse(load_config().growth_enabled)


if __name__ == "__main__":
    unittest.main()
