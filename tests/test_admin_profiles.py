import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database import Database
from bot.middlewares import ProfileMiddleware


def telegram_user(user_id=111, username="alex_live", first="Alex", last="Ivanov", language="ru"):
    return SimpleNamespace(
        id=user_id, username=username, first_name=first, last_name=last,
        language_code=language,
    )


class ProfileStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)

    async def profile_row(self, user_id=111):
        cursor = await self.db.conn.execute(
            "SELECT username,display_name,language_code,first_seen_at,profile_seen_at,last_active_at "
            "FROM telegram_user_profiles WHERE user_id=?", (user_id,),
        )
        return await cursor.fetchone()

    async def test_new_profile_writes_first_seen_and_activity(self):
        await self.db.remember_profile(111, username="alex_live", display_name="Alex Ivanov",
                                       language_code="ru", now=1000.0)
        await self.db.touch_activity(111, now=1000.0)

        row = await self.profile_row()
        self.assertEqual(row[0], "alex_live")
        self.assertEqual(row[1], "Alex Ivanov")
        self.assertEqual(row[2], "ru")
        self.assertEqual(row[3], 1000.0)
        self.assertEqual(row[5], 1000.0)

    async def test_repeat_update_keeps_first_seen_and_refreshes_username(self):
        await self.db.remember_profile(111, username="old", display_name="Alex",
                                       language_code="ru", now=1000.0)
        await self.db.remember_profile(111, username="new", display_name="Alex Ivanov",
                                       language_code="en", now=2000.0)

        row = await self.profile_row()
        self.assertEqual(row[0], "new")
        self.assertEqual(row[2], "en")
        self.assertEqual(row[3], 1000.0)
        self.assertEqual(row[4], 2000.0)

    async def test_activity_updates_without_touching_profile_time(self):
        await self.db.remember_profile(111, username="alex", display_name="Alex",
                                       language_code="ru", now=1000.0)
        await self.db.touch_activity(111, now=1500.0)

        row = await self.profile_row()
        self.assertEqual(row[4], 1000.0)
        self.assertEqual(row[5], 1500.0)


class ProfileMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def build(self, db, *, activity_seconds=300, profile_seconds=86400, retry_seconds=60):
        return ProfileMiddleware(db, activity_seconds=activity_seconds,
                                 profile_seconds=profile_seconds, retry_seconds=retry_seconds)

    async def test_middleware_writes_profile_and_activity_once(self):
        db = SimpleNamespace(remember_profile=AsyncMock(), touch_activity=AsyncMock())
        calls = []

        async def handler(event, data):
            calls.append(event.from_user.id)
            return "done"

        middleware = self.build(db)
        event = SimpleNamespace(from_user=telegram_user())
        data = {"event_from_user": event.from_user}

        self.assertEqual(await middleware(handler, event, data), "done")
        await middleware(handler, event, data)

        self.assertEqual(calls, [111, 111])
        self.assertEqual(db.remember_profile.await_count, 1)
        self.assertEqual(db.touch_activity.await_count, 1)
        self.assertEqual(db.remember_profile.await_args.kwargs["username"], "alex_live")
        self.assertEqual(db.remember_profile.await_args.kwargs["display_name"], "Alex Ivanov")

    async def test_activity_is_written_again_after_throttle_window(self):
        db = SimpleNamespace(remember_profile=AsyncMock(), touch_activity=AsyncMock())

        async def handler(event, data):
            return None

        middleware = self.build(db, activity_seconds=0)
        event = SimpleNamespace(from_user=telegram_user())
        await middleware(handler, event, {"event_from_user": event.from_user})
        await middleware(handler, event, {"event_from_user": event.from_user})

        self.assertEqual(db.touch_activity.await_count, 2)

    async def test_failed_write_waits_before_retry(self):
        db = SimpleNamespace(
            remember_profile=AsyncMock(side_effect=RuntimeError("boom")),
            touch_activity=AsyncMock(side_effect=RuntimeError("boom")),
        )

        async def handler(event, data):
            return None

        middleware = self.build(db, activity_seconds=0, retry_seconds=60)
        event = SimpleNamespace(from_user=telegram_user())
        await middleware(handler, event, {})
        await middleware(handler, event, {})

        # Метка окна обновляется только после успешной записи, поэтому сбой не
        # «съедает» сутки профиля, а короткая пауза защищает базу от долбёжки.
        self.assertEqual(db.remember_profile.await_count, 1)
        self.assertEqual(db.touch_activity.await_count, 1)

    async def test_failed_write_retries_when_retry_window_is_zero(self):
        db = SimpleNamespace(
            remember_profile=AsyncMock(side_effect=RuntimeError("boom")),
            touch_activity=AsyncMock(side_effect=RuntimeError("boom")),
        )

        async def handler(event, data):
            return None

        middleware = self.build(db, activity_seconds=0, retry_seconds=0)
        event = SimpleNamespace(from_user=telegram_user())
        await middleware(handler, event, {})
        await middleware(handler, event, {})

        self.assertEqual(db.remember_profile.await_count, 2)
        self.assertEqual(db.touch_activity.await_count, 2)

    async def test_successful_write_clears_retry_marker(self):
        db = SimpleNamespace(
            remember_profile=AsyncMock(side_effect=[RuntimeError("boom"), None]),
            touch_activity=AsyncMock(),
        )

        async def handler(event, data):
            return None

        middleware = self.build(db, retry_seconds=0)
        event = SimpleNamespace(from_user=telegram_user())
        await middleware(handler, event, {})
        await middleware(handler, event, {})
        await middleware(handler, event, {})

        # После успешной записи окно профиля начинается заново.
        self.assertEqual(db.remember_profile.await_count, 2)

    async def test_database_failure_does_not_break_the_handler(self):
        db = SimpleNamespace(
            remember_profile=AsyncMock(side_effect=RuntimeError("secret /data/bot.db")),
            touch_activity=AsyncMock(side_effect=RuntimeError("secret /data/bot.db")),
        )
        calls = []

        async def handler(event, data):
            calls.append(1)
            return "ok"

        middleware = self.build(db)
        event = SimpleNamespace(from_user=telegram_user())

        self.assertEqual(await middleware(handler, event, {}), "ok")
        self.assertEqual(calls, [1])

    async def test_updates_without_user_are_ignored(self):
        db = SimpleNamespace(remember_profile=AsyncMock(), touch_activity=AsyncMock())

        async def handler(event, data):
            return "ok"

        middleware = self.build(db)
        self.assertEqual(await middleware(handler, SimpleNamespace(), {}), "ok")
        self.assertEqual(db.remember_profile.await_count, 0)
        self.assertEqual(db.touch_activity.await_count, 0)

    async def test_missing_optional_profile_fields_are_stored_as_none(self):
        db = SimpleNamespace(remember_profile=AsyncMock(), touch_activity=AsyncMock())

        async def handler(event, data):
            return None

        middleware = self.build(db)
        user = SimpleNamespace(id=222, username=None, first_name=None, last_name=None,
                               language_code=None)
        await middleware(handler, SimpleNamespace(from_user=user), {})

        kwargs = db.remember_profile.await_args.kwargs
        self.assertIsNone(kwargs["username"])
        self.assertIsNone(kwargs["display_name"])
        self.assertIsNone(kwargs["language_code"])


if __name__ == "__main__":
    unittest.main()
