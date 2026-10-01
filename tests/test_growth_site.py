import re
import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer
from tests.test_admin_telegram_auth import BOT_TOKEN


class GrowthSiteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = None
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        if self.server is not None:
            await self.server.stop()

    async def start(self, *, username=None, owner=False):
        access = AdminAccess(
            "emergency-key-" + "x" * 32, enabled=True, owner_id=425785231,
            bot_token=BOT_TOKEN, bot_username="TwitchSignalTestbot",
            public_base_url="https://staging.example.test",
        ) if owner else None
        self.server = OAuthCallbackServer(
            "https://staging.example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=access, growth_bot_username=username,
            growth_public_base_url="https://staging.example.test" if username else None,
        )
        await self.server.start()
        port = self.server._runner.addresses[0][1]
        return f"http://127.0.0.1:{port}"

    async def test_site_is_absent_without_stage_binding(self):
        base = await self.start()
        for path in ("/site", "/site/for-viewers", "/site/site.css", "/site/demo-poster.png", "/site/demo-landscape.mp4", "/robots.txt"):
            with self.subTest(path=path):
                async with self.session.get(base + path) as response:
                    self.assertEqual(response.status, 404)

    async def test_only_testbot_can_mount_site(self):
        self.server = OAuthCallbackServer(
            "https://staging.example.test/twitch/callback", "127.0.0.1", 0,
            growth_bot_username="TwitchSignalBot",
            growth_public_base_url="https://staging.example.test",
        )
        with self.assertRaises(ValueError):
            await self.server.start()

    async def test_four_pages_have_unique_server_html_and_safe_metadata(self):
        base = await self.start(username="TwitchSignalTestbot", owner=True)
        titles = set()
        descriptions = set()
        for path in ("/site", "/site/for-viewers", "/site/for-streamers", "/site/help"):
            with self.subTest(path=path):
                async with self.session.get(base + path) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                    self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
                    self.assertIn("font-src 'self'", response.headers["Content-Security-Policy"])
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    html = await response.text()
                self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
                self.assertIn(
                    f'<link rel="canonical" href="https://staging.example.test{path}">', html,
                )
                self.assertEqual(len(re.findall(r"<h1(?:\s|>)", html)), 1)
                title = re.search(r"<title>(.*?)</title>", html).group(1)
                description = re.search(r'<meta name="description" content="(.*?)">', html).group(1)
                self.assertTrue(title and description)
                titles.add(title)
                descriptions.add(description)
                self.assertIn("https://t.me/TwitchSignalTestbot?start=src_site", html)
                self.assertNotIn("Админ-панель", html)
                self.assertNotIn("/admin", html)
                self.assertNotIn("мгновенно", html.lower())
                self.assertNotIn("aggregateRating", html)
                self.assertNotIn("Offers", html)
                self.assertIn('type="application/ld+json"', html)
        self.assertEqual(len(titles), 4)
        self.assertEqual(len(descriptions), 4)
        async with self.session.get(base + "/site") as response:
            home = await response.text()
        for path in ("/site/for-viewers", "/site/for-streamers", "/site/help"):
            self.assertIn(f'href="{path}"', home)
        self.assertIn("Я зритель", home)
        self.assertIn("Я стример", home)
        async with self.session.get(base + "/admin/api/snapshot") as response:
            self.assertEqual(response.status, 401)

    async def test_robots_noindex_assets_and_feature_truth(self):
        base = await self.start(username="TwitchSignalTestbot")
        async with self.session.get(base + "/robots.txt") as response:
            self.assertEqual(response.status, 200)
            robots = await response.text()
            self.assertIn("Allow: /site", robots)
            self.assertNotIn("Disallow: /site", robots)
        for path, content_type in (
            ("/site/site.css", "text/css"),
            ("/site/brand-logo.png", "image/png"),
            ("/site/golos-cyrillic.woff2", "font/woff2"),
            ("/site/golos-latin.woff2", "font/woff2"),
        ):
            with self.subTest(path=path):
                async with self.session.get(base + path) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        async with self.session.get(base + "/site/for-viewers") as response:
            viewer = await response.text()
        self.assertIn("при начале эфира", viewer)
        self.assertIn("смена игры", viewer.lower())
        async with self.session.get(base + "/site/for-streamers") as response:
            streamer = await response.text()
        self.assertIn("тестовый plus", streamer.lower())

    async def test_video_is_user_controlled_and_supports_partial_load(self):
        base = await self.start(username="TwitchSignalTestbot")
        async with self.session.get(base + "/site") as response:
            html = await response.text()
        self.assertIn('poster="/site/demo-poster.png"', html)
        self.assertIn('src="/site/demo-landscape.mp4"', html)
        self.assertRegex(html, r"<video[^>]+controls")
        self.assertIn('preload="none"', html)
        self.assertNotIn("autoplay", html)
        self.assertIn("синтетическое демо", html.lower())
        for path, content_type in (
            ("/site/demo-poster.png", "image/png"),
            ("/site/demo-landscape.mp4", "video/mp4"),
            ("/site/demo-portrait.mp4", "video/mp4"),
        ):
            with self.subTest(path=path):
                async with self.session.get(base + path, headers={"Range": "bytes=0-15"}) as response:
                    self.assertEqual(response.status, 206)
                    self.assertIn(content_type, response.headers["Content-Type"])
                    self.assertTrue(response.headers["Content-Range"].startswith("bytes 0-15/"))
                    self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
                    self.assertEqual(len(await response.read()), 16)


if __name__ == "__main__":
    unittest.main()
