import unittest

from bot.database import Database


class ViewerAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        await self.db.add_channel(101, "alpha")
        await self.db.add_channel(202, "beta")

    async def asyncTearDown(self):
        await self.db.close()

    async def test_test_grant_is_idempotent_expires_and_revokes(self):
        self.assertFalse(await self.db.has_viewer_plus(101, now=100))
        grant = await self.db.issue_test_viewer_plus(
            101, "viewer-101", starts_at=100, expires_at=200,
            issued_by=425785231, now=100,
        )
        self.assertEqual(grant, await self.db.issue_test_viewer_plus(
            101, "viewer-101", starts_at=100, expires_at=200,
            issued_by=425785231, now=101,
        ))
        self.assertTrue(await self.db.has_viewer_plus(101, now=150))
        self.assertFalse(await self.db.has_viewer_plus(202, now=150))
        self.assertFalse(await self.db.has_viewer_plus(101, now=200))
        with self.assertRaises(ValueError):
            await self.db.issue_test_viewer_plus(
                202, "viewer-101", starts_at=100, expires_at=200,
                issued_by=425785231, now=101,
            )
        self.assertTrue(await self.db.revoke_test_viewer_plus(
            grant, revoked_at=160, issued_by=425785231,
        ))
        self.assertFalse(await self.db.has_viewer_plus(101, now=161))
        self.assertFalse(await self.db.revoke_test_viewer_plus(
            grant, revoked_at=162, issued_by=425785231,
        ))

    async def test_filter_write_requires_owner_subscription_plus_and_version(self):
        with self.assertRaises(PermissionError):
            await self.db.save_viewer_filter(
                101, "alpha", expected_version=0,
                games=["Minecraft"], title_keywords=[], exclude_keywords=[], now=150,
            )
        await self.db.issue_test_viewer_plus(
            101, "viewer-filter", starts_at=100, expires_at=200,
            issued_by=425785231, now=100,
        )
        self.assertIsNone(await self.db.save_viewer_filter(
            101, "beta", expected_version=0,
            games=["Minecraft"], title_keywords=[], exclude_keywords=[], now=150,
        ))
        self.assertEqual(await self.db.save_viewer_filter(
            101, "alpha", expected_version=0,
            games=["Minecraft"], title_keywords=["speedrun"],
            exclude_keywords=["rerun"], now=150,
        ), 1)
        self.assertIsNone(await self.db.save_viewer_filter(
            101, "alpha", expected_version=0,
            games=[], title_keywords=[], exclude_keywords=[], now=151,
        ))
        self.assertEqual(await self.db.save_viewer_filter(
            101, "alpha", expected_version=1,
            games=["Minecraft"], title_keywords=[], exclude_keywords=[], now=152,
        ), 2)
        stored = await self.db.get_viewer_filter(101, "alpha")
        self.assertEqual(stored[0], 2)
        self.assertEqual(stored[1].games, ("Minecraft",))
        self.assertIsNone(await self.db.get_viewer_filter(202, "alpha"))
        self.assertIsNotNone(await self.db.get_effective_viewer_filter(101, "alpha", now=150))
        self.assertIsNone(await self.db.get_effective_viewer_filter(101, "alpha", now=201))
        with self.assertRaises(PermissionError):
            await self.db.save_viewer_filter(
                101, "alpha", expected_version=2,
                games=[], title_keywords=[], exclude_keywords=[], now=201,
            )
        await self.db.remove_channel(101, "alpha")
        self.assertIsNone(await self.db.get_viewer_filter(101, "alpha"))

    async def test_remove_all_channels_clears_viewer_rules(self):
        await self.db.issue_test_viewer_plus(
            101, "viewer-remove-all", starts_at=100, expires_at=200,
            issued_by=425785231, now=100,
        )
        await self.db.save_viewer_filter(
            101, "alpha", expected_version=0,
            games=[], title_keywords=["live"], exclude_keywords=[], now=150,
        )
        self.assertEqual(await self.db.remove_all_channels(101), 1)
        self.assertIsNone(await self.db.get_viewer_filter(101, "alpha"))
