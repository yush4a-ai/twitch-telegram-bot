"""Картинки: тип по содержимому, лимит размера, безопасное удаление."""
import pathlib
import tempfile
import unittest

from bot.media_store import (
    MAX_IMAGE_BYTES,
    MediaError,
    image_extension,
    remove_image,
    save_image,
)

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class MediaStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name) / "media"

    def test_type_is_detected_by_content_not_by_name(self):
        self.assertEqual(image_extension(JPEG), "jpg")
        self.assertEqual(image_extension(PNG), "png")
        self.assertIsNone(image_extension(b"<html>not an image</html>"))
        self.assertIsNone(image_extension(b""))

    def test_saved_file_keeps_the_detected_type(self):
        path = save_image(self.dir, PNG, prefix="campaign")
        self.assertTrue(path.endswith(".png"))
        self.assertTrue(pathlib.Path(path).is_file())
        self.assertEqual(pathlib.Path(path).read_bytes(), PNG)

    def test_oversized_image_is_rejected(self):
        with self.assertRaises(MediaError):
            save_image(self.dir, JPEG + b"0" * MAX_IMAGE_BYTES, prefix="campaign")

    def test_wrong_type_is_rejected(self):
        with self.assertRaises(MediaError):
            save_image(self.dir, b"not an image", prefix="campaign")

    def test_two_uploads_do_not_overwrite_each_other(self):
        first = save_image(self.dir, JPEG, prefix="campaign")
        second = save_image(self.dir, JPEG, prefix="campaign")
        self.assertNotEqual(first, second)

    def test_removal_is_limited_to_the_media_directory(self):
        inside = save_image(self.dir, PNG, prefix="campaign")
        outside = pathlib.Path(self.tmp.name) / "important.txt"
        outside.write_text("не трогать", encoding="utf-8")

        self.assertTrue(remove_image(inside, directory=self.dir))
        self.assertFalse(pathlib.Path(inside).exists())
        # Чужой файл рядом с каталогом удалить нельзя.
        self.assertFalse(remove_image(str(outside), directory=self.dir))
        self.assertTrue(outside.exists())
        self.assertFalse(remove_image(None, directory=self.dir))


if __name__ == "__main__":
    unittest.main()
