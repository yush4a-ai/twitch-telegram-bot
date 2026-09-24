from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unit-test-token-never-used")
os.environ.setdefault("TWITCH_CLIENT_ID", "unit-test-client")
os.environ.setdefault("TWITCH_CLIENT_SECRET", "unit-test-secret")

from bot.database import Database
from bot.poller import OFFLINE_GRACE_SECONDS, StreamPoller
from bot.stream_thumbnail import build_url
from bot.twitch import StreamInfo, TwitchClient


def _stream(*, viewers: int = 70) -> StreamInfo:
    return StreamInfo(
        user_login="channel",
        stream_id="stream-1",
        title="Test stream",
        game_name="Rust",
        viewer_count=viewers,
        started_at="2026-01-01T00:00:00Z",
        thumbnail_url=(
            "https://static-cdn.jtvnw.net/previews-ttv/"
            "live_user_channel-{width}x{height}.jpg"
        ),
    )


class ThumbnailUrlTests(unittest.TestCase):
    def test_build_url_is_640x360_and_bucket_stable(self) -> None:
        raw = (
            "https://static-cdn.jtvnw.net/previews-ttv/"
            "live_user_channel-{width}x{height}.jpg"
        )
        first = build_url(raw, 123)
        second = build_url(raw, 123)
        self.assertEqual(first, second)
        self.assertIn("640x360.jpg", first)
        self.assertIn("_tsb=123", first)

    def test_invalid_or_incomplete_url_is_ignored(self) -> None:
        self.assertIsNone(build_url(None, 1))
        self.assertIsNone(build_url("not-a-url-{width}x{height}", 1))
        self.assertIsNone(build_url("https://example.test/{unknown}.jpg", 1))


class TwitchThumbnailParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_response_reuses_existing_request_and_keeps_thumbnail(self) -> None:
        client = TwitchClient("client", "dummy", SimpleNamespace())
        client._request = AsyncMock(return_value={
            "data": [{
                "user_login": "Channel",
                "id": "stream-1",
                "title": "Title",
                "game_name": "Rust",
                "viewer_count": 70,
                "started_at": "2026-01-01T00:00:00Z",
                "thumbnail_url": "https://cdn/{width}x{height}.jpg",
            }]
        })

        streams = await client.get_live_streams(["channel"])

        self.assertEqual(client._request.await_count, 1)
        self.assertEqual(
            streams["channel"].thumbnail_url,
            "https://cdn/{width}x{height}.jpg",
        )


class PrivateThumbnailLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=701)),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(return_value=SimpleNamespace()),
            delete_message=AsyncMock(),
        )
        self.twitch = SimpleNamespace(get_live_streams=AsyncMock())
        self.poller = StreamPoller(self.bot, self.db, self.twitch, 60)
        self.poller._maybe_snapshot_followers = AsyncMock()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.directory.cleanup()

    async def _track(self, chat_id: int) -> None:
        await self.db.add_channel(chat_id, "channel")
        await self.db.set_display_name("channel", "Channel")

    async def _poll(self, chat_id: int, now: float, stream=None) -> None:
        self.twitch.get_live_streams.return_value = (
            {} if stream is None else {"channel": stream}
        )
        with patch("bot.poller.time.time", return_value=now):
            await self.poller._check_streams()

    async def _kind(self, chat_id: int) -> str:
        state = await self.db.get_live_post_state(chat_id, "channel")
        self.assertIsNotNone(state)
        return state.message_kind

    async def test_private_initial_post_upgrades_to_photo_without_download(self) -> None:
        await self._track(101)
        await self._poll(101, 1000.0, _stream())

        self.assertEqual(await self._kind(101), "photo")
        self.bot.send_message.assert_awaited_once()
        self.bot.edit_message_media.assert_awaited_once()
        media = self.bot.edit_message_media.await_args.kwargs["media"]
        self.assertIn("640x360.jpg", media.media)
        self.assertIn("<b>Channel</b>", media.caption)
        self.assertIn("«<a href=", media.caption)
        self.assertIn("в эфире", media.caption)

    async def test_media_refresh_is_not_repeated_inside_five_minutes(self) -> None:
        await self._track(101)
        await self._poll(101, 1000.0, _stream())
        self.bot.edit_message_media.reset_mock()

        await self._poll(101, 1100.0, _stream(viewers=71))

        self.bot.edit_message_media.assert_not_awaited()
        self.bot.edit_message_caption.assert_awaited()
    async def test_media_refresh_runs_again_after_five_minutes(self) -> None:
        await self._track(101)
        await self._poll(101, 1000.0, _stream())
        self.bot.edit_message_media.reset_mock()
        await self._poll(101, 1301.0, _stream(viewers=72))
        self.bot.edit_message_media.assert_awaited_once()

    async def test_group_does_not_get_thumbnail_media(self) -> None:
        await self._track(-100123)
        await self._poll(-100123, 1000.0, _stream())
        self.bot.edit_message_media.assert_not_awaited()
        self.assertEqual(await self._kind(-100123), "text")

    async def test_private_offline_keeps_photo_and_edits_ended_caption(self) -> None:
        await self._track(101)
        await self._poll(101, 1000.0, _stream())
        await self._poll(101, 1100.0, None)
        self.bot.edit_message_caption.reset_mock()

        finished_at = 1100.0 + OFFLINE_GRACE_SECONDS + 1
        with patch("bot.poller.time.time", return_value=finished_at):
            await self.poller._cleanup_offline_posts()

        self.bot.delete_message.assert_not_awaited()
        self.bot.edit_message_caption.assert_awaited()
        kwargs = self.bot.edit_message_caption.await_args.kwargs
        self.assertIn("завершил эфир", kwargs["caption"])
        self.assertNotIn("зрителей", kwargs["caption"])
        self.assertEqual(await self._kind(101), "photo")


if __name__ == "__main__":
    unittest.main()
