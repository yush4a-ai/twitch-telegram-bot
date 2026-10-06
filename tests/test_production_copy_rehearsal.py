import hashlib
import json
import os
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet


class IsolatedCopyToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.db'
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE legacy(id INTEGER PRIMARY KEY, payload TEXT)')
            conn.execute("INSERT INTO legacy VALUES(1,'saved report')")
            conn.commit()

    def test_backup_manifest_and_restore_keep_original_legacy_rows(self):
        from scripts.production_copy_rehearsal import prepare_copy, inspect_db, restore_sealed
        sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        before = inspect_db(self.source)
        result = prepare_copy(self.root, self.source, sha)
        self.assertEqual(result['integrity'], 'ok')
        self.assertEqual(result['foreign_keys'], [])
        restored = self.root/'restored.db'
        restore_sealed(self.root, self.root/'sealed.db', restored, result['sha256'])
        self.assertEqual(inspect_db(restored)['tables'], before['tables'])
        self.assertEqual(hashlib.sha256(restored.read_bytes()).hexdigest(), result['sha256'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), sha)

    def test_wrong_source_hash_stops_without_outputs(self):
        from scripts.production_copy_rehearsal import prepare_copy
        with self.assertRaises(ValueError):
            prepare_copy(self.root, self.source, '0'*64)
        self.assertFalse((self.root/'sealed.db').exists())

    def test_sealed_wal_snapshot_inspection_and_backup_create_no_source_sidecars(self):
        from scripts.production_copy_rehearsal import prepare_copy, inspect_db
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
        before = set(self.root.iterdir())
        sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        inspect_db(self.source)
        self.assertEqual(set(self.root.iterdir()), before)
        prepare_copy(self.root, self.source, sha)
        self.assertFalse(Path(str(self.source)+'-wal').exists())
        self.assertFalse(Path(str(self.source)+'-shm').exists())
        self.assertFalse(Path(str(self.root/'sealed.db')+'-wal').exists())
        self.assertFalse(Path(str(self.root/'sealed.db')+'-shm').exists())

    def test_network_guard_preserves_async_sqlite_and_denies_socket_creation(self):
        code = '''import asyncio,socket
from scripts.production_copy_rehearsal import _disable_network
async def run():
    _disable_network()
    import aiosqlite
    db=await aiosqlite.connect(':memory:')
    try:
        assert await (await db.execute('SELECT 1')).fetchall()==[(1,)]
        try: socket.socket()
        except RuntimeError: pass
        else: raise AssertionError('network was not blocked')
    finally: await db.close()
asyncio.run(run())'''
        result = subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10)
        self.assertEqual(result.returncode, 0, 'offline network guard broke async SQLite or allowed sockets')

    def test_source_outside_isolated_root_is_not_opened(self):
        from scripts.production_copy_rehearsal import prepare_copy
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(PermissionError):
                prepare_copy(Path(other), self.source, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_restore_never_replaces_source_or_stale_sidecars(self):
        from scripts.production_copy_rehearsal import prepare_copy, restore_sealed
        result = prepare_copy(self.root, self.source, hashlib.sha256(self.source.read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            restore_sealed(self.root, self.root/'sealed.db', self.source, result['sha256'])
        dest = self.root/'rollback.db'
        Path(str(dest)+'-wal').write_bytes(b'stale')
        with self.assertRaises(FileExistsError):
            restore_sealed(self.root, self.root/'sealed.db', dest, result['sha256'])
        self.assertEqual(Path(str(dest)+'-wal').read_bytes(), b'stale')

    def test_restore_refuses_backup_with_wal_instead_of_ignoring_it(self):
        from scripts.production_copy_rehearsal import prepare_copy, restore_sealed
        result = prepare_copy(self.root, self.source, hashlib.sha256(self.source.read_bytes()).hexdigest())
        Path(str(self.root/'sealed.db')+'-wal').write_bytes(b'uncheckpointed')
        with self.assertRaises(ValueError):
            restore_sealed(self.root, self.root/'sealed.db', self.root/'rollback.db', result['sha256'])
        self.assertFalse((self.root/'rollback.db').exists())

    def test_no_authorization_stops_before_any_artifact_export(self):
        from scripts.production_copy_rehearsal import rehearse
        authorization = self.root/'authorization.json'
        authorization.write_text(json.dumps({'authorized_isolated_copy':False}))
        with self.assertRaises(PermissionError):
            rehearse(self.root, self.source, authorization)
        self.assertFalse((self.root/'old-artifact').exists())

    def test_foreign_key_corruption_is_a_stop_not_an_ignored_warning(self):
        from scripts.production_copy_rehearsal import inspect_db
        with closing(sqlite3.connect(self.source)) as conn:
            conn.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); '
                'CREATE TABLE child(id INTEGER REFERENCES parent(id)); INSERT INTO child VALUES(99);')
        with self.assertRaises(ValueError):
            inspect_db(self.source)

    def test_preservation_checks_original_values_not_just_counts(self):
        from scripts.production_copy_rehearsal import inspect_db, assert_preserved
        before = inspect_db(self.source)
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute("UPDATE legacy SET payload='lost report'")
            conn.commit()
        with self.assertRaises(ValueError):
            assert_preserved(before, self.source)

    def test_unapproved_additive_table_is_rejected_even_when_legacy_rows_survive(self):
        from scripts.production_copy_rehearsal import inspect_db, assert_expected_additions
        before = inspect_db(self.source)
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE unexpected(payload TEXT)')
        after = inspect_db(self.source)
        with self.assertRaises(ValueError):
            assert_expected_additions(before, after, {'tables':{}, 'columns':{}})
        self.assertEqual(assert_expected_additions(before, after,
            {'tables':{'unexpected':['payload']}, 'columns':{}})['tables'], {'unexpected':['payload']})

    def test_synthetic_old_artifact_migrates_reopens_and_restores_without_claiming_production(self):
        from scripts.production_copy_rehearsal import _export_artifact, _offline_open, rehearse, REPO
        old_sha = 'dc9239eb0b82fb80d1788fc657205740cebc49e9'
        artifact, _ = _export_artifact(self.root, old_sha, 'seed-artifact')
        source = self.root/'synthetic-legacy.db'
        key = Fernet.generate_key().decode()
        code = '''import asyncio,os,sys
from bot.database import Database
async def run():
    db=Database(sys.argv[1],token_encryption_key=os.environ['TOKEN_ENCRYPTION_KEY'])
    await db.connect()
    try:
        await db.add_channel(101,'synthetic')
        await db.save_user_token('synthetic','123','fixture-access','fixture-refresh',2000000000)
        await db.add_stream_history(101,'synthetic','legacy-live',1700003600,3600,10,5,1,started_at='2023-11-14T22:13:20Z')
        await db.add_stream_sample(101,'synthetic','legacy-live',1700000000,10,'fixture title','Game')
    finally:
        await db.close()
asyncio.run(run())'''
        env = {k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
        env.update(PYTHONPATH=str(artifact), TOKEN_ENCRYPTION_KEY=key)
        result = subprocess.run([sys.executable,'-c',code,str(source)],cwd=artifact,env=env,capture_output=True)
        self.assertEqual(result.returncode, 0, 'synthetic old artifact seed failed')
        authorization = self.root/'authorization.json'
        authorization.write_text(json.dumps(dict(authorized_isolated_copy=True, source_kind='synthetic_fixture',
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), old_artifact_sha=old_sha,
            new_artifact_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
            expected_additions=json.loads((REPO/'tests/fixtures/production_legacy_additions.json').read_text()),
            expected_schema_versions=[
                'mini_001_viewer_preferences','mini_002_category_alerts','mini_003_category_delivery',
                'mini_004_streamer_intents','mini_005_viewer_reminders','mini_006_viewer_folders',
                'mini_007_viewer_history','mini_008_streamer_presets','mini_009_viewer_trial',
                'mini_010_viewer_favorites','mini_011_viewer_undo','prep_001_telegram_update_inbox',
                'r10_001_billing_subjects','r11_001_plus_payment_orders','r11_002_plus_payment_events',
                'r11_003_entitlement_beneficiary','r11_004_payment_reconciliation','r11_005_channel_intent_reasons',
                'r11_006_entitlement_recovery',
                'r3_001_observations','r3_002_notification_jobs','r3_003_live_update_revision',
                'r4_001_streamer_access','r4_002_streamer_communities','r4_003_streamer_templates',
                'r4_004_streamer_stats','r5_001_billing_ledger','r7_001_viewer_filters','r8_001_growth_attribution'])))
        opened = []
        def record_open(artifact, path, root):
            opened.append(path.name)
            return _offline_open(artifact, path, root)
        with patch.dict(os.environ, {'TOKEN_ENCRYPTION_KEY':key}), patch(
                'scripts.production_copy_rehearsal._offline_open', side_effect=record_open):
            evidence = rehearse(self.root, source, authorization)
        self.assertEqual(opened, ['baseline.db','migration.db','reopen.db','rollback.db'])
        self.assertEqual(evidence['production_gate'], 'NOT CLOSED')
        self.assertEqual(evidence['stop_reopen'], 'PASS')
        self.assertEqual(evidence['token_fields'], 2)
        self.assertEqual(evidence['reports_html_compatibility']['generated'], 1)
        text = (self.root/'rehearsal-evidence.json').read_text()
        self.assertNotIn('fixture-access', text)
        self.assertNotIn(key, text)
