"""Viewer Plus folders inherit filters without changing Free subscriptions."""

import os
import tempfile
import time
import unittest

from bot.database import Database
from bot.viewer_filter import ViewerFilter, matches_viewer_filter
from bot.viewer_folders import FolderConflict, FolderLimit, ViewerFolderService


class ViewerFolderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(os.path.join(self.directory.name, "folders.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.service = ViewerFolderService(self.db)
        self.now = time.time()
        for user_id in (101, 202):
            for login in ("alpha", "beta"):
                await self.db.add_channel(user_id, login)

    async def grant(self, user_id=101):
        return await self.db.issue_test_viewer_plus(
            user_id, f"folder-{user_id}", starts_at=self.now - 1,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now,
        )

    async def test_free_foreign_folder_and_foreign_subscription_denied(self):
        with self.assertRaises(PermissionError):
            await self.service.create(101, "Игры", now=self.now)
        await self.grant(101)
        await self.grant(202)
        folder = await self.service.create(101, "Игры", now=self.now)
        with self.assertRaises(PermissionError):
            await self.service.move(202, "alpha", folder.id,
                                    expected_folder_id=None, now=self.now)
        with self.assertRaises(PermissionError):
            await self.service.rename(202, folder.id, "Чужое", expected_version=1,
                                      now=self.now)
        with self.assertRaises(PermissionError):
            await self.service.move(101, "gamma", folder.id,
                                    expected_folder_id=None, now=self.now)
        self.assertEqual(await self.service.memberships(202), {})

    async def test_move_inheritance_explicit_override_and_global_mute(self):
        await self.grant()
        first = await self.service.create(101, "Игры", now=self.now)
        second = await self.service.create(101, "Разговоры", now=self.now)
        first = await self.service.save_rule(
            101, first.id, expected_version=first.version,
            games=["Minecraft"], title_keywords=[], exclude_keywords=[], now=self.now,
        )
        self.assertEqual(first.version, 2)
        await self.service.move(101, "alpha", first.id,
                                expected_folder_id=None, now=self.now)
        self.assertEqual(await self.db.get_effective_viewer_filter(101, "alpha", now=self.now),
                         ViewerFilter(("Minecraft",), (), ()))
        self.assertFalse(matches_viewer_filter(
            await self.db.get_effective_viewer_filter(101, "alpha", now=self.now),
            "Just Chatting", "Title",
        ))
        await self.db.save_viewer_filter(
            101, "alpha", expected_version=0, games=[],
            title_keywords=[], exclude_keywords=[], now=self.now,
        )
        self.assertEqual(await self.db.get_effective_viewer_filter(101, "alpha", now=self.now),
                         ViewerFilter((), (), ()))
        self.assertFalse(await self.db.delete_viewer_filter(
            101, "alpha", expected_version=2, now=self.now,
        ))
        self.assertEqual(await self.db.get_effective_viewer_filter(101, "alpha", now=self.now),
                         ViewerFilter((), (), ()))
        self.assertTrue(await self.db.delete_viewer_filter(
            101, "alpha", expected_version=1, now=self.now,
        ))
        self.assertEqual(await self.db.get_effective_viewer_filter(101, "alpha", now=self.now),
                         ViewerFilter(("Minecraft",), (), ()))
        await self.service.move(101, "alpha", second.id,
                                expected_folder_id=first.id, now=self.now)
        self.assertEqual((await self.service.memberships(101))["alpha"], second.id)
        with self.assertRaises(FolderConflict):
            await self.service.move(101, "alpha", first.id,
                                    expected_folder_id=None, now=self.now)
        await self.db.set_notify_enabled(101, "alpha", False)
        cursor = await self.db.conn.execute(
            "SELECT notify_enabled FROM tracked_channels WHERE chat_id=101 "
            "AND twitch_login='alpha'"
        )
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_delete_keeps_subscription_and_expiry_only_disables_rule(self):
        grant_id = await self.grant()
        folder = await self.service.create(101, "Игры", now=self.now)
        await self.service.save_rule(
            101, folder.id, expected_version=folder.version,
            games=["Minecraft"], title_keywords=[], exclude_keywords=[], now=self.now,
        )
        await self.service.move(101, "beta", folder.id,
                                expected_folder_id=None, now=self.now)
        self.assertIsNotNone(await self.db.get_effective_viewer_filter(101, "beta", now=self.now))
        await self.db.revoke_test_viewer_plus(grant_id, revoked_at=self.now + 1,
                                              issued_by=425785231)
        self.assertIsNone(await self.db.get_effective_viewer_filter(101, "beta", now=self.now + 1))
        self.assertEqual((await self.service.memberships(101))["beta"], folder.id)
        with self.assertRaises(PermissionError):
            await self.service.delete(101, folder.id, expected_version=2,
                                      now=self.now + 1)
        await self.db.issue_test_viewer_plus(
            101, "folder-again", starts_at=self.now + 1,
            expires_at=self.now + 3600, issued_by=425785231, now=self.now + 1,
        )
        await self.service.delete(101, folder.id, expected_version=2,
                                  now=self.now + 1)
        self.assertEqual(await self.service.memberships(101), {})
        self.assertIn("beta", await self.db.list_channels(101))

    async def test_versions_names_and_unfollow_cleanup_are_bounded(self):
        await self.grant()
        folder = await self.service.create(101, "Игры", now=self.now)
        with self.assertRaises(FolderConflict):
            await self.service.create(101, "игры", now=self.now)
        with self.assertRaises(FolderConflict):
            await self.service.save_rule(
                101, folder.id, expected_version=2, games=[],
                title_keywords=[], exclude_keywords=[], now=self.now,
            )
        for invalid in ([], {"id": folder.id}, "not-an-id"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    await self.service.rename(101, invalid, "Новое",
                                              expected_version=1, now=self.now)
        for invalid in (True, -1, "1"):
            with self.subTest(version=invalid):
                with self.assertRaises(ValueError):
                    await self.service.delete(101, folder.id,
                                              expected_version=invalid, now=self.now)
        await self.service.move(101, "alpha", folder.id,
                                expected_folder_id=None, now=self.now)
        await self.db.remove_channel(101, "alpha")
        self.assertEqual(await self.service.memberships(101), {})
        await self.db.add_channel(101, "alpha")
        self.assertEqual(await self.service.memberships(101), {})

    async def test_technical_folder_ceiling_rejects_unbounded_empty_growth(self):
        await self.grant()
        for index in range(200):
            await self.service.create(101, f"Папка {index}", now=self.now)
        self.assertEqual(len(await self.service.list_folders(101)), 200)
        with self.assertRaises(FolderLimit):
            await self.service.create(101, "Папка 201", now=self.now)
        self.assertEqual(len(await self.service.list_folders(101)), 200)


if __name__ == "__main__":
    unittest.main()
