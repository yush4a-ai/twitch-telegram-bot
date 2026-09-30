import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from bot.database import Database, SCHEMA
from bot.oauth import OAuthFlowError, UserTokenResult
from bot.handlers.auth import cmd_streamer_connect


class StreamerAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "bot.db")
        self.db = Database(self.path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_verified_link_is_one_to_one_and_legacy_token_is_not_identity(self):
        await self.db.save_user_token("alpha", "11", "access", "refresh", 500)
        self.assertIsNone(await self.db.get_streamer_identity(101))
        self.assertFalse(await self.db.has_streamer_plus(101, now=100))

        self.assertTrue(await self.db.link_streamer_identity(101, "11", "Alpha", verified_at=100))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertFalse(await self.db.link_streamer_identity(202, "11", "alpha", verified_at=110))
        self.assertFalse(await self.db.link_streamer_identity(101, "22", "beta", verified_at=110))
        self.assertTrue(await self.db.link_streamer_identity(101, "11", "ALPHA", verified_at=120))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))

    async def test_grant_idempotency_expiry_revoke_and_audit(self):
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        grant = await self.db.issue_test_streamer_plus(
            "11", "test-order-1", starts_at=100, expires_at=200, issued_by=425785231, now=90,
        )
        self.assertEqual(
            grant,
            await self.db.issue_test_streamer_plus(
                "11", "test-order-1", starts_at=100, expires_at=200,
                issued_by=425785231, now=95,
            ),
        )
        with self.assertRaises(ValueError):
            await self.db.issue_test_streamer_plus(
                "11", "test-order-1", starts_at=100, expires_at=201,
                issued_by=425785231, now=95,
            )
        self.assertFalse(await self.db.has_streamer_plus(101, now=99.99))
        self.assertTrue(await self.db.has_streamer_plus(101, now=100))
        self.assertTrue(await self.db.has_streamer_plus(101, now=199.99))
        self.assertFalse(await self.db.has_streamer_plus(101, now=200))
        self.assertFalse(await self.db.has_streamer_plus(202, now=150))
        self.assertTrue(await self.db.revoke_test_streamer_plus(grant, revoked_at=150, issued_by=425785231))
        self.assertFalse(await self.db.revoke_test_streamer_plus(grant, revoked_at=151, issued_by=425785231))
        self.assertFalse(await self.db.has_streamer_plus(101, now=151))
        cursor = await self.db.conn.execute("SELECT action FROM entitlement_events ORDER BY id")
        self.assertEqual([row[0] for row in await cursor.fetchall()], ["grant", "revoke"])

    async def test_invalid_grant_and_unknown_link_do_not_write(self):
        with self.assertRaises(ValueError):
            await self.db.issue_test_streamer_plus(
                "11", "key", starts_at=100, expires_at=200, issued_by=425785231,
            )
        await self.db.link_streamer_identity(101, "11", "alpha", verified_at=50)
        for key, start, expiry in (("", 100, 200), ("key", 200, 200), ("key", 201, 200)):
            with self.subTest(key=key, start=start, expiry=expiry), self.assertRaises(ValueError):
                await self.db.issue_test_streamer_plus(
                    "11", key, starts_at=start, expires_at=expiry, issued_by=425785231,
                )
        cursor = await self.db.conn.execute("SELECT COUNT(*) FROM entitlement_grants")
        self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_verified_connection_saves_token_and_identity_atomically(self):
        result = UserTokenResult("alpha", "11", "access", "refresh", 500)
        self.assertTrue(await self.db.save_verified_streamer_connection(101, result, verified_at=100))
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertEqual(await self.db.get_user_token("alpha"), ("11", "access", "refresh", 500))
        other = UserTokenResult("alpha", "11", "other", "other-refresh", 600)
        self.assertFalse(await self.db.save_verified_streamer_connection(202, other, verified_at=110))
        self.assertEqual(await self.db.get_user_token("alpha"), ("11", "access", "refresh", 500))

        await self.db.conn.execute(
            "CREATE TRIGGER fail_token_update BEFORE UPDATE ON twitch_user_tokens "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await self.db.save_verified_streamer_connection(101, other, verified_at=120)
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertEqual(await self.db.get_user_token("alpha"), ("11", "access", "refresh", 500))


class StreamerSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_additive_migration_on_old_db_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "old.db")
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(SCHEMA)
            for _ in range(2):
                db = Database(path)
                await db.connect()
                try:
                    self.assertIn("r4_001_streamer_access", await db.schema_versions())
                    cursor = await db.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                        "('streamer_identities', 'entitlement_grants', 'entitlement_events')"
                    )
                    self.assertEqual(len(await cursor.fetchall()), 3)
                finally:
                    await db.close()

    async def test_failed_streamer_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bot.db")
            db = Database(path)

            async def fail():
                await db.conn.execute("CREATE TABLE test_partial_streamer (id INTEGER)")
                raise RuntimeError("injected streamer failure")

            db._migrate_streamer_schema = fail
            try:
                with self.assertRaisesRegex(RuntimeError, "injected streamer failure"):
                    await db.connect()
            finally:
                await db.close()
            with closing(sqlite3.connect(path)) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("test_partial_streamer", names)


class StreamerConnectHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.directory.name, "bot.db"))
        await self.db.connect()
        self.config = SimpleNamespace(twitch_client_id="client", twitch_client_secret="secret")
        self.server = object()

    async def asyncTearDown(self):
        await self.db.close()
        self.directory.cleanup()

    async def test_private_verified_flow_links_captured_telegram_user(self):
        user = SimpleNamespace(id=101)
        message = SimpleNamespace(
            chat=SimpleNamespace(id=101, type="private"), from_user=user,
            answer=AsyncMock(),
        )

        async def oauth(*args, **kwargs):
            user.id = 202
            return UserTokenResult("alpha", "11", "access", "refresh", 500)

        with patch("bot.handlers.auth.run_authorization_flow", side_effect=oauth):
            await cmd_streamer_connect(message, self.db, self.config, self.server)
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertIsNone(await self.db.get_streamer_identity(202))
        self.assertEqual(message.answer.await_count, 1)

    async def test_group_or_unidentified_message_never_starts_oauth(self):
        for chat_type, user_id in (("group", 101), ("private", None), ("private", 202)):
            message = SimpleNamespace(
                chat=SimpleNamespace(id=101, type=chat_type),
                from_user=SimpleNamespace(id=user_id) if user_id is not None else None,
                answer=AsyncMock(),
            )
            with patch("bot.handlers.auth.run_authorization_flow", new_callable=AsyncMock) as oauth:
                await cmd_streamer_connect(message, self.db, self.config, self.server)
            oauth.assert_not_awaited()
        self.assertIsNone(await self.db.get_streamer_identity(101))

    async def test_oauth_error_keeps_prior_connection_and_token(self):
        await self.db.save_verified_streamer_connection(
            101, UserTokenResult("alpha", "11", "access", "refresh", 500),
            verified_at=100,
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=101, type="private"),
            from_user=SimpleNamespace(id=101), answer=AsyncMock(),
        )
        with patch("bot.handlers.auth.run_authorization_flow", side_effect=OAuthFlowError("timeout")):
            await cmd_streamer_connect(message, self.db, self.config, self.server)
        self.assertEqual(await self.db.get_streamer_identity(101), ("11", "alpha"))
        self.assertEqual(await self.db.get_user_token("alpha"), ("11", "access", "refresh", 500))


if __name__ == "__main__":
    unittest.main()
