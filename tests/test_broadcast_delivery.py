"""Отправка рассылки и ответов: выбор способа и обход лимита подписи к фото.

У подписи к фото в Telegram лимит 1024 символа, а текст сообщения может быть
до 4096. Если это не учитывать, длинная рассылка с картинкой не уйдёт вообще.
"""
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from main import (
    TELEGRAM_CAPTION_LIMIT,
    _make_broadcast_sender,
    _make_chat_sender,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class BroadcastSenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = pathlib.Path(self.tmp.name) / "shot.png"
        self.image.write_bytes(PNG)
        self.bot = SimpleNamespace(
            send_photo=AsyncMock(), send_message=AsyncMock())

    def campaign(self, body: str, image: str | None) -> dict:
        return {
            "id": 1, "body": body, "image_path": image,
            "button_text": None, "button_url": None,
        }

    async def test_text_only_campaign_uses_a_message(self):
        await _make_broadcast_sender(self.bot)(777010, self.campaign("Привет", None))
        self.bot.send_message.assert_awaited_once()
        self.bot.send_photo.assert_not_awaited()

    async def test_short_campaign_with_image_uses_a_caption(self):
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign("Коротко", str(self.image)))
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(self.bot.send_photo.await_args.kwargs["caption"], "Коротко")
        self.bot.send_message.assert_not_awaited()

    async def test_long_campaign_with_image_is_split_instead_of_failing(self):
        long_body = "т" * (TELEGRAM_CAPTION_LIMIT + 500)
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign(long_body, str(self.image)))
        # Фото уходит без подписи, текст — отдельным сообщением с кнопками.
        self.bot.send_photo.assert_awaited_once()
        self.assertNotIn("caption", self.bot.send_photo.await_args.kwargs)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.kwargs["text"], long_body)

    async def test_long_answer_with_image_is_split_too(self):
        long_text = "о" * (TELEGRAM_CAPTION_LIMIT + 1)
        delivered = await _make_chat_sender(self.bot)(777010, long_text, str(self.image))
        self.assertTrue(delivered)
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.assert_awaited_once()

    async def test_short_answer_with_image_uses_a_caption(self):
        delivered = await _make_chat_sender(self.bot)(777010, "Ответ", str(self.image))
        self.assertTrue(delivered)
        self.assertEqual(self.bot.send_photo.await_args.kwargs["caption"], "Ответ")
        self.bot.send_message.assert_not_awaited()

    async def test_missing_image_file_falls_back_to_a_message(self):
        missing = str(pathlib.Path(self.tmp.name) / "нет-файла.png")
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign("Текст", missing))
        self.bot.send_photo.assert_not_awaited()
        self.bot.send_message.assert_awaited_once()

    async def test_text_keeps_the_owner_markup(self):
        # Владелец оформляет рассылку жирным и курсивом: разметка должна дойти.
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign("Спасибо <b>вам</b>", None))
        self.assertEqual(
            self.bot.send_message.await_args.kwargs.get("parse_mode"),
            ParseMode.HTML,
        )

    async def test_answer_keeps_the_owner_markup(self):
        await _make_chat_sender(self.bot)(777010, "Смотри <i>вот</i>", None)
        self.assertEqual(
            self.bot.send_message.await_args.kwargs.get("parse_mode"),
            ParseMode.HTML,
        )

    async def test_broken_markup_still_reaches_the_person(self):
        # «<3» ломает разметку: второй попыткой текст уходит как есть.
        self.bot.send_message.side_effect = [
            TelegramBadRequest(method=None, message="can't parse entities"),
            None,
        ]
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign("Спасибо <3", None))
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertIsNone(
            self.bot.send_message.await_args.kwargs.get("parse_mode"))

    async def test_broken_markup_in_a_photo_caption_also_falls_back(self):
        self.bot.send_photo.side_effect = [
            TelegramBadRequest(method=None, message="can't parse entities"),
            None,
        ]
        await _make_broadcast_sender(self.bot)(
            777010, self.campaign("Спасибо <3", str(self.image)))
        self.assertEqual(self.bot.send_photo.await_count, 2)
        self.assertIsNone(self.bot.send_photo.await_args.kwargs.get("parse_mode"))


if __name__ == "__main__":
    unittest.main()
