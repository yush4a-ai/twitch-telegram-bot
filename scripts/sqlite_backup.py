"""Staging SQLite online backup and non-destructive restore drill."""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


def ensure_staging_path(path: str | Path, environment_name: str | None) -> None:
    normalized = str(path).replace("\\", "/")
    if (normalized == "/data" or normalized.startswith("/data/")) and environment_name != "staging":
        raise PermissionError("Операции с /data разрешены только в Railway staging")


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _integrity(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise ValueError("SQLite integrity_check не прошёл")


def _table_count(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]


def _new_temporary_file(parent: Path, prefix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".db", dir=parent)
    os.close(fd)
    return Path(name)


def backup_database(source: Path, destination: Path) -> dict[str, object]:
    """Publish one consistent live snapshot, never replacing a previous backup."""
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == destination.resolve():
        raise ValueError("Источник и backup destination совпадают")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _new_temporary_file(destination.parent, f".{destination.name}.partial-")
    try:
        try:
            with closing(_read_only(source)) as original, closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy, pages=256, sleep=0.1)
                _integrity(copy)
                copy.commit()
        except sqlite3.DatabaseError as error:
            raise ValueError("Не удалось создать целостный SQLite backup") from error
        # Hard-link is an atomic no-clobber publication on the same filesystem.
        os.link(temporary, destination)
        return {"integrity": "ok", "bytes": destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def verify_backup(path: Path) -> dict[str, object]:
    """Check backup and restore it into a disposable new DB, never into DB_PATH."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    restored = _new_temporary_file(path.parent, f".{path.name}.restore-")
    try:
        try:
            with closing(_read_only(path)) as backup, closing(sqlite3.connect(restored)) as copy:
                _integrity(backup)
                backup.backup(copy, pages=256, sleep=0.1)
                _integrity(copy)
                tables = _table_count(copy)
                copy.commit()
        except sqlite3.DatabaseError as error:
            raise ValueError("SQLite backup повреждён") from error
        return {"integrity": "ok", "restored_tables": tables, "bytes": path.stat().st_size}
    finally:
        restored.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite backup/restore drill для staging")
    actions = parser.add_subparsers(dest="action", required=True)
    backup = actions.add_parser("backup")
    backup.add_argument("--db", type=Path, required=True)
    backup.add_argument("--out", type=Path, required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        environment = os.getenv("RAILWAY_ENVIRONMENT_NAME")
        if args.action == "backup":
            ensure_staging_path(args.db, environment)
            ensure_staging_path(args.out, environment)
            result = backup_database(args.db, args.out)
        else:
            ensure_staging_path(args.backup, environment)
            result = verify_backup(args.backup)
        print(" ".join(f"{key}={value}" for key, value in result.items()))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"SQLite backup остановлен: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
