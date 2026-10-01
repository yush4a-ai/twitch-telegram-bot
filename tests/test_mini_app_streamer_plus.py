"""Streamer Plus stays bound to the verified broadcaster and community."""

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web

from bot.database import Database
from bot.live_post import (
    LivePostContent, LivePostMediaStatus, LivePostTarget,
    LivePostUpdater, TelegramAnimation,
)
from bot.mini_app_web import install_mini_app_routes
from bot.poller import StreamPoller
from bot.preview_runtime import PreviewManager
from tests.test_admin_telegram_auth import BOT_TOKEN, signed_webapp


class MiniAppStreamerPlusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=100)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=100)
        await self.db.add_streamer_community(101, -1001, "Alpha group", "supergroup")
        await self.db.add_channel(-1001, "alpha")
        self.bot = SimpleNamespace(
            id=999,
            get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup", title="Alpha group")),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=701)),
        )
        app = web.Application()
        install_mini_app_routes(app, self.db, BOT_TOKEN, bot=self.bot)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{self.runner.addresses[0][1]}"
        self.session = aiohttp.ClientSession()
        self.addAsyncCleanup(self.session.close)

    def request(self, path, actor=101, **fields):
        return self.session.post(
            self.base + path,
            json={"init_data": signed_webapp(actor), **fields},
        )

    async def grant(self):
        now = time.time()
        return await self.db.issue_test_streamer_plus(
            "11", f"t10-{now}", starts_at=now - 5, expires_at=now + 600,
            issued_by=425785231, now=now,
        )

    async def test_free_example_and_stats_are_not_paid_or_sent(self):
        async with self.request("/app/api/streamer/post-example", chat_id=-1001) as response:
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertFalse(data["custom_active"])
            self.assertFalse(data["animation_available"])
            self.assertIn("alpha", data["text"])
            self.assertEqual(data["buttons"][0]["url"], "https://twitch.tv/alpha")
        self.bot.send_message.assert_not_awaited()
        async with self.request("/app/api/streamer/template", chat_id=-1001) as response:
            self.assertEqual(response.status, 200)
            self.assertFalse((await response.json())["can_edit"])
        async with self.request(
            "/app/api/streamer/template", chat_id=-1001, version=0,
            headline="Plus", body="Only Plus", buttons=[],
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/streamer/stats") as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/streamer/preview", chat_id=-1001, enabled=True,
        ) as response:
            self.assertEqual(response.status, 403)

    async def test_plus_template_is_versioned_owned_validated_and_previewed(self):
        await self.grant()
        template = {
            "chat_id": -1001, "version": 0, "headline": "<Мой эфир>",
            "body": "Сейчас играем", "buttons": [
                {"label": "Сайт", "url": "https://example.com/watch"},
            ],
        }
        async with self.request("/app/api/streamer/template", **template) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["version"], 1)
        async with self.request("/app/api/streamer/template", **template) as response:
            self.assertEqual(response.status, 409)
        async with self.request("/app/api/streamer/template", actor=202, **template) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/streamer/template", **{**template, "chat_id": -9999},
        ) as response:
            self.assertEqual(response.status, 403)
        async with self.request(
            "/app/api/streamer/template", **{**template, "version": 1, "buttons": [
                {"label": "bad", "url": "http://localhost/"},
            ]},
        ) as response:
            self.assertEqual(response.status, 400)
        async with self.request("/app/api/streamer/template", chat_id=-1001) as response:
            saved = await response.json()
            self.assertTrue(saved["can_edit"])
            self.assertEqual(saved["headline"], "<Мой эфир>")
        async with self.request("/app/api/streamer/post-example", chat_id=-1001) as response:
            data = await response.json()
            self.assertTrue(data["custom_active"])
            self.assertIn("<Мой эфир>", data["text"])
            self.assertEqual([button["label"] for button in data["buttons"]], ["Смотреть на Twitch", "Сайт"])
        self.bot.send_message.assert_not_awaited()

    async def test_expiry_preserves_draft_but_uses_standard_and_disables_media(self):
        grant_id = await self.grant()
        async with self.request(
            "/app/api/streamer/template", chat_id=-1001, version=0,
            headline="Paid heading", body="Paid body", buttons=[],
        ) as response:
            self.assertEqual(response.status, 200)
        await self.db.set_live_state(-1001, "alpha", True, "stream-1", broadcaster_id="11")
        await self.db.set_live_message_if_current(-1001, "alpha", "stream-1", 701)
        async with self.request(
            "/app/api/streamer/preview", chat_id=-1001, enabled=True,
        ) as response:
            self.assertEqual(response.status, 200)
        before = await self.db.get_preview_destination_state(-1001, "alpha")
        self.assertTrue(PreviewManager._eligible(before))
        await self.db.revoke_test_streamer_plus(
            grant_id, issued_by=425785231, revoked_at=time.time(),
        )
        after = await self.db.get_preview_destination_state(-1001, "alpha")
        self.assertFalse(PreviewManager._eligible(after))
        async with self.request("/app/api/streamer/post-example", chat_id=-1001) as response:
            data = await response.json()
            self.assertFalse(data["custom_active"])
            self.assertFalse(data["animation_available"])
            self.assertNotIn("Paid heading", data["text"])
        async with self.request("/app/api/streamer/template", chat_id=-1001) as response:
            saved = await response.json()
            self.assertFalse(saved["can_edit"])
            self.assertEqual(saved["headline"], "Paid heading")
        await self.grant()
        renewed = await self.db.get_preview_destination_state(-1001, "alpha")
        self.assertTrue(PreviewManager._eligible(renewed))
        async with self.request("/app/api/streamer/post-example", chat_id=-1001) as response:
            self.assertTrue((await response.json())["custom_active"])
        self.bot.send_message.assert_not_awaited()

    async def test_stats_count_confirmed_posts_without_views(self):
        await self.grant()
        await self.db.set_live_state(-1001, "alpha", True, "stream-1", broadcaster_id="11")
        await self.db.set_live_message_if_current(-1001, "alpha", "stream-1", 701)
        async with self.request("/app/api/streamer/stats") as response:
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data["period_days"], 30)
            self.assertEqual(data["published_posts"], 1)
            self.assertEqual(data["connected_communities"], 1)
            self.assertNotIn("views", data)
        async with self.request("/app/api/streamer/stats", actor=202) as response:
            self.assertEqual(response.status, 403)

    async def test_presets_are_private_and_apply_requires_current_version_and_plus(self):
        async with self.request("/app/api/streamer/presets/create", name="Игры",
                                headline="Title", body="Body", buttons=[]) as response:
            self.assertEqual(response.status, 403)
        grant_id = await self.grant()
        async with self.request("/app/api/streamer/presets/create", name="Игры",
                                headline="Title", body="Body", buttons=[]) as response:
            self.assertEqual(response.status, 200)
            preset_id = (await response.json())["preset"]["id"]
        self.assertIsNone(await self.db.get_streamer_template(101, -1001))
        async with self.request("/app/api/streamer/presets", actor=202) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["presets"], [])
        async with self.request("/app/api/streamer/presets/apply", actor=202,
                                preset_id=preset_id, chat_id=-1001,
                                expected_version=0) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/streamer/presets/apply", preset_id=preset_id,
                                chat_id=-1001, expected_version=0) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["version"], 1)
        async with self.request("/app/api/streamer/presets/apply", preset_id=preset_id,
                                chat_id=-1001, expected_version=0) as response:
            self.assertEqual(response.status, 409)
        async with self.request("/app/api/streamer/stats/compare") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["period_days"], 7)
        await self.db.revoke_test_streamer_plus(
            grant_id, issued_by=425785231, revoked_at=time.time(),
        )
        async with self.request("/app/api/streamer/presets/apply", preset_id=preset_id,
                                chat_id=-1001, expected_version=1) as response:
            self.assertEqual(response.status, 403)
        async with self.request("/app/api/streamer/stats/compare") as response:
            self.assertEqual(response.status, 403)

    async def test_lost_community_rights_block_editor_example_and_media(self):
        await self.grant()
        self.bot.get_chat_member.return_value = SimpleNamespace(status="member")
        for path, fields in (
            ("/app/api/streamer/template", {"chat_id": -1001}),
            ("/app/api/streamer/post-example", {"chat_id": -1001}),
            ("/app/api/streamer/preview", {"chat_id": -1001, "enabled": True}),
        ):
            with self.subTest(path=path):
                async with self.request(path, **fields) as response:
                    self.assertEqual(response.status, 403)

    async def test_expiry_between_composition_and_send_rebuilds_free_content(self):
        grant_id = await self.grant()
        await self.db.save_streamer_template(
            101, -1001, expected_version=0, headline="Paid heading",
            body="Paid body", buttons=[],
        )
        await self.db.set_live_state(-1001, "alpha", True, "stream-1", broadcaster_id="11")
        poller = StreamPoller(
            self.bot, self.db, SimpleNamespace(), 60, notification_queue_enabled=True,
        )

        async def expire_then_send(send, _label):
            await self.db.revoke_test_streamer_plus(
                grant_id, issued_by=425785231, revoked_at=time.time(),
            )
            return await send()

        poller._tg_call = expire_then_send
        await poller._notify(-1001, "alpha", "Example title", 42, "Game")
        sent = self.bot.send_message.await_args.args[1]
        self.assertIn("alpha", sent)
        self.assertNotIn("Paid heading", sent)
        self.assertNotIn("Paid body", sent)

    async def test_expiry_after_render_before_animation_edit_sends_no_video(self):
        grant_id = await self.grant()
        await self.db.set_live_state(-1001, "alpha", True, "stream-1", broadcaster_id="11")
        await self.db.set_live_message_if_current(-1001, "alpha", "stream-1", 701)
        await self.db.set_preview_enabled(-1001, "alpha", True)
        self.bot.edit_message_media = AsyncMock(return_value=SimpleNamespace(animation=None))
        updater = LivePostUpdater(self.bot, self.db)

        async def render_content():
            await self.db.revoke_test_streamer_plus(
                grant_id, issued_by=425785231, revoked_at=time.time(),
            )
            return LivePostContent("Example", None)

        result = await updater.apply_animation(
            target=LivePostTarget(-1001, "alpha", "stream-1", 701),
            animation=TelegramAnimation("existing-file", 6),
            is_current_physical_stream=lambda: True,
            build_content=render_content,
        )
        self.assertEqual(result.status, LivePostMediaStatus.SKIPPED_DISABLED)
        self.bot.edit_message_media.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
