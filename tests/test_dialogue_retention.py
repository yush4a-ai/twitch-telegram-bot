"""Срок хранения переписки: старые сообщения и их файлы действительно удаляются."""
import os
import pathlib
import tempfile
import unittest

from bot.database import Database
from bot.dialogue_retention import purge_once
from bot.media_store import save_image

NOW = 1_700_000_000.0
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class DialogueRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = pathlib.Path(self.tmp.name) / "media"
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_old_messages_disappear_and_files_go_with_them(self):
        old_path = save_image(self.media, PNG, prefix="chat-1")
        fresh_path = save_image(self.media, PNG, prefix="chat-1")
        await self.db.record_dialogue_message(
            9001, "in", "Старое", now=NOW - 200 * 86400, image_path=old_path)
        await self.db.record_dialogue_message(
            9001, "in", "Новое", now=NOW, image_path=fresh_path)

        result = await purge_once(self.db, self.media, days=180.0, now=NOW)

        self.assertEqual(result["messages"], 1)
        self.assertEqual(result["files"], 1)
        self.assertFalse(pathlib.Path(old_path).exists())
        self.assertTrue(pathlib.Path(fresh_path).exists())
        history = await self.db.dialogue_history(9001)
        self.assertEqual([row["body"] for row in history], ["Новое"])

    async def test_nothing_to_purge_is_not_an_error(self):
        result = await purge_once(self.db, self.media, days=180.0, now=NOW)
        self.assertEqual(result, {"messages": 0, "files": 0})

    async def test_negative_retention_does_not_wipe_everything(self):
        await self.db.record_dialogue_message(9003, "in", "Свежее", now=NOW)
        # Отрицательный срок увёл бы границу в будущее и стёр всю переписку.
        result = await purge_once(self.db, self.media, days=-5.0, now=NOW)
        self.assertEqual(result["messages"], 0)
        self.assertEqual(len(await self.db.dialogue_history(9003)), 1)

    async def test_file_outside_the_media_directory_is_left_alone(self):
        outside = pathlib.Path(self.tmp.name) / "important.txt"
        outside.write_text("не трогать", encoding="utf-8")
        await self.db.record_dialogue_message(
            9002, "in", "Старое", now=NOW - 200 * 86400, image_path=str(outside))

        result = await purge_once(self.db, self.media, days=180.0, now=NOW)

        self.assertEqual(result["messages"], 1)
        self.assertEqual(result["files"], 0)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
