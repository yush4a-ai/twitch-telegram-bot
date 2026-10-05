"""Переписка: какие сообщения попадают в чат панели, а какие нет."""
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.media_store import MAX_IMAGE_BYTES
from bot.middlewares import DialogueMiddleware

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeTelegram:
    """Подделка клиента: настоящий Bot в тестах не создаётся (это проверяет изоляция)."""

    def __init__(self):
        self.downloaded = 0

    async def download(self, file, destination):
        self.downloaded += 1
        destination.write(PNG)


def message(*, chat_type="private", text="Привет", caption=None,
            user_id=777010, message_id=5, photo=None):
    return SimpleNamespace(
        chat=SimpleNamespace(type=chat_type),
        from_user=SimpleNamespace(id=user_id),
        text=text, caption=caption, message_id=message_id, photo=photo,
    )


class DialogueMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_text_becomes_a_dialogue_message(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        result = await DialogueMiddleware(db)(handler, message(), {})
        self.assertEqual(result, "ok")
        db.record_dialogue_message.assert_awaited_once()
        args, kwargs = db.record_dialogue_message.call_args
        self.assertEqual(args[0], 777010)
        self.assertEqual(args[1], "in")
        self.assertEqual(args[2], "Привет")
        self.assertEqual(kwargs["telegram_message_id"], 5)

    async def test_commands_are_not_part_of_the_conversation(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        await DialogueMiddleware(db)(handler, message(text="/start"), {})
        db.record_dialogue_message.assert_not_awaited()
        self.assertEqual(handler.await_count, 1)

    async def test_group_and_channel_messages_are_ignored(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        for chat_type in ("group", "supergroup", "channel"):
            await DialogueMiddleware(db)(handler, message(chat_type=chat_type), {})
        db.record_dialogue_message.assert_not_awaited()

    async def test_empty_message_is_not_stored(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        await DialogueMiddleware(db)(handler, message(text="   "), {})
        db.record_dialogue_message.assert_not_awaited()

    async def test_caption_is_stored_when_there_is_no_text(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        await DialogueMiddleware(db)(handler, message(text=None, caption="Подпись"), {})
        self.assertEqual(
            db.record_dialogue_message.call_args[0][2], "Подпись")

    async def test_photo_without_caption_is_still_a_message(self):
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        await DialogueMiddleware(db)(
            handler, message(text=None, photo=[SimpleNamespace(file_id="f")]), {})
        db.record_dialogue_message.assert_awaited_once()
        # Текста нет — в переписке это показывается как вложение.
        self.assertIsNone(db.record_dialogue_message.call_args[0][2])

    async def test_photo_is_downloaded_when_a_media_directory_is_configured(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        media = pathlib.Path(tmp.name) / "media"
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        bot = FakeTelegram()

        await DialogueMiddleware(db, media_dir=media)(
            handler,
            message(text=None, photo=[SimpleNamespace(file_id="f", file_size=100)]),
            {"bot": bot},
        )

        stored = db.record_dialogue_message.call_args.kwargs["image_path"]
        self.assertTrue(stored and pathlib.Path(stored).exists())
        self.assertEqual(bot.downloaded, 1)

    async def test_oversized_photo_is_not_downloaded(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        media = pathlib.Path(tmp.name) / "media"
        db = SimpleNamespace(record_dialogue_message=AsyncMock())
        handler = AsyncMock(return_value="ok")
        bot = FakeTelegram()

        await DialogueMiddleware(db, media_dir=media)(
            handler,
            message(text=None, photo=[
                SimpleNamespace(file_id="f", file_size=MAX_IMAGE_BYTES + 1)]),
            {"bot": bot},
        )

        self.assertIsNone(db.record_dialogue_message.call_args.kwargs["image_path"])
        self.assertEqual(bot.downloaded, 0)

    async def test_storage_failure_does_not_break_the_answer(self):
        db = SimpleNamespace(
            record_dialogue_message=AsyncMock(side_effect=RuntimeError("db")))
        handler = AsyncMock(return_value="ok")
        result = await DialogueMiddleware(db)(handler, message(), {})
        self.assertEqual(result, "ok")
        self.assertEqual(handler.await_count, 1)


if __name__ == "__main__":
    unittest.main()
