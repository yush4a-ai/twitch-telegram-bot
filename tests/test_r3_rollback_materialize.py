import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import Database
from scripts.r3_rollback_materialize import materialize_shared_samples


class R3RollbackMaterializeTests(unittest.IsolatedAsyncioTestCase):
    async def test_materialization_preserves_exact_membership_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            first = Path(directory) / "first.offline.db"
            second = Path(directory) / "second.offline.db"
            db = Database(str(source))
            await db.connect()
            try:
                await db.record_stream_observation("alpha", "s1", 10.0, 10, "First", "Game", [1])
                await db.record_stream_observation("alpha", "s1", 20.0, 20, "Second", "Game", [1, 2])
                expected_first = await db.get_stream_samples(1, "alpha", "s1")
                expected_second = await db.get_stream_samples(2, "alpha", "s1")
            finally:
                await db.close()
            result = materialize_shared_samples(source, first)
            self.assertEqual((result["memberships"], result["inserted"]), (3, 3))
            reopened = Database(str(first))
            await reopened.connect()
            try:
                self.assertEqual(await reopened.get_stream_samples(1, "alpha", "s1"), expected_first)
                self.assertEqual(await reopened.get_stream_samples(2, "alpha", "s1"), expected_second)
            finally:
                await reopened.close()
            repeat = materialize_shared_samples(first, second)
            self.assertEqual((repeat["memberships"], repeat["inserted"]), (3, 0))

    async def test_conflicting_legacy_sample_aborts_without_publishing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            output = Path(directory) / "rollback.offline.db"
            db = Database(str(source))
            await db.connect()
            try:
                await db.record_stream_observation("alpha", "s1", 10.0, 10, "First", "Game", [1])
                await db.conn.execute(
                    "INSERT INTO stream_samples VALUES (1, 'alpha', 's1', 10, 99, 'Wrong', 'Game')"
                )
                await db.conn.commit()
            finally:
                await db.close()
            with self.assertRaises(ValueError):
                materialize_shared_samples(source, output)
            self.assertFalse(output.exists())

    def test_output_cannot_replace_input_or_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            sqlite3.connect(source).close()
            with self.assertRaises(ValueError):
                materialize_shared_samples(source, source)
            output = Path(directory) / "rollback.offline.db"
            output.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                materialize_shared_samples(source, output)
            self.assertEqual(output.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
