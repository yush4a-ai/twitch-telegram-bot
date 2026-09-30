"""Non-destructive online backup and restore drill for staging SQLite."""

import sqlite3

import pytest

from scripts.sqlite_backup import backup_database, ensure_staging_path, verify_backup


def _database(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO events (value) VALUES ('committed')")
    conn.commit()
    return conn


def test_online_backup_is_consistent_with_wal(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    writer = _database(source)
    writer.execute("INSERT INTO events (value) VALUES ('uncommitted')")
    try:
        summary = backup_database(source, destination)
    finally:
        writer.rollback()
        writer.close()
    with sqlite3.connect(destination) as copy:
        rows = copy.execute("SELECT value FROM events ORDER BY id").fetchall()
    assert rows == [("committed",)]
    assert summary["integrity"] == "ok"
    assert summary["bytes"] > 0


def test_verify_restores_to_separate_file_without_changing_backup(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _database(source).close()
    backup_database(source, destination)
    before = destination.read_bytes()
    summary = verify_backup(destination)
    assert summary["integrity"] == "ok"
    assert summary["restored_tables"] == 1
    assert destination.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir() if "restore" in path.name) == []


def test_rejects_corrupt_and_existing_backup(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _database(source).close()
    destination.write_bytes(b"do not replace")
    with pytest.raises(FileExistsError):
        backup_database(source, destination)
    assert destination.read_bytes() == b"do not replace"
    with pytest.raises(ValueError):
        verify_backup(destination)


def test_rejects_missing_source_and_same_destination(tmp_path):
    source = tmp_path / "source.db"
    with pytest.raises(FileNotFoundError):
        backup_database(source, tmp_path / "snapshot.db")
    _database(source).close()
    with pytest.raises(ValueError):
        backup_database(source, source)


def test_data_volume_path_requires_staging_environment():
    ensure_staging_path("/data/bot.db", "staging")
    with pytest.raises(PermissionError):
        ensure_staging_path("/data/bot.db", "production")
    with pytest.raises(PermissionError):
        ensure_staging_path("/data/backups/snapshot.db", None)
