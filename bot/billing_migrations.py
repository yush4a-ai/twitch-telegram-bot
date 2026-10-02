"""Additive R11 metadata/attempts on the existing ledger, under caller transaction."""

import re


ORDER_COLUMNS = {
    "beneficiary_telegram_user_id": "INTEGER CHECK(beneficiary_telegram_user_id > 0)",
    "catalog_version": "TEXT NOT NULL DEFAULT 'legacy-test'",
    "method": "TEXT NOT NULL DEFAULT 'mock'",
    "period_code": "TEXT NOT NULL DEFAULT 'test_duration'",
    "period_rule": "TEXT NOT NULL DEFAULT 'legacy_test_seconds'",
    "period_rule_version": "TEXT",
    "terms_version": "TEXT NOT NULL DEFAULT 'legacy-test'",
    "financial_status": "TEXT NOT NULL DEFAULT 'pending'",
    "access_starts_at": "REAL",
    "access_expires_at": "REAL",
    "product_snapshot_json": "TEXT",
}


async def migrate_plus_payments(conn, *, now: float) -> None:
    # Do not executescript/commit: DDL and all binding remain in Database._migrate.
    cursor = await conn.execute("SELECT 1 FROM schema_migrations WHERE version='r11_001_plus_payment_orders'")
    if await cursor.fetchone() is None:
        cursor = await conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='billing_orders'")
        definition = (await cursor.fetchone())[0]
        relaxed = re.sub(
            r"CHECK\s*\(duration_seconds BETWEEN 60 AND 2678400\)",
            "CHECK((currency='TEST' AND duration_seconds BETWEEN 60 AND 2678400) OR (currency IN ('RUB','XTR') AND duration_seconds >= 0))",
            definition,
        )
        if relaxed != definition:
            definition = re.sub(r'(?i)CREATE TABLE\s+["`\[]?billing_orders["`\]]?', "CREATE TABLE billing_orders_r11", relaxed, count=1)
            await conn.execute(definition)
            await conn.execute("INSERT INTO billing_orders_r11 SELECT * FROM billing_orders")
            await conn.execute("DROP TABLE billing_orders")
            await conn.execute("ALTER TABLE billing_orders_r11 RENAME TO billing_orders")
            await conn.execute("CREATE INDEX idx_billing_orders_pending_expiry ON billing_orders(status,checkout_expires_at)")
            await conn.execute("CREATE INDEX idx_billing_orders_owner ON billing_orders(telegram_user_id,created_at)")
        columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(billing_orders)")).fetchall()}
        for name, definition in ORDER_COLUMNS.items():
            if name not in columns:
                await conn.execute(f"ALTER TABLE billing_orders ADD COLUMN {name} {definition}")
        await conn.execute(
            "UPDATE billing_orders SET beneficiary_telegram_user_id=telegram_user_id, "
            "financial_status=CASE status WHEN 'paid' THEN 'confirmed' WHEN 'cancelled' THEN 'canceled' ELSE status END, "
            "access_starts_at=paid_at, access_expires_at=CASE WHEN paid_at IS NOT NULL THEN paid_at+duration_seconds END "
            "WHERE telegram_user_id>0"
        )
        await conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES ('r11_001_plus_payment_orders',?)", (now,))

    statements = (
        "CREATE TABLE IF NOT EXISTS billing_payment_attempts ("
        "attempt_id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES billing_orders(order_id),"
        "provider TEXT NOT NULL,method TEXT NOT NULL,state TEXT NOT NULL,provider_reference TEXT,"
        "created_at REAL NOT NULL,next_reconcile_at REAL,reconcile_count INTEGER NOT NULL DEFAULT 0 CHECK(reconcile_count>=0),"
        "lease_until REAL,payload_digest TEXT NOT NULL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_attempt_active ON billing_payment_attempts(order_id) "
        "WHERE state IN ('creating','pending','creation_unknown','reconciling','manual_review')",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_attempt_reference ON billing_payment_attempts(provider,provider_reference) WHERE provider_reference IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_payment_attempt_due ON billing_payment_attempts(next_reconcile_at,lease_until)",
        "CREATE TABLE IF NOT EXISTS billing_provider_inbox (provider TEXT NOT NULL,event_key TEXT NOT NULL,"
        "transaction_id TEXT NOT NULL,raw_status TEXT NOT NULL,payload_digest TEXT NOT NULL,received_at REAL NOT NULL,"
        "state TEXT NOT NULL,PRIMARY KEY(provider,event_key)) WITHOUT ROWID",
        "CREATE INDEX IF NOT EXISTS idx_provider_inbox_state ON billing_provider_inbox(provider,state,received_at)",
        "CREATE TABLE IF NOT EXISTS billing_provider_facts (provider TEXT NOT NULL,transaction_id TEXT NOT NULL,"
        "order_id TEXT NOT NULL REFERENCES billing_orders(order_id),attempt_id TEXT NOT NULL REFERENCES billing_payment_attempts(attempt_id),"
        "status TEXT NOT NULL,raw_status TEXT NOT NULL,amount_minor INTEGER NOT NULL CHECK(amount_minor>0),currency TEXT NOT NULL,"
        "method TEXT NOT NULL,observed_at REAL NOT NULL,PRIMARY KEY(provider,transaction_id)) WITHOUT ROWID",
        "CREATE TABLE IF NOT EXISTS billing_payment_refunds (request_key TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES billing_orders(order_id),"
        "actor_telegram_user_id INTEGER NOT NULL,state TEXT NOT NULL,provider_reference TEXT,requested_at REAL NOT NULL)",
    )
    for statement in statements:
        await conn.execute(statement)
    await conn.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES ('r11_002_plus_payment_events',?)", (now,))
    cursor = await conn.execute("SELECT 1 FROM schema_migrations WHERE version='r11_003_entitlement_beneficiary'")
    if await cursor.fetchone() is None:
        columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(entitlement_grants)")).fetchall()}
        if "beneficiary_telegram_user_id" not in columns:
            await conn.execute("ALTER TABLE entitlement_grants ADD COLUMN beneficiary_telegram_user_id INTEGER CHECK(beneficiary_telegram_user_id>0)")
        await conn.execute(
            "UPDATE entitlement_grants SET beneficiary_telegram_user_id=CAST(subject_id AS INTEGER) "
            "WHERE subject_kind='viewer' AND plan='viewer_plus' AND subject_id NOT GLOB '*[^0-9]*' "
            "AND CAST(subject_id AS INTEGER)>0 AND CAST(CAST(subject_id AS INTEGER) AS TEXT)=subject_id"
        )
        await conn.execute(
            "UPDATE entitlement_grants AS g SET beneficiary_telegram_user_id=("
            "SELECT o.telegram_user_id FROM billing_orders o WHERE o.grant_id=g.grant_id "
            "AND o.subject_kind=g.subject_kind AND o.subject_id=g.subject_id AND o.plan=g.plan "
            "AND o.provider='mock' AND o.currency='TEST' AND o.status='paid' AND o.telegram_user_id>0) "
            "WHERE g.subject_kind='streamer' AND g.plan='streamer_plus' AND g.source='mock'"
        )
        await conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES ('r11_003_entitlement_beneficiary',?)", (now,))
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entitlement_beneficiary ON entitlement_grants(beneficiary_telegram_user_id,plan,starts_at,expires_at) WHERE revoked_at IS NULL"
    )
    for table in ("billing_orders", "entitlement_grants"):
        await conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_frozen_beneficiary BEFORE UPDATE OF beneficiary_telegram_user_id ON {table} "
            "WHEN OLD.beneficiary_telegram_user_id IS NOT NULL AND NEW.beneficiary_telegram_user_id IS NOT OLD.beneficiary_telegram_user_id "
            "BEGIN SELECT RAISE(ABORT,'frozen beneficiary'); END"
        )
