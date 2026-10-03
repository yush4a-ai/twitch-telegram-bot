import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.mini_app_viewer import _PublicNames


class PublicProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_name_and_avatar_share_one_bounded_metadata_request(self):
        url='https://static-cdn.jtvnw.net/jtv_user_pictures/alpha-profile_image-70x70.png'
        twitch=SimpleNamespace(get_public_profiles=AsyncMock(return_value={'alpha':{'display_name':'Alpha','profile_image_url':url},'beta':{'display_name':'Beta','profile_image_url':'https://example.test/track'}}))
        cache=_PublicNames(twitch)
        self.assertEqual(await cache.get(['alpha','beta']),{'alpha':'Alpha','beta':'Beta'})
        await cache.get(['alpha'])
        twitch.get_public_profiles.assert_awaited_once_with(['alpha','beta'])
        self.assertEqual(cache.avatar('alpha'),url)
        self.assertIsNone(cache.avatar('beta'))


class TelegramAvatarTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_is_bounded_cached_and_never_returns_a_token_url(self):
        from bot.mini_app_avatars import TelegramAvatarCache
        async def download(_path,*,destination,timeout):
            destination.write(b'\xff\xd8\xfftest-image')
        bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path='photos/0.jpg')),download_file=AsyncMock(side_effect=download))
        cache=TelegramAvatarCache(bot)
        first,second=await asyncio.gather(cache.get('photo-id'),cache.get('photo-id'))
        self.assertTrue(first.startswith('data:image/jpeg;base64,'))
        self.assertEqual(first,second)
        bot.get_file.assert_awaited_once_with('photo-id')
        self.assertNotIn('telegram.org',first)
        self.assertIsNone(await cache.get(None))

    async def test_oversized_or_non_raster_file_is_not_exposed(self):
        from bot.mini_app_avatars import TelegramAvatarCache
        for content in (b'x'*70000,b'<svg onload="danger"/>'):
            async def download(_path,*,destination,timeout):destination.write(content)
            bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path='photos/0.jpg')),download_file=download)
            self.assertIsNone(await TelegramAvatarCache(bot).get('photo-id'))
