"""R11 adds payment metadata to the existing ledger without guessing beneficiaries."""

import hashlib
import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from bot import billing_models
from bot.database import Database


def legacy_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE billing_orders (
                order_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                telegram_user_id INTEGER NOT NULL, subject_kind TEXT NOT NULL,
                subject_id TEXT NOT NULL, broadcaster_id TEXT, plan TEXT NOT NULL,
                provider TEXT NOT NULL, status TEXT NOT NULL, units INTEGER NOT NULL,
                currency TEXT NOT NULL, duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 60 AND 2678400),
                created_at REAL NOT NULL, checkout_expires_at REAL NOT NULL,
                checkout_reference TEXT, paid_at REAL, closed_at REAL, grant_id TEXT UNIQUE
            );
            CREATE TABLE entitlement_grants (
                grant_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
                subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, plan TEXT NOT NULL,
                source TEXT NOT NULL, starts_at REAL NOT NULL, expires_at REAL NOT NULL,
                revoked_at REAL, issued_by INTEGER NOT NULL, created_at REAL NOT NULL
            );
            INSERT INTO billing_orders VALUES ('old-order','old-request',101,'streamer','11','11',
                'streamer_plus','mock','paid',1,'TEST',3600,100,1000,'mock-checkout:old-order',110,NULL,'bound');
            INSERT INTO entitlement_grants VALUES ('bound','bound-request','streamer','11','streamer_plus','mock',110,3710,NULL,999,110);
            INSERT INTO entitlement_grants VALUES ('unbound','unbound-request','streamer','11','streamer_plus','test',110,3710,NULL,999,110);
            INSERT INTO entitlement_grants VALUES ('viewer','viewer-request','viewer','202','viewer_plus','test',110,3710,NULL,999,110);
            INSERT INTO entitlement_grants VALUES ('wrong','wrong-request','streamer','other','streamer_plus','test',110,3710,NULL,999,110);
            CREATE TABLE billing_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, action TEXT NOT NULL, happened_at REAL NOT NULL);
            INSERT INTO billing_audit VALUES (7,'old-order','captured',110);
            CREATE TABLE billing_payments (provider TEXT NOT NULL,payment_id TEXT NOT NULL,order_id TEXT NOT NULL,status TEXT NOT NULL,units INTEGER NOT NULL,currency TEXT NOT NULL,captured_at REAL NOT NULL,refunded_at REAL,PRIMARY KEY(provider,payment_id)) WITHOUT ROWID;
            INSERT INTO billing_payments VALUES ('mock','old-payment','old-order','captured',1,'TEST',110,NULL);
        """)


class PlusPaymentMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "legacy.db"
        legacy_database(self.path)

    async def connect(self):
        db = Database(str(self.path))
        await db.connect()
        self.addAsyncCleanup(db.close)
        return db

    async def test_migration_preserves_legacy_and_does_not_guess_streamer_buyer(self):
        backup = Path(self.directory.name) / "source-copy.db"
        backup.write_bytes(self.path.read_bytes())
        before = hashlib.sha256(backup.read_bytes()).hexdigest()
        db = await self.connect()
        columns = {r[1] for r in await (await db.conn.execute("PRAGMA table_info(entitlement_grants)")).fetchall()}
        self.assertIn("beneficiary_telegram_user_id", columns)
        bindings = dict(await (await db.conn.execute("SELECT grant_id,beneficiary_telegram_user_id FROM entitlement_grants")).fetchall())
        self.assertEqual(bindings, {"bound": 101, "unbound": None, "viewer": 202, "wrong": None})
        order = await db.get_billing_order("old-order")
        self.assertEqual((order.telegram_user_id, order.currency, order.units, order.duration_seconds, order.status, order.grant_id), (101, "TEST", 1, 3600, "paid", "bound"))
        self.assertEqual(order.beneficiary_telegram_user_id, 101)
        self.assertEqual((await db.get_billing_payment("old-order")).payment_id, "old-payment")
        self.assertEqual(await (await db.conn.execute("SELECT id,action FROM billing_audit")).fetchall(), [(7, "captured")])
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), before)
        self.assertEqual((await (await db.conn.execute("PRAGMA integrity_check")).fetchone())[0], "ok")
        self.assertEqual(await (await db.conn.execute("PRAGMA foreign_key_check")).fetchall(), [])
        with self.assertRaises(sqlite3.IntegrityError):
            await db.conn.execute("UPDATE billing_orders SET beneficiary_telegram_user_id=999 WHERE order_id='old-order'")
        await db.conn.rollback()

        await db.conn.execute("INSERT INTO billing_provider_inbox(provider,event_key,transaction_id,raw_status,payload_digest,received_at,state) VALUES ('platega','event','txn','CONFIRMED','digest',201,'pending')")
        await db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await db.conn.execute("INSERT INTO billing_provider_inbox(provider,event_key,transaction_id,raw_status,payload_digest,received_at,state) VALUES ('platega','event','txn','CONFIRMED','other',202,'pending')")
        await db.conn.rollback()

    async def test_migration_is_atomic_repeatable_and_rolls_back(self):
        from bot import database
        migration = getattr(database, "migrate_plus_payments", None)
        self.assertTrue(callable(migration), "R11 migration must run in the existing transaction")

        async def failed_migration(conn, *, now):
            await migration(conn, now=now)
            raise RuntimeError("injected post-migration failure")

        with patch.object(database, "migrate_plus_payments", failed_migration):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                await Database(str(self.path)).connect()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertNotIn("beneficiary_telegram_user_id", {r[1] for r in conn.execute("PRAGMA table_info(entitlement_grants)")})
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='billing_payment_attempts'").fetchone())
        db = await self.connect()
        await db.close()
        second = await self.connect()
        versions = await second.schema_versions()
        for version in ("r11_001_plus_payment_orders", "r11_002_plus_payment_events", "r11_003_entitlement_beneficiary", "r11_004_payment_reconciliation"):
            self.assertEqual(versions.count(version), 1)
        self.assertEqual((await (await second.conn.execute("SELECT count(*) FROM entitlement_grants")).fetchone())[0], 4)
        with self.assertRaises(sqlite3.IntegrityError):
            await second.conn.execute("INSERT INTO billing_payment_attempts(attempt_id,order_id,provider,method,state,created_at,payload_digest) VALUES ('foreign','missing','platega','sbp','creating',200,'digest')")
        await second.conn.rollback()

    async def test_provider_transaction_attempt_and_event_uniqueness(self):
        db = await self.connect()
        await db.conn.execute("INSERT INTO billing_payment_attempts(attempt_id,order_id,provider,method,state,created_at,payload_digest) VALUES ('first','old-order','platega','sbp','pending',200,'digest')")
        await db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await db.conn.execute("INSERT INTO billing_payment_attempts(attempt_id,order_id,provider,method,state,created_at,payload_digest) VALUES ('second','old-order','platega','sbp','creating',201,'digest')")
        await db.conn.rollback()

    async def test_store_uses_same_ledger_and_two_connections_keep_one_request(self):
        from bot import billing_store
        from bot.billing_models import BillingSubject, Money, ProductSnapshot, ServerOrderSnapshot, PaymentAttempt
        from dataclasses import replace
        db = await self.connect()
        other = await self.connect()
        product = ProductSnapshot("viewer_plus", "qa-v1", Money(15000, "RUB"), None,
                                  "one_month", "qa_seconds:2678500", "qa-period-v1", False, (), ("viewer_video",))
        snapshot = ServerOrderSnapshot("a" * 32, 303, 303, BillingSubject("viewer", "303"), None,
                                       product, Money(15000, "RUB"), "platega", "sbp", "qa-terms", 200, 1100)
        attempt = PaymentAttempt("attempt-a", snapshot.order_id, "platega", "sbp", "creating", None,
                                 200, None, 0, None, "digest")
        orders = await asyncio.gather(
            billing_store.BillingStore(db).create_order(snapshot, "same-request", attempt, duration_seconds=2678500),
            billing_store.BillingStore(other).create_order(replace(snapshot, order_id="b" * 32), "same-request", replace(attempt, attempt_id="attempt-b", order_id="b" * 32), duration_seconds=2678500),
        )
        self.assertEqual(orders[0].order_id, orders[1].order_id)
        self.assertEqual(orders[0].beneficiary_telegram_user_id, 303)
        self.assertEqual((await (await db.conn.execute("SELECT count(*) FROM billing_orders WHERE request_key='same-request'")).fetchone())[0], 1)
        self.assertEqual((await (await db.conn.execute("SELECT count(*) FROM billing_payment_attempts")).fetchone())[0], 1)
        self.assertEqual(orders[0].units, 15000)
        self.assertEqual(orders[0].duration_seconds, 2678500, "future calendar period is not limited by legacy TEST 31-day CHECK")
        with self.assertRaises(ValueError):
            await billing_store.BillingStore(db).create_order(replace(snapshot, money=Money(30000, "RUB")), "same-request", attempt)
        with self.assertRaises(PermissionError):
            await billing_store.BillingStore(db).create_order(replace(snapshot, beneficiary_telegram_user_id=999), "foreign-beneficiary", attempt)

    async def test_new_mock_order_freezes_server_buyer(self):
        db = await self.connect()
        order = await db.create_billing_order("c" * 32, "new-mock", 303, 3600, plan="viewer_plus", now=200)
        self.assertEqual(order.beneficiary_telegram_user_id, 303)

    async def test_attempt_failure_rolls_back_order_and_audit_in_same_database(self):
        from bot.billing_store import BillingStore
        from bot.billing_models import BillingSubject, Money, ProductSnapshot, ServerOrderSnapshot, PaymentAttempt
        db = await self.connect()
        product = ProductSnapshot("viewer_plus", "qa-v1", Money(15000, "RUB"), None,
                                  "one_month", "unapproved", None, False, (), ())
        snapshot = ServerOrderSnapshot("d" * 32, 303, 303, BillingSubject("viewer", "303"), None,
                                       product, Money(15000, "RUB"), "platega", "sbp", "qa-terms", 200, 1100)
        attempt = PaymentAttempt("attempt-fail", snapshot.order_id, "platega", "sbp", "creating", None,
                                 200, None, 0, None, "digest")
        await db.conn.execute("CREATE TEMP TRIGGER fail_attempt BEFORE INSERT ON billing_payment_attempts BEGIN SELECT RAISE(ABORT,'attempt failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "attempt failure"):
            await BillingStore(db).create_order(snapshot, "atomic-request", attempt)
        self.assertIsNone(await db.get_billing_order(snapshot.order_id))
        self.assertEqual((await (await db.conn.execute("SELECT count(*) FROM billing_audit WHERE order_id=?", (snapshot.order_id,))).fetchone())[0], 0)


class MoneyValidationTests(unittest.TestCase):
    def test_money_rejects_bool_float_nonpositive_and_unknown_currency(self):
        money = getattr(billing_models, "Money", None)
        self.assertTrue(callable(money), "Money validates server amount boundaries")
        for amount, currency in ((True, "RUB"), (150.0, "RUB"), (0, "RUB"), (-1, "XTR"), (1, "USD")):
            with self.subTest(amount=amount, currency=currency), self.assertRaises(ValueError):
                money(amount, currency)
        self.assertEqual(money(15000, "RUB").amount_minor, 15000)
