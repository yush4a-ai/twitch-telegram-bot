"""Build a new R2-readable offline copy of R3 shared stream samples.

This tool never changes the source database or an existing output. A real
cutover still requires stopping staging writers and reconciling queue jobs.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


_JOIN = (
    "FROM stream_observation_memberships m JOIN stream_observations o "
    "ON o.twitch_login = m.twitch_login AND o.stream_id = m.stream_id "
    "AND o.sampled_at = m.sampled_at "
)
_MATCH = (
    "s.chat_id = m.chat_id AND s.twitch_login = m.twitch_login "
    "AND s.stream_id = m.stream_id AND s.sampled_at = m.sampled_at"
)
_CONTENT = (
    "s.viewer_count = o.viewer_count AND s.title = o.title "
    "AND s.game_name = o.game_name"
)


def materialize_shared_samples(source: Path, destination: Path) -> dict[str, object]:
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("source and output must differ")
    if not str(destination).lower().endswith(".offline.db"):
        raise ValueError("output must end with .offline.db")
    if destination.exists():
        raise FileExistsError(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.partial-", suffix=".db",
        dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
            with closing(sqlite3.connect(temporary)) as copy:
                old.backup(copy, pages=256, sleep=0.1)
                if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("source snapshot integrity failed")
                copy.execute("BEGIN IMMEDIATE")
                try:
                    orphaned = copy.execute(
                        "SELECT COUNT(*) FROM stream_observation_memberships m "
                        "LEFT JOIN stream_observations o ON "
                        "o.twitch_login = m.twitch_login AND o.stream_id = m.stream_id "
                        "AND o.sampled_at = m.sampled_at WHERE o.twitch_login IS NULL"
                    ).fetchone()[0]
                    if orphaned:
                        raise ValueError("shared memberships have missing observations")
                    memberships = copy.execute(
                        "SELECT COUNT(*) " + _JOIN
                    ).fetchone()[0]
                    conflict = copy.execute(
                        "SELECT COUNT(*) " + _JOIN +
                        "JOIN stream_samples s ON " + _MATCH +
                        " WHERE NOT (" + _CONTENT + ")"
                    ).fetchone()[0]
                    if conflict:
                        raise ValueError("legacy sample conflicts with shared observation")
                    inserted = copy.execute(
                        "INSERT INTO stream_samples "
                        "(chat_id, twitch_login, stream_id, sampled_at, "
                        "viewer_count, title, game_name) "
                        "SELECT m.chat_id, m.twitch_login, m.stream_id, m.sampled_at, "
                        "o.viewer_count, o.title, o.game_name " + _JOIN +
                        "WHERE NOT EXISTS (SELECT 1 FROM stream_samples s WHERE " +
                        _MATCH + ")"
                    ).rowcount
                    exact = copy.execute(
                        "SELECT COUNT(*) " + _JOIN +
                        "WHERE (SELECT COUNT(*) FROM stream_samples s WHERE " +
                        _MATCH + " AND " + _CONTENT + ") = 1"
                    ).fetchone()[0]
                    if exact != memberships:
                        raise ValueError("legacy sample count or content mismatch")
                    copy.commit()
                except BaseException:
                    copy.rollback()
                    raise
                if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("materialized output integrity failed")
        os.link(temporary, destination)
        return {
            "integrity": "ok", "memberships": memberships,
            "inserted": inserted, "bytes": destination.stat().st_size,
        }
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = materialize_shared_samples(args.source, args.out)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"R3 rollback materialization stopped: {type(error).__name__}", file=sys.stderr)
        return 2
    print(" ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
