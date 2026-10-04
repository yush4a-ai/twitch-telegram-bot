"""Free viewer journey through the signed Mini App and the bot's shared database."""

import os
import tempfile
import time
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from bot.database import Database
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class FakeTwitch:
    def __init__(self):
        self.existing = {"alpha", "beta", "gamma"}
        self.searches = []

    async def channel_exists(self, login):
        return login in self.existing

    async def search_channels(self, query, limit=6):
        self.searches.append(query)
        return [SimpleNamespace(login=query, display_name=query.title(), is_live=False)] if query in self.existing else []


class MiniAppViewerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.twitch = FakeTwitch()
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            mini_app_db=self.db, mini_app_bot_token=BOT_TOKEN,
            mini_app_twitch=self.twitch,
        )
        await self.server.start()
        self.session = aiohttp.ClientSession()
        self.base = f"http://127.0.0.1:{self.server._runner.addresses[0][1]}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    def request(self, action, actor_id=101, **fields):
        return self.session.post(
            f"{self.base}/app/api/viewer/{action}",
            json={"init_data": signed_webapp(actor_id), **fields},
        )

    async def test_search_and_follow_normalize_nick_and_twitch_link(self):
        for query in ("ALPHA", "https://www.twitch.tv/ALPHA?ref=share", "twitch.tv/ALPHA"):
            with self.subTest(query=query):
                async with self.request("search", query=query) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())["results"][0]["login"], "alpha")
        self.assertEqual(self.twitch.searches, ["alpha", "alpha", "alpha"])
        async with self.request("follow", login="https://twitch.tv/ALPHA") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["result"], "created")
        async with self.request("follow", login="alpha") as response:
            self.assertEqual((await response.json())["result"], "already")
        self.assertEqual(await self.db.list_channels(101), ["alpha"])
        for bad in ("https://evil.example/alpha", "https://twitch.tv@evil.example/alpha", "https://twitch.tv/alpha/more"):
            async with self.request("search", query=bad) as response:
                self.assertEqual(response.status, 400)

    async def test_bot_and_app_see_same_private_rows_and_permissions(self):
        await self.db.add_channel(101, "alpha")  # bot-side update
        await self.db.add_channel(202, "beta")
        async with self.request("state", 202, user_id=101) as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertEqual([row["login"] for row in state["subscriptions"]], ["beta"])
            self.assertEqual(state["channel_limit"], 50)
            self.assertNotIn("alpha", str(state))
        async with self.request("state") as response:
            self.assertEqual([row["login"] for row in (await response.json())["subscriptions"]], ["alpha"])
        async with self.request("follow", login="gamma") as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.list_channels(101), ["alpha", "gamma"])
        async with self.request("notify", login="beta", enabled=False) as response:
            self.assertEqual(response.status, 404)
        async with self.request("unfollow", login="beta") as response:
            self.assertEqual(response.status, 404)

    async def test_notify_pause_keeps_follow_and_unfollow_cleans_filter(self):
        await self.db.add_channel(101, "alpha")
        async with self.request("notify", login="alpha", enabled=False) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.list_channels(101), ["alpha"])
        self.assertFalse(await self.db.get_notify_enabled(101, "alpha"))
        await self.db.conn.execute(
            "INSERT INTO viewer_alert_filters (telegram_user_id, twitch_login, version, games_json, title_keywords_json, exclude_keywords_json, updated_at) VALUES (?, ?, 1, '[]', '[]', '[]', ?)",
            (101, "alpha", time.time()),
        )
        await self.db.conn.commit()
        async with self.request("unfollow", login="alpha") as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(await self.db.list_channels(101), [])
        self.assertIsNone(await self.db.get_viewer_filter(101, "alpha"))
        async with self.request("notify", login="alpha", enabled=True) as response:
            self.assertEqual(response.status, 404)

    async def test_unsigned_mutation_and_unknown_channel_are_rejected(self):
        async with self.session.post(self.base + "/app/api/viewer/follow", json={"login": "alpha"}) as response:
            self.assertEqual(response.status, 401)
        async with self.request("follow", login="unknown") as response:
            self.assertEqual(response.status, 404)
        self.assertEqual(await self.db.list_channels(101), [])

    async def test_stale_live_marker_is_not_presented_as_current(self):
        await self.db.add_channel(101, "alpha")
        await self.db.conn.execute(
            "UPDATE tracked_channels SET is_live=1,last_seen_live_at=? WHERE chat_id=? AND twitch_login=?",
            (time.time() - 900, 101, "alpha"),
        )
        await self.db.conn.commit()
        async with self.request("state") as response:
            self.assertEqual((await response.json())["subscriptions"][0]["status"], "stale")
        await self.db.conn.execute(
            "UPDATE tracked_channels SET last_seen_live_at=? WHERE chat_id=? AND twitch_login=?",
            (time.time(), 101, "alpha"),
        )
        await self.db.conn.commit()
        async with self.request("state") as response:
            self.assertEqual((await response.json())["subscriptions"][0]["status"], "live")

    async def test_free_limit_applies_even_when_follow_endpoint_is_called_directly(self):
        for index in range(50):
            await self.db.add_channel(101, f"test{index:02}")
        async with self.request("follow", login="gamma") as response:
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["error"], "channel_limit")
        self.assertEqual(len(await self.db.list_channels(101)), 50)

    async def test_search_rate_limit_is_per_verified_user(self):
        for _ in range(6):
            async with self.request("search", query="alpha") as response:
                self.assertEqual(response.status, 200)
        async with self.request("search", query="alpha") as response:
            self.assertEqual(response.status, 429)
        async with self.request("search", 202, query="alpha") as response:
            self.assertEqual(response.status, 200)

    async def test_full_list_never_spends_twitch_lookup_budget(self):
        for index in range(50):
            await self.db.add_channel(101, f'full{index:02}')
        self.twitch.channel_exists = AsyncMock(return_value=True)
        async with self.request('follow', login='alpha') as response:
            self.assertEqual(response.status, 409)
        self.twitch.channel_exists.assert_not_awaited()

    async def test_follow_burst_has_user_and_global_bounds(self):
        self.twitch.channel_exists = AsyncMock(return_value=False)
        for index in range(20):
            async with self.request('follow', login=f'unknown{index}') as response:
                self.assertEqual(response.status, 404 if index < 6 else 429)
        self.assertEqual(self.twitch.channel_exists.await_count, 6)
        for index in range(50):
            async with self.request('follow', actor_id=200+index, login=f'other{index}') as response:
                self.assertIn(response.status, (404, 429))
        self.assertLessEqual(self.twitch.channel_exists.await_count, 30)

    async def test_concurrent_identical_negative_lookup_is_shared_and_cached(self):
        async def missing(login):
            await asyncio.sleep(.05)
            return False
        self.twitch.channel_exists = AsyncMock(side_effect=missing)
        async def follow(actor):
            async with self.request('follow', actor_id=actor, login='missing') as response:
                return response.status
        self.assertEqual(await asyncio.gather(*(follow(300+i) for i in range(6))), [404]*6)
        self.assertEqual(self.twitch.channel_exists.await_count, 1)
        self.assertEqual(await follow(400), 404)
        self.assertEqual(self.twitch.channel_exists.await_count, 1)

    async def test_follow_gate_does_not_replace_shared_twitch_client(self):
        self.twitch.channel_exists = AsyncMock(return_value=False)
        for index in range(7):
            async with self.request('follow', login=f'unknown{index}') as response:
                await response.read()
        # A poller-side call uses the unchanged client, outside the route's gate.
        self.assertFalse(await self.twitch.channel_exists('pollerlogin'))
        self.assertEqual(self.twitch.channel_exists.await_count, 7)

    async def test_state_names_are_public_bounded_cached_and_fallback_to_real_login(self):
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(101, "beta")
        await self.db.add_channel(202, "gamma")
        self.twitch.get_display_names = AsyncMock(return_value={
            "alpha": "Очень длинное русское имя стримера с несколькими словами",
            "beta": "x" * 257, "gamma": "Чужой стример",
        })
        async with self.request("state") as response:
            rows = (await response.json())["subscriptions"]
        self.assertEqual(rows[0]["display_name"], "Очень длинное русское имя стримера с несколькими словами")
        self.assertEqual(rows[1]["display_name"], "beta")
        self.twitch.get_display_names.assert_awaited_once_with(["alpha", "beta"])
        async with self.request("state") as response:
            self.assertEqual((await response.json())["subscriptions"], rows)
        self.assertEqual(self.twitch.get_display_names.await_count, 1)
        async with self.request("state", 202) as response:
            self.assertEqual((await response.json())["subscriptions"][0]["display_name"], "Чужой стример")
        self.twitch.get_display_names.assert_awaited_with(["gamma"])

    async def test_name_cache_ttl_bound_concurrent_lookup_and_timeout(self):
        from bot.mini_app_viewer import _PublicNames
        clock = [10.0]
        fake = SimpleNamespace(get_display_names=AsyncMock(side_effect=lambda logins: {login: login.upper() for login in logins}))
        names = _PublicNames(fake, clock=lambda: clock[0])
        first, second = await asyncio.gather(names.get(["alpha"]), names.get(["alpha"]))
        self.assertEqual(first, {"alpha": "ALPHA"}); self.assertEqual(first, second)
        self.assertEqual(fake.get_display_names.await_count, 1)
        clock[0] += 301
        self.assertEqual(await names.get(["alpha"]), first)
        self.assertEqual(fake.get_display_names.await_count, 2)
        await names.get([f"name{i}" for i in range(2001)])
        self.assertLessEqual(len(names.entries), 2000)
        async def blocked(_logins):
            await asyncio.sleep(1)
        fake.get_display_names.side_effect = blocked
        with patch("bot.mini_app_viewer._NAMES_TIMEOUT", .01):
            self.assertEqual(await names.get(["offline"]), {"offline": "offline"})
        fake.get_display_names.side_effect = RuntimeError("metadata unavailable")
        self.assertEqual(await names.get(["unavailable"]), {"unavailable": "unavailable"})
