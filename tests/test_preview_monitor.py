"""Наблюдение за видеопревью в панели: сколько места занимают его файлы.

Превью — самая прожорливая часть бота. Когда место на томе кончалось, снаружи
это выглядело как «превью сломалось», поэтому владелец должен видеть расход
рядом с состоянием подсистемы.
"""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from bot import admin_metrics


class PreviewDiskUsageTests(unittest.TestCase):
    def test_counts_only_preview_temp_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            capture = root / "signalbot-preview"
            render = root / "twitch-signalbot-preview-render"
            foreign = root / "other-files"
            for folder in (capture, render, foreign):
                folder.mkdir()
            (capture / "segment-000000001.ts").write_bytes(b"0" * 4096)
            (render / "preview.mp4").write_bytes(b"1" * 2048)
            (foreign / "bot.db").write_bytes(b"2" * 8192)

            with patch.object(admin_metrics.tempfile, "gettempdir", return_value=tmp):
                usage = admin_metrics.preview_disk_usage()

        self.assertIsNotNone(usage)
        self.assertEqual(usage["preview_bytes"], 4096 + 2048)
        self.assertGreater(usage["total_bytes"], 0)
        self.assertGreaterEqual(usage["free_bytes"], 0)

    def test_missing_directories_report_zero_not_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(admin_metrics.tempfile, "gettempdir", return_value=tmp):
                usage = admin_metrics.preview_disk_usage()

        self.assertIsNotNone(usage)
        self.assertEqual(usage["preview_bytes"], 0)

    def test_volume_with_the_database_is_reported_separately(self):
        """Том с базой и временная папка — разные места; нужны обе цифры."""
        with tempfile.TemporaryDirectory() as tmp:
            database = pathlib.Path(tmp) / "bot.db"
            database.write_bytes(b"db")
            with patch.object(admin_metrics.tempfile, "gettempdir", return_value=tmp):
                with patch.dict("os.environ", {"DB_PATH": str(database)}):
                    usage = admin_metrics.preview_disk_usage()

        self.assertIsNotNone(usage)
        self.assertIn("data_free_bytes", usage)
        self.assertIn("data_total_bytes", usage)
        self.assertGreater(usage["data_total_bytes"], 0)

    def test_walking_is_bounded_and_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "signalbot-preview"
            root.mkdir()
            (root / "one.ts").write_bytes(b"0" * 1024)
            with patch.object(admin_metrics, "_directory_bytes", side_effect=OSError):
                # Ошибка обхода не должна ломать снимок панели.
                try:
                    usage = admin_metrics.preview_disk_usage()
                except OSError:
                    self.fail("preview_disk_usage не должен падать из-за ошибок обхода")
        self.assertIsNotNone(usage)


if __name__ == "__main__":
    unittest.main()
