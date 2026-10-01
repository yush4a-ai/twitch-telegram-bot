"""Viewer-owned folders and inherited Plus alert filters."""

from __future__ import annotations

import json
import math
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite

from .database import Database
from .viewer_filter import ViewerFilter, validate_viewer_filter


class FolderConflict(Exception):
    """The folder name or client version no longer matches server state."""


class FolderNameTaken(FolderConflict):
    """A different folder owned by this viewer already has the name."""


class FolderLimit(Exception):
    """Bound empty-folder growth in SQLite and Mini App state responses."""


@dataclass(frozen=True)
class ViewerFolder:
    id: str
    name: str
    version: int
    rule: ViewerFilter


class ViewerFolderService:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _valid_user(user_id: int, now: float) -> None:
        if (type(user_id) is not int or user_id <= 0
                or not isinstance(now, (int, float)) or not math.isfinite(now)):
            raise ValueError("invalid folder identity")

    @staticmethod
    def _valid_folder_id(folder_id: object) -> None:
        if not isinstance(folder_id, str) or re.fullmatch(r"[0-9a-f]{32}", folder_id) is None:
            raise ValueError("invalid folder id")

    @staticmethod
    def _valid_version(version: object) -> None:
        if type(version) is not int or version < 1:
            raise ValueError("invalid folder version")

    @staticmethod
    def _name(name: str) -> tuple[str, str]:
        if not isinstance(name, str):
            raise ValueError("invalid folder name")
        visible = name.strip()
        if (
            not 2 <= len(visible) <= 40
            or any(unicodedata.category(char).startswith("C") for char in visible)
        ):
            raise ValueError("invalid folder name")
        return visible, visible.casefold()

    @staticmethod
    def _folder(row: tuple) -> ViewerFolder:
        return ViewerFolder(
            id=row[0], name=row[1], version=row[2],
            rule=validate_viewer_filter(
                json.loads(row[3]), json.loads(row[4]), json.loads(row[5]),
            ),
        )

    @asynccontextmanager
    async def _transaction(self):
        async with self._db._write_lock:
            await self._db.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._db.conn
                await self._db.conn.commit()
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def _require_plus(self, user_id: int, *, now: float) -> None:
        self._valid_user(user_id, now)
        if not await self._db.has_viewer_plus(user_id, now=now):
            raise PermissionError("Viewer Plus required")

    async def list_folders(self, user_id: int) -> list[ViewerFolder]:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid folder identity")
        cursor = await self._db.conn.execute(
            "SELECT id,name,version,games_json,title_keywords_json,"
            "exclude_keywords_json FROM viewer_folders WHERE telegram_user_id=? "
            "ORDER BY name_key,id", (user_id,),
        )
        return [self._folder(tuple(row)) for row in await cursor.fetchall()]

    async def memberships(self, user_id: int) -> dict[str, str]:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid folder identity")
        cursor = await self._db.conn.execute(
            "SELECT m.twitch_login,m.folder_id FROM viewer_folder_memberships m "
            "JOIN viewer_folders f ON f.id=m.folder_id "
            "AND f.telegram_user_id=m.telegram_user_id "
            "JOIN tracked_channels c ON c.chat_id=m.telegram_user_id "
            "AND c.twitch_login=m.twitch_login WHERE m.telegram_user_id=?",
            (user_id,),
        )
        return dict(await cursor.fetchall())

    async def create(self, user_id: int, name: str, *, now: float) -> ViewerFolder:
        visible, key = self._name(name)
        async with self._transaction() as conn:
            await self._require_plus(user_id, now=now)
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM viewer_folders WHERE telegram_user_id=?",
                (user_id,),
            )
            if (await cursor.fetchone())[0] >= 200:
                raise FolderLimit("folder storage ceiling reached")
            folder_id = uuid.uuid4().hex
            try:
                await conn.execute(
                    "INSERT INTO viewer_folders "
                    "(id,telegram_user_id,name,name_key,version,games_json,"
                    "title_keywords_json,exclude_keywords_json,updated_at) "
                    "VALUES (?,?,?,?,1,'[]','[]','[]',?)",
                    (folder_id, user_id, visible, key, now),
                )
            except aiosqlite.IntegrityError as error:
                raise FolderNameTaken("folder name already exists") from error
            return ViewerFolder(folder_id, visible, 1, ViewerFilter((), (), ()))

    async def rename(self, user_id: int, folder_id: str, name: str, *,
                     expected_version: int, now: float) -> ViewerFolder:
        self._valid_folder_id(folder_id)
        self._valid_version(expected_version)
        visible, key = self._name(name)
        async with self._transaction() as conn:
            await self._require_plus(user_id, now=now)
            cursor = await conn.execute(
                "SELECT version FROM viewer_folders WHERE id=? AND telegram_user_id=?",
                (folder_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise PermissionError("folder not owned")
            if row[0] != expected_version:
                raise FolderConflict("folder changed")
            try:
                await conn.execute(
                    "UPDATE viewer_folders SET name=?,name_key=?,version=version+1,"
                    "updated_at=? WHERE id=? AND telegram_user_id=?",
                    (visible, key, now, folder_id, user_id),
                )
            except aiosqlite.IntegrityError as error:
                raise FolderNameTaken("folder name already exists") from error
            cursor = await conn.execute(
                "SELECT id,name,version,games_json,title_keywords_json,"
                "exclude_keywords_json FROM viewer_folders WHERE id=?",
                (folder_id,),
            )
            return self._folder(tuple(await cursor.fetchone()))

    async def save_rule(
        self, user_id: int, folder_id: str, *, expected_version: int,
        games: object, title_keywords: object, exclude_keywords: object,
        now: float,
    ) -> ViewerFolder:
        self._valid_folder_id(folder_id)
        self._valid_version(expected_version)
        rule = validate_viewer_filter(games, title_keywords, exclude_keywords)
        async with self._transaction() as conn:
            await self._require_plus(user_id, now=now)
            cursor = await conn.execute(
                "SELECT version FROM viewer_folders WHERE id=? AND telegram_user_id=?",
                (folder_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise PermissionError("folder not owned")
            if row[0] != expected_version:
                raise FolderConflict("folder changed")
            await conn.execute(
                "UPDATE viewer_folders SET games_json=?,title_keywords_json=?,"
                "exclude_keywords_json=?,version=version+1,updated_at=? "
                "WHERE id=? AND telegram_user_id=?",
                (json.dumps(rule.games, ensure_ascii=False),
                 json.dumps(rule.title_keywords, ensure_ascii=False),
                 json.dumps(rule.exclude_keywords, ensure_ascii=False),
                 now, folder_id, user_id),
            )
            cursor = await conn.execute(
                "SELECT id,name,version,games_json,title_keywords_json,"
                "exclude_keywords_json FROM viewer_folders WHERE id=?",
                (folder_id,),
            )
            return self._folder(tuple(await cursor.fetchone()))

    async def move(self, user_id: int, login: str, folder_id: str | None, *,
                   expected_folder_id: str | None, now: float) -> str | None:
        if (
            not isinstance(login, str) or re.fullmatch(r"[A-Za-z0-9_]{2,25}", login) is None
        ):
            raise ValueError("invalid membership")
        if folder_id is not None:
            self._valid_folder_id(folder_id)
        if expected_folder_id is not None:
            self._valid_folder_id(expected_folder_id)
        login = login.lower()
        async with self._transaction() as conn:
            await self._require_plus(user_id, now=now)
            cursor = await conn.execute(
                "SELECT 1 FROM tracked_channels WHERE chat_id=? AND twitch_login=?",
                (user_id, login),
            )
            if await cursor.fetchone() is None:
                raise PermissionError("subscription not owned")
            if folder_id is not None:
                cursor = await conn.execute(
                    "SELECT 1 FROM viewer_folders WHERE id=? AND telegram_user_id=?",
                    (folder_id, user_id),
                )
                if await cursor.fetchone() is None:
                    raise PermissionError("folder not owned")
            cursor = await conn.execute(
                "SELECT folder_id FROM viewer_folder_memberships "
                "WHERE telegram_user_id=? AND twitch_login=?", (user_id, login),
            )
            row = await cursor.fetchone()
            current = row[0] if row else None
            if current != expected_folder_id:
                raise FolderConflict("membership changed")
            if folder_id is None:
                await conn.execute(
                    "DELETE FROM viewer_folder_memberships WHERE telegram_user_id=? "
                    "AND twitch_login=?", (user_id, login),
                )
            else:
                await conn.execute(
                    "INSERT INTO viewer_folder_memberships "
                    "(telegram_user_id,twitch_login,folder_id,updated_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(telegram_user_id,twitch_login) "
                    "DO UPDATE SET folder_id=excluded.folder_id,updated_at=excluded.updated_at",
                    (user_id, login, folder_id, now),
                )
            return folder_id

    async def delete(self, user_id: int, folder_id: str, *,
                     expected_version: int, now: float) -> None:
        self._valid_folder_id(folder_id)
        self._valid_version(expected_version)
        async with self._transaction() as conn:
            await self._require_plus(user_id, now=now)
            cursor = await conn.execute(
                "SELECT version FROM viewer_folders WHERE id=? AND telegram_user_id=?",
                (folder_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise PermissionError("folder not owned")
            if row[0] != expected_version:
                raise FolderConflict("folder changed")
            await conn.execute(
                "DELETE FROM viewer_folder_memberships WHERE telegram_user_id=? "
                "AND folder_id=?", (user_id, folder_id),
            )
            await conn.execute(
                "DELETE FROM viewer_folders WHERE telegram_user_id=? AND id=?",
                (user_id, folder_id),
            )
