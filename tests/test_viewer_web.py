import os
import tempfile
import time
import unittest

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, OWNER_ID, signed_webapp


class ViewerWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(202, "beta")
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            viewer_db=self.db, viewer_bot_token=BOT_TOKEN,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()
        await self.db.close()
        self.directory.cleanup()

    def request(self, path, user_id=101, **fields):
        return self.session.post(
            self.base + path,
            json={"init_data": signed_webapp(user_id), **fields},
        )

    async def test_direct_url_and_forged_identity_never_expose_settings_or_admin(self):
        async with self.session.get(self.base + "/viewer") as response:
            self.assertEqual(response.status, 200)
            shell = await response.text()
            self.assertNotIn("Админ-панель", shell)
            self.assertNotIn("alpha", shell)
        for asset in ("app.js", "app.css"):
            with self.subTest(asset=asset):
                async with self.session.get(self.base + "/viewer/" + asset) as response:
                    self.assertEqual(response.status, 200)
                    self.assertNotIn("Админ-панель", await response.text())
        async with self.session.post(self.base + "/viewer/api/state", json={}) as response:
            self.assertEqual(response.status, 401)
        async with self.session.get(self.base + "/viewer/api/state") as response:
            self.assertEqual(response.status, 405)
        forged = signed_webapp(101) + "&user=%7B%22id%22%3A202%7D"
        async with self.session.post(
            self.base + "/viewer/api/state", json={"init_data": forged},
        ) as response:
            self.assertEqual(response.status, 403)
        stale = signed_webapp(101, auth_date=int(time.time()) - 601)
        async with self.session.post(
            self.base + "/viewer/api/state", json={"init_data": stale},
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.session.post(
            self.base + "/viewer/api/state",
            data='{"init_data":"one","init_data":"two"}',
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.request("/viewer/api/state", 202) as response:
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual([row["login"] for row in payload["subscriptions"]], ["beta"])
            self.assertNotIn("alpha", str(payload))
            self.assertNotIn("admin", str(payload).lower())
        async with self.request("/viewer/api/state", OWNER_ID) as response:
            self.assertEqual(response.status, 200)
            self.assertNotIn("Админ-панель", str(await response.json()))

    async def test_notify_filter_and_digest_use_one_private_database(self):
        async with self.request(
            "/viewer/api/notify", login="alpha", enabled=False,
        ) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse(await self.db.get_notify_enabled(101, "alpha"))
        async with self.request(
            "/viewer/api/notify", login="beta", enabled=False,
        ) as response:
            self.assertEqual(response.status, 404)
        async with self.request(
            "/viewer/api/filter", 202, login="alpha", expected_version=0,
            games=[], title_keywords=[], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 404)
        self.assertTrue(await self.db.get_notify_enabled(202, "beta"))
        async with self.request(
            "/viewer/api/filter", login="alpha", expected_version=0,
            games=["Minecraft"], title_keywords=[], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 403)
        now = time.time()
        await self.db.issue_test_viewer_plus(
            101, "viewer-web", starts_at=now - 10, expires_at=now + 3600,
            issued_by=OWNER_ID, now=now,
        )
        async with self.request(
            "/viewer/api/filter", login="alpha", expected_version=0,
            games=["Minecraft"], title_keywords=["speedrun"], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["version"], 1)
        async with self.request(
            "/viewer/api/filter", login="alpha", expected_version=0,
            games=[], title_keywords=[], exclude_keywords=[],
        ) as response:
            self.assertEqual(response.status, 409)
        async with self.request("/viewer/api/state") as response:
            state = await response.json()
            self.assertTrue(state["plus_active"])
            self.assertEqual(state["subscriptions"][0]["filter"]["games"], ["Minecraft"])
        await self.db.set_quiet_hours(101, 100, 200, 0)
        async with self.request("/viewer/api/digest", enabled=False) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse((await self.db.get_quiet_hours(101))[3])
        await self.db.set_quiet_hours_notify_after(101, True)
        async with self.request("/viewer/api/state") as response:
            state = await response.json()
            self.assertTrue(state["digest_enabled"])


if __name__ == "__main__":
    unittest.main()
