import unittest
from tests import test_mini_app_viewer as viewer
from tests import test_mini_app_streamer_connect as streamer


class ViewerReportsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=viewer.MiniAppViewerTests.asyncSetUp
    asyncTearDown=viewer.MiniAppViewerTests.asyncTearDown
    request=viewer.MiniAppViewerTests.request

    async def test_free_settings_preserve_start_notification_and_format(self):
        await self.db.add_channel(101,'alpha')
        await self.db.set_personal_notify_if_subscribed(101,'alpha',False)
        async with self.request('reports/save',login='alpha',enabled=True,format='full') as response:
            self.assertEqual(response.status,200)
        self.assertFalse(await self.db.get_notify_enabled(101,'alpha'))
        self.assertTrue(await self.db.get_auto_report_enabled(101,'alpha'))
        self.assertEqual(await self.db.get_report_format(101,'alpha'),'full')
        async with self.request('reports') as response:
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['items'][0]['recipient'],'self')

    async def test_wrong_owner_unknown_row_and_invalid_fields_never_write(self):
        await self.db.add_channel(202,'alpha')
        for fields,status in [({'login':'alpha','enabled':True,'format':'full'},404),
                              ({'login':'alpha','enabled':1,'format':'full'},400),
                              ({'login':'alpha','enabled':True,'format':'pdf'},400),
                              ({'login':'alpha','enabled':True,'format':'full','user_id':202},400)]:
            async with self.request('reports/save',**fields) as response:self.assertEqual(response.status,status)
        self.assertFalse(await self.db.get_auto_report_enabled(202,'alpha'))


class StreamerReportsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=streamer.MiniAppStreamerConnectTests.asyncSetUp
    request=streamer.MiniAppStreamerConnectTests.request

    async def connect_channel(self):
        from bot.mini_app_streamer import complete_community_intent
        import time
        async with self.request('/app/api/streamer/community-intent',chat_type='channel') as response:
            intent=(await response.json())['intent_id']
        row=await self.db.get_community_intent(intent)
        self.assertTrue(await complete_community_intent(self.db,self.bot,101,row[2],-1001,now=time.time()))
        await self.db.add_channel(-1001,'alpha')
        await self.db.register_telegram_channel(-1001,'Free channel')
        await self.db.set_notify_enabled(-1001,'alpha',False)

    async def test_channel_report_is_independent_free_and_revoked_rights_block_changes(self):
        await self.connect_channel()
        async with self.request('/app/api/streamer/reports/save',chat_id=-1001,enabled=True,format='full') as response:
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['item']['recipient'],'channel')
        self.assertTrue(await self.db.get_channel_report_enabled(-1001,'alpha'))
        self.assertFalse(await self.db.get_notify_enabled(-1001,'alpha'))
        self.user_admin=False
        async with self.request('/app/api/streamer/reports/save',chat_id=-1001,enabled=False,format='brief') as response:
            self.assertEqual(response.status,403)
        self.assertEqual(await self.db.get_report_format(-1001,'alpha'),'full')

    async def test_foreign_channel_is_not_visible(self):
        await self.connect_channel()
        async with self.request('/app/api/streamer/reports',202) as response:
            self.assertEqual((await response.json())['items'],[])
        async with self.request('/app/api/streamer/reports/save',202,chat_id=-1001,enabled=True,format='full') as response:
            self.assertEqual(response.status,403)

    async def test_one_denied_channel_does_not_hide_other_settings(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        await self.connect_channel()
        await self.db.add_streamer_community(101,-1002,'Second','channel')
        await self.db.add_channel(-1002,'alpha')
        async def member(chat,user):
            return SimpleNamespace(status='member' if chat==-1001 and user==101 else 'administrator',can_post_messages=True)
        async def chat(chat):return SimpleNamespace(id=chat,type='channel',title='Channel')
        self.bot.get_chat_member=AsyncMock(side_effect=member);self.bot.get_chat=AsyncMock(side_effect=chat)
        async with self.request('/app/api/streamer/reports') as response:
            self.assertEqual(response.status,200)
            items={row['chat_id']:row for row in (await response.json())['items']}
            self.assertFalse(items[-1001]['available'])
            self.assertNotIn('enabled',items[-1001])
            self.assertTrue(items[-1002]['available'])
