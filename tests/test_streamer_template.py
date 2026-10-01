import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database import Database
from bot.live_post import LivePostContent
from bot.streamer_template import validate_streamer_template
from bot.streamer_post import compose_streamer_post
from bot.poller import StreamPoller
from bot.twitch import TwitchClient


class StreamerTemplateValidationTests(unittest.TestCase):
    def test_safe_plain_text_and_two_https_buttons(self):
        template = validate_streamer_template(
            "Сейчас в эфире", "Заходи на трансляцию!",
            [{"label": "Сообщество", "url": "https://example.com/community"},
             {"label": "Расписание", "url": "https://example.org/schedule"}],
        )
        self.assertEqual(template.headline, "Сейчас в эфире")
        self.assertEqual(len(template.buttons), 2)

    def test_unsafe_urls_and_oversized_content_are_rejected(self):
        urls = (
            "http://example.com", "javascript:alert(1)", "https://localhost/path",
            "https://127.0.0.1/", "https://[::1]/", "https://user@example.com/",
            "https://example.com:8443/", "https://example.local/",
            "https://example.com/\n", "https://example.com@evil.test/",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_streamer_template("Live", "Body", [{"label": "Button", "url": url}])
        for headline, body, buttons in (
            ("", "Body", []), ("x" * 61, "Body", []),
            ("Live", "x" * 141, []), ("Live", "Body", [{}]),
            ("Live", "Body", [{"label": "x", "url": "https://example.com"}] * 3),
        ):
            with self.subTest(headline=headline, body=body), self.assertRaises(ValueError):
                validate_streamer_template(headline, body, buttons)
        with self.assertRaises(ValueError):
            validate_streamer_template("\ud800", "", [])

    def test_custom_text_is_escaped_and_twitch_button_remains(self):
        template = validate_streamer_template(
            "<Live & go>", "Watch <now>",
            [{"label": "Site", "url": "https://example.com/"}],
        )
        base = LivePostContent(
            html="<b>alpha</b> в эфире",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Twitch", url="https://twitch.tv/alpha")],
            ]),
        )
        result = compose_streamer_post(base, template)
        self.assertIn("&lt;Live &amp; go&gt;", result.html)
        self.assertIn("Watch &lt;now&gt;", result.html)
        self.assertIn("<b>alpha</b>", result.html)
        self.assertEqual(result.reply_markup.inline_keyboard[0][0].text, "Twitch")
        self.assertEqual(result.reply_markup.inline_keyboard[1][0].text, "Site")
        too_long = LivePostContent(html="x" * 1000, reply_markup=base.reply_markup)
        self.assertIs(compose_streamer_post(too_long, template), too_long)


class StreamerTemplateStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        await self.db.link_streamer_identity(202, "22", "beta", verified_at=50)
        await self.db.add_channel(-1001, "alpha")
        await self.db.issue_test_streamer_plus(
            "11", "grant", starts_at=100, expires_at=200, issued_by=425785231, now=90,
        )
        await self.db.add_streamer_community(101, -1001, "Group", "supergroup", now=150)

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_versioned_save_and_access_require_right_broadcaster_and_live_state(self):
        buttons = [{"label": "Site", "url": "https://example.com/"}]
        self.assertEqual(
            await self.db.save_streamer_template(
                101, -1001, expected_version=0,
                headline="Live", body="Join", buttons=buttons, now=150,
            ), 1,
        )
        self.assertIsNone(await self.db.save_streamer_template(
            101, -1001, expected_version=0,
            headline="Stale", body="", buttons=[], now=151,
        ))
        self.assertEqual(await self.db.get_streamer_template(101, -1001),
                         (1, "Live", "Join", buttons))
        self.assertIsNone(await self.db.get_streamer_template(202, -1001))
        self.assertIsNone(await self.db.get_active_streamer_template_for_destination(-1001, "alpha", now=150))
        await self.db.set_live_state(-1001, "alpha", True, "s1", broadcaster_id="11")
        template = await self.db.get_active_streamer_template_for_destination(-1001, "alpha", now=150)
        self.assertEqual(template.headline, "Live")
        await self.db.set_live_state(-1001, "alpha", True, "s2", broadcaster_id="22")
        self.assertIsNone(await self.db.get_active_streamer_template_for_destination(-1001, "alpha", now=150))
        await self.db.set_live_state(-1001, "alpha", True, "s3", broadcaster_id="11")
        self.assertIsNone(await self.db.get_active_streamer_template_for_destination(-1001, "alpha", now=200))

    async def test_queued_live_and_preview_use_only_matching_current_broadcaster(self):
        grant_id = await self.db.issue_test_streamer_plus(
            "11", "current-grant", starts_at=time.time() - 5,
            expires_at=time.time() + 600, issued_by=425785231,
        )
        await self.db.save_streamer_template(
            101, -1001, expected_version=0, headline="Plus <live>", body="Hello",
            buttons=[{"label": "Site", "url": "https://example.com/"}], now=150,
        )
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)))
        poller = StreamPoller(bot, self.db, SimpleNamespace(), 60, notification_queue_enabled=True)
        await self.db.set_live_state(-1001, "alpha", True, "s1", broadcaster_id="22")
        wrong = await poller._live_post_content(
            "alpha", "Title", "Game", 42, None, chat_id=-1001,
            include_track_link=False,
        )
        self.assertNotIn("Plus", wrong.html)
        await self.db.set_live_state(-1001, "alpha", True, "s2", broadcaster_id="11")
        current = await poller._live_post_content(
            "alpha", "Title", "Game", 42, None, chat_id=-1001,
            include_track_link=False,
        )
        self.assertIn("Plus &lt;live&gt;", current.html)
        self.assertEqual(current.reply_markup.inline_keyboard[-1][0].text, "Site")
        preview = await poller.build_preview_content(
            SimpleNamespace(twitch_login="alpha", title="Title", game_name="Game", viewer_count=42),
            SimpleNamespace(chat_id=-1001, include_track_link=False, last_stream_ended_at=None),
        )
        self.assertIn("Plus &lt;live&gt;", preview.html)
        self.assertTrue(await self.db.revoke_test_streamer_plus(
            grant_id, issued_by=425785231, revoked_at=time.time(),
        ))
        revoked = await poller._live_post_content(
            "alpha", "Title", "Game", 42, None, chat_id=-1001,
            include_track_link=False,
        )
        self.assertNotIn("Plus", revoked.html)
        await self.db.set_live_state(-1001, "alpha", False, "s2", broadcaster_id="11")
        ended = await poller._live_post_content(
            "alpha", "Title", "Game", 42, None, chat_id=-1001,
            include_track_link=False,
        )
        self.assertNotIn("Plus", ended.html)

    async def test_two_communities_keep_independent_templates_and_versions(self):
        await self.db.add_channel(-1003, "alpha")
        await self.db.add_streamer_community(101, -1003, "Second", "supergroup", now=150)
        self.assertEqual(await self.db.save_streamer_template(
            101, -1001, expected_version=0, headline="First", body="", buttons=[], now=150,
        ), 1)
        self.assertEqual(await self.db.save_streamer_template(
            101, -1003, expected_version=0, headline="Second", body="", buttons=[], now=150,
        ), 1)
        self.assertEqual(await self.db.save_streamer_template(
            101, -1001, expected_version=1, headline="First v2", body="", buttons=[], now=151,
        ), 2)
        self.assertEqual((await self.db.get_streamer_template(101, -1001))[1], "First v2")
        self.assertEqual((await self.db.get_streamer_template(101, -1003))[1], "Second")


class TwitchBroadcasterParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_helix_user_id_is_kept_for_live_post_authorization(self):
        twitch = TwitchClient("client", "secret", SimpleNamespace())
        twitch._request = AsyncMock(return_value={"data": [{
            "user_login": "Alpha", "user_id": "11", "id": "stream-1",
            "title": "Live", "game_name": "Game", "viewer_count": 42,
            "started_at": "2026-01-01T00:00:00Z",
        }]})
        streams = await twitch.get_live_streams(["alpha"])
        self.assertEqual(streams["alpha"].broadcaster_id, "11")


if __name__ == "__main__":
    unittest.main()
