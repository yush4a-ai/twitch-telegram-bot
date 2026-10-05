import os
import tempfile
import time
import unittest

from bot.database import Database

DAY = 86_400.0
MSK_OFFSET = 3 * 3600


class AdminPeopleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        self.addAsyncCleanup(self.db.close)
        self.now = time.time()

    async def profile(self, user_id, *, username=None, display_name=None, first_seen=None,
                      last_active=None):
        await self.db.remember_profile(
            user_id, username=username, display_name=display_name, language_code="ru",
            now=self.now if first_seen is None else first_seen,
        )
        if last_active is not None:
            await self.db.touch_activity(user_id, now=last_active)

    async def channel(self, chat_id, login):
        await self.db.conn.execute(
            "INSERT INTO tracked_channels (chat_id,twitch_login,added_at) VALUES (?,?,1)",
            (chat_id, login),
        )
        await self.db.conn.commit()

    async def grant(self, grant_id, *, subject_kind="viewer", subject_id="111",
                    plan="viewer_plus", source="manual", starts_at=None, expires_at=None,
                    beneficiary="auto"):
        base = self.now if starts_at is None else starts_at
        if beneficiary == "auto":
            beneficiary = int(subject_id) if subject_kind == "viewer" else None
        await self.db.conn.execute(
            "INSERT INTO entitlement_grants (grant_id,request_key,subject_kind,subject_id,plan,source,"
            "starts_at,expires_at,issued_by,created_at,beneficiary_telegram_user_id) "
            "VALUES (?,?,?,?,?,?,?,?,42,?,?)",
            (grant_id, f"key-{grant_id}", subject_kind, subject_id, plan, source,
             base - DAY, base + (DAY if expires_at is None else expires_at),
             base, beneficiary),
        )
        await self.db.conn.commit()

    async def test_search_matches_username_id_and_twitch_login(self):
        await self.profile(111, username="alex_live", display_name="Alex Ivanov")
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('bc1',111,'alex_stream',?)", (self.now,),
        )
        await self.db.conn.commit()

        by_username = await self.db.search_people("alex_live", filter_kind="all", limit=20, offset=0, now=self.now)
        by_id = await self.db.search_people("111", filter_kind="all", limit=20, offset=0, now=self.now)
        by_twitch = await self.db.search_people("alex_stream", filter_kind="all", limit=20, offset=0, now=self.now)

        self.assertEqual([row["user_id"] for row in by_username], [111])
        self.assertEqual([row["user_id"] for row in by_id], [111])
        self.assertEqual([row["user_id"] for row in by_twitch], [111])
        self.assertNotIn("language_code", by_username[0])

    async def test_search_puts_exact_id_first(self):
        await self.profile(111, username="111_user", display_name="Other")
        await self.profile(1110, username="close", display_name="Close")

        rows = await self.db.search_people("1110", filter_kind="all", limit=20, offset=0, now=self.now)

        self.assertEqual(rows[0]["user_id"], 1110)

    async def test_search_filters_by_plus_and_activity(self):
        await self.profile(111, username="plus_user", display_name="Plus", last_active=self.now)
        await self.profile(222, username="free_user", display_name="Free",
                           last_active=self.now - 5 * DAY)
        await self.grant("g1", subject_id="111")

        plus = await self.db.search_people("", filter_kind="plus", limit=20, offset=0, now=self.now)
        active = await self.db.search_people("", filter_kind="active_today", limit=20, offset=0, now=self.now)

        self.assertEqual([row["user_id"] for row in plus], [111])
        self.assertEqual([row["user_id"] for row in active], [111])

    async def test_person_card_includes_limits_accounts_and_activity(self):
        await self.profile(111, username="alex_live", display_name="Alex Ivanov", last_active=self.now)
        await self.channel(111, "alpha")
        await self.channel(111, "beta")
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('bc1',111,'alex_stream',?)", (self.now,),
        )
        await self.db.conn.commit()
        await self.grant("g1", subject_id="111")

        card = await self.db.person_card(111, now=self.now)

        self.assertEqual(card["user_id"], 111)
        self.assertEqual(card["display_name"], "Alex Ivanov")
        self.assertEqual(card["limits"]["channels"], {"used": 2, "limit": 200})
        self.assertEqual(card["limits"]["video"], {"used": 0, "limit": 5})
        self.assertEqual(card["twitch_login"], "alex_stream")
        self.assertEqual([item["grant_id"] for item in card["grants"]], ["g1"])
        self.assertEqual(card["last_active_at"], self.now)

    async def test_legacy_streamer_grant_gives_plus_limit(self):
        await self.profile(111, username="alex", display_name="Alex")
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('bc1',111,'alex_stream',?)", (self.now,),
        )
        await self.db.conn.commit()
        await self.grant("legacy", subject_kind="streamer", subject_id="bc1",
                         plan="streamer_plus", beneficiary=None)

        card = await self.db.person_card(111, now=self.now)

        self.assertEqual(card["limits"]["channels"]["limit"], 200)
        self.assertEqual([item["plan"] for item in card["grants"]], ["streamer_plus"])

    async def test_search_escapes_like_wildcards(self):
        await self.profile(111, username="alex", display_name="Alex")
        await self.profile(222, username="bob", display_name="Bob")

        rows = await self.db.search_people("%", filter_kind="all", limit=20, offset=0, now=self.now)
        underscore = await self.db.search_people("_", filter_kind="all", limit=20, offset=0, now=self.now)

        self.assertEqual(rows, [])
        self.assertEqual(underscore, [])

    async def test_list_row_takes_plan_and_expiry_from_one_grant(self):
        await self.profile(111, username="alex", display_name="Alex")
        await self.grant("viewer", subject_id="111", plan="viewer_plus",
                         expires_at=10 * DAY)
        await self.db.conn.execute(
            "INSERT INTO streamer_identities (broadcaster_id,telegram_user_id,twitch_login,verified_at) "
            "VALUES ('bc1',111,'alex_stream',?)", (self.now,),
        )
        await self.db.conn.commit()
        await self.grant("streamer", subject_kind="streamer", subject_id="bc1",
                         plan="streamer_plus", expires_at=5 * DAY)

        rows = await self.db.search_people("alex", filter_kind="all", limit=20, offset=0, now=self.now)

        self.assertEqual(rows[0]["plan"], "streamer_plus")
        self.assertEqual(rows[0]["expires_at"], self.now + 5 * DAY)

    async def test_person_card_returns_none_for_unknown_person(self):
        self.assertIsNone(await self.db.person_card(999, now=self.now))

    async def test_person_history_returns_reason_and_change(self):
        await self.profile(111, username="alex", display_name="Alex")
        await self.grant("g1", subject_id="111")
        await self.db.conn.execute(
            "INSERT INTO entitlement_events (grant_id,action,actor_telegram_id,happened_at,"
            "reason,reason_note,comment,previous_grant_id,previous_expires_at,new_expires_at) "
            "VALUES ('g1','extend',42,?, 'partnership',NULL,'продлили','g0',100,200)",
            (self.now,),
        )
        await self.db.conn.commit()

        events = await self.db.person_history(111, limit=10, offset=0)

        self.assertEqual(events[0]["action"], "extend")
        self.assertEqual(events[0]["reason"], "partnership")
        self.assertEqual(events[0]["previous_expires_at"], 100)
        self.assertEqual(events[0]["new_expires_at"], 200)

    async def test_active_today_counts_from_moscow_midnight(self):
        midnight = ((self.now + MSK_OFFSET) // DAY) * DAY - MSK_OFFSET
        await self.profile(111, username="today", display_name="Today",
                           last_active=midnight + 60)
        await self.profile(222, username="yesterday", display_name="Yesterday",
                           last_active=midnight - 60)

        self.assertEqual(await self.db.count_active_since(midnight), 1)

    async def test_new_users_window_is_seven_days(self):
        await self.profile(111, username="fresh", display_name="Fresh",
                           first_seen=self.now - 2 * DAY)
        await self.profile(222, username="old", display_name="Old",
                           first_seen=self.now - 10 * DAY)

        self.assertEqual(await self.db.count_first_seen_since(self.now - 7 * DAY), 1)

    async def test_activity_by_day_returns_seven_buckets(self):
        await self.profile(111, username="a", display_name="A", last_active=self.now - 1 * DAY)
        await self.profile(222, username="b", display_name="B", last_active=self.now - 1 * DAY)
        await self.profile(333, username="c", display_name="C", last_active=self.now - 3 * DAY)

        buckets = await self.db.activity_by_day(days=7, now=self.now)

        self.assertEqual(len(buckets), 7)
        self.assertEqual(sum(item["users"] for item in buckets), 3)
        self.assertEqual([item["users"] for item in buckets][-2], 2)


if __name__ == "__main__":
    unittest.main()
