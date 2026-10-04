"""Runtime database backup tests for the production audit fix.

The production volume held no backup newer than 30.09 while the live database
was written to every minute. These tests pin the behaviour of the runtime
copier: one consistent snapshot, never overwriting an existing file, pruned
rotation that never touches files it does not own, and no exception escaping
into the poller loop.
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from bot.db_backup import create_backup, run_backup_loop


def _seed(path: Path, rows: int = 20) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO sample (value) VALUES (?)", [(f"row-{index}",) for index in range(rows)]
        )
        conn.commit()


class CreateBackupTests(unittest.TestCase):
    def test_backup_is_consistent_and_keeps_wal_rows(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bot.db"
            _seed(source, rows=25)
            backup_dir = root / "backups"

            destination = create_backup(source, backup_dir, now=1_700_000_000.0)

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.parent, backup_dir)
            with closing(sqlite3.connect(destination)) as copy:
                self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    copy.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 25
                )

    def test_existing_backup_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bot.db"
            _seed(source)
            backup_dir = root / "backups"

            first = create_backup(source, backup_dir, now=1_700_000_000.0)
            second = create_backup(source, backup_dir, now=1_700_000_000.0)

            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file() and second.is_file())

    def test_rotation_keeps_newest_and_leaves_foreign_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bot.db"
            _seed(source)
            backup_dir = root / "backups"
            backup_dir.mkdir()
            foreign = backup_dir / "pre-cutover-20260930.db"
            foreign.write_bytes(b"manual copy")
            for index in range(6):
                (backup_dir / f"auto-2026100{index}T000000Z.db").write_bytes(b"old")

            create_backup(source, backup_dir, retention=3, now=1_700_000_000.0)

            remaining = sorted(path.name for path in backup_dir.glob("auto-*.db"))
            self.assertEqual(len(remaining), 3)
            self.assertTrue(foreign.is_file())

    def test_missing_source_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                create_backup(root / "absent.db", root / "backups")


class BackupLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_survives_a_failed_backup(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stop = asyncio.Event()

            task = asyncio.create_task(
                run_backup_loop(
                    root / "absent.db",
                    root / "backups",
                    interval_seconds=0.01,
                    stop_event=stop,
                )
            )
            await asyncio.sleep(0.05)
            stop.set()
            # Цикл обязан выйти сам, без исключения наружу: сбой копии не должен
            # ронять фоновую задачу бота.
            await asyncio.wait_for(task, timeout=5.0)
            self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
