"""Undo restores settings atomically and cannot resurrect a newer subscription."""
import asyncio
import unittest

from tests import test_mini_app_viewer as helpers


class UndoTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.MiniAppViewerTests.asyncSetUp
    asyncTearDown = helpers.MiniAppViewerTests.asyncTearDown
    request = helpers.MiniAppViewerTests.request

    async def deleted(self):
        await self.db.add_channel(101, 'alpha')
        await self.db.set_personal_notify_if_subscribed(101, 'alpha', False)
        await self.db.set_viewer_favorite(101, 'alpha', True)
        async with self.request('unfollow', login='alpha') as response:
            self.assertEqual(response.status, 200)
            return (await response.json())['undo_token']

    async def test_restores_notifications_favorite_order_and_single_use(self):
        token = await self.deleted()
        self.assertNotIn('alpha', await self.db.list_channels(101))
        async with self.request('unfollow/undo', undo_token=token) as response:
            self.assertEqual(response.status, 200)
        self.assertFalse(await self.db.get_notify_enabled(101, 'alpha'))
        self.assertEqual(await self.db.list_viewer_favorites(101), {'alpha'})
        async with self.request('unfollow/undo', undo_token=token) as response:
            self.assertEqual(response.status, 409)

    async def test_requires_same_verified_user_and_strict_payload(self):
        token = await self.deleted()
        async with self.session.post(self.base+'/app/api/viewer/unfollow/undo',json={'undo_token':token}) as response:
            self.assertEqual(response.status, 401)
        async with self.request('unfollow/undo',202,undo_token=token) as response:
            self.assertEqual(response.status, 409)
        async with self.request('unfollow/undo',undo_token=token,user_id=202) as response:
            self.assertEqual(response.status, 400)
        self.assertNotIn('alpha',await self.db.list_channels(101))
        async with self.request('unfollow/undo',undo_token=token) as response:
            self.assertEqual(response.status, 200)

    async def test_refollow_then_delete_invalidates_old_token_in_every_database_path(self):
        for limited in (False,True):
            token=await self.deleted()
            if limited: await self.db.add_channel_with_limit(101,'alpha',50)
            else: await self.db.add_channel(101,'alpha')
            await self.db.remove_channel(101,'alpha')
            async with self.request('unfollow/undo',undo_token=token) as response:
                self.assertEqual(response.status,409)

    async def test_expiry_and_limit_are_checked_at_restore(self):
        token=await self.deleted()
        await self.db.conn.execute('UPDATE viewer_unfollow_undo SET expires_at=0')
        await self.db.conn.commit()
        async with self.request('unfollow/undo',undo_token=token) as response:
            self.assertEqual(response.status,409)
        token=await self.deleted()
        for index in range(50):await self.db.add_channel(101,f'other{index}')
        async with self.request('unfollow/undo',undo_token=token) as response:
            self.assertEqual(response.status,409)
            self.assertEqual((await response.json())['error'],'channel_limit')
        self.assertEqual(len(await self.db.list_channels(101)),50)

    async def test_two_restores_only_one_wins(self):
        token=await self.deleted()
        async def restore():
            async with self.request('unfollow/undo',undo_token=token) as response:return response.status
        self.assertEqual(sorted(await asyncio.gather(restore(),restore())),[200,409])

    async def test_clear_all_invalidates_undo_even_when_no_subscription_remains(self):
        token=await self.deleted()
        await self.db.remove_all_channels(101)
        async with self.request('unfollow/undo',undo_token=token) as response:
            self.assertEqual(response.status,409)

    async def test_full_preferences_survive_reopen_and_versions_advance(self):
        from bot.database import Database
        await self.db.add_channel(101,'alpha')
        await self.db.conn.execute("INSERT INTO viewer_alert_filters VALUES (101,'alpha','[]','[\"live\"]','[]',4,1)")
        await self.db.conn.execute("INSERT INTO category_alert_preferences VALUES (101,'alpha',1,'[\"1\"]','[\"Game\"]',7,1)")
        await self.db.conn.execute("INSERT INTO viewer_video_selection_state VALUES (101,3)")
        await self.db.conn.execute("INSERT INTO viewer_video_selections VALUES (101,'11','alpha',0)")
        await self.db.conn.execute("INSERT INTO viewer_plan_priority VALUES (101,'alpha',9)")
        await self.db.conn.execute("UPDATE tracked_channels SET added_at=123,auto_report_enabled=1,report_format='full' WHERE chat_id=101")
        await self.db.conn.commit()
        token=await self.db.remove_channel_with_undo(101,'alpha')
        other=Database(self.db._path);await other.connect()
        try:self.assertEqual(await other.undo_channel_removal(101,token['undo_token']),('restored','alpha'))
        finally:await other.close()
        for table,column,value in [('viewer_alert_filters','version',5),('category_alert_preferences','version',8),('viewer_video_selection_state','version',5),('viewer_plan_priority','priority',9)]:
            cursor=await self.db.conn.execute(f'SELECT {column} FROM {table} WHERE telegram_user_id=101')
            self.assertEqual((await cursor.fetchone())[0],value)
        self.assertEqual(await self.db.get_report_format(101,'alpha'),'full')
        self.assertTrue(await self.db.get_auto_report_enabled(101,'alpha'))

    async def test_mid_restore_failure_rolls_back_and_token_can_retry(self):
        token=await self.deleted()
        await self.db.conn.execute("CREATE TRIGGER fail_undo_favorite BEFORE INSERT ON viewer_favorites BEGIN SELECT RAISE(ABORT,'injected'); END")
        await self.db.conn.commit()
        async with self.request('unfollow/undo',undo_token=token) as response:self.assertEqual(response.status,503)
        self.assertNotIn('alpha',await self.db.list_channels(101))
        await self.db.conn.execute('DROP TRIGGER fail_undo_favorite');await self.db.conn.commit()
        async with self.request('unfollow/undo',undo_token=token) as response:self.assertEqual(response.status,200)

    async def test_video_change_after_delete_rejects_restore_without_partial_preferences(self):
        await self.db.add_channel(101,'alpha')
        await self.db.conn.execute("INSERT INTO viewer_video_selection_state VALUES (101,3)")
        await self.db.conn.execute("INSERT INTO viewer_video_selections VALUES (101,'11','alpha',0)")
        await self.db.conn.commit()
        token=await self.db.remove_channel_with_undo(101,'alpha')
        await self.db.conn.execute('UPDATE viewer_video_selection_state SET version=version+1');await self.db.conn.commit()
        async with self.request('unfollow/undo',undo_token=token['undo_token']) as response:self.assertEqual(response.status,409)
        self.assertNotIn('alpha',await self.db.list_channels(101))
