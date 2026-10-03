"""Persistent favorites are presentation preferences, independent of delivery."""
import unittest
from unittest.mock import patch

from bot.database import Database
from tests import test_mini_app_viewer as viewer_helpers


class FavoritesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = viewer_helpers.MiniAppViewerTests.asyncSetUp
    asyncTearDown = viewer_helpers.MiniAppViewerTests.asyncTearDown
    request = viewer_helpers.MiniAppViewerTests.request

    async def test_favorite_is_personal_persistent_and_independent_of_notifications(self):
        for user in (101, 202):
            await self.db.add_channel(user, 'alpha')
        await self.db.set_personal_notify_if_subscribed(101, 'alpha', False)
        before = await self.db.list_personal_channel_status(101)
        for _ in range(2):
            async with self.request('favorite', login='alpha', is_favorite=True) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {'is_favorite': True})
        self.assertEqual(await self.db.list_personal_channel_status(101), before)
        async with self.request('state') as response:
            self.assertTrue((await response.json())['subscriptions'][0]['is_favorite'])
        async with self.request('state', 202) as response:
            self.assertFalse((await response.json())['subscriptions'][0]['is_favorite'])
        other = Database(self.db._path)
        await other.connect()
        try:
            self.assertEqual(await other.list_viewer_favorites(101), {'alpha'})
            self.assertEqual(await other.list_viewer_favorites(202), set())
        finally:
            await other.close()
        async with self.request('favorite', login='alpha', is_favorite=False) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse(await self.db.get_notify_enabled(101, 'alpha'))

    async def test_unfollow_cascades_without_restoring_favorite_on_refollow(self):
        await self.db.add_channel(101, 'alpha')
        self.assertTrue(await self.db.set_viewer_favorite(101, 'alpha', True))
        async with self.request('unfollow', login='alpha') as response:
            self.assertEqual(response.status, 200)
        await self.db.add_channel(101, 'alpha')
        self.assertEqual(await self.db.list_viewer_favorites(101), set())

    async def test_no_auth_forgery_unknown_subscription_or_non_boolean(self):
        await self.db.add_channel(202, 'alpha')
        async with self.session.post(self.base+'/app/api/viewer/favorite',json={'login':'alpha','is_favorite':True}) as response:
            self.assertEqual(response.status, 401)
        async with self.request('favorite', login='alpha', is_favorite=True) as response:
            self.assertEqual(response.status, 404)
        async with self.request('favorite', login='alpha', is_favorite=True, user_id=202) as response:
            self.assertEqual(response.status, 400)
        for value in (1, 'true', None, [], {}):
            async with self.request('favorite', 202, login='alpha', is_favorite=value) as response:
                self.assertEqual(response.status, 400)
        self.assertEqual(await self.db.list_viewer_favorites(202), set())

    async def test_storage_error_is_retryable_and_keeps_previous_state(self):
        await self.db.add_channel(101, 'alpha')
        with patch.object(self.db, 'set_viewer_favorite', side_effect=RuntimeError('test storage failure')):
            async with self.request('favorite', login='alpha', is_favorite=True) as response:
                self.assertEqual(response.status, 503)
        self.assertEqual(await self.db.list_viewer_favorites(101), set())

    async def test_star_does_not_change_free_plan_priority(self):
        for index in range(51):
            await self.db.add_channel(101, f'channel{index:02d}')
        before = await self.db.list_personal_channel_status(101)
        self.assertTrue(next(row for row in before if row[0]=='channel50')[4])
        self.assertTrue(await self.db.set_viewer_favorite(101, 'channel50', True))
        self.assertEqual(await self.db.list_personal_channel_status(101), before)
