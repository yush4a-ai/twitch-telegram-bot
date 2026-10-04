"""Offline-only rehearsal on an explicitly authorized COPY under OS TEMP.

Never connects to Railway or the active DB; no main, polling, workers or network.
Evidence contains counts/hashes, never tokens or row payloads.
"""
from __future__ import annotations
import argparse
import asyncio
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]


def _contained(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if root == Path(tempfile.gettempdir()).resolve() or not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise PermissionError('Isolated root должен быть отдельным каталогом OS TEMP')
    if not path.is_relative_to(root) or path == root:
        raise PermissionError('Copy/output выходит за isolated root')
    return path


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _fingerprint(rows):
    # Preserve a multiset; ordering/rowid changes alone do not falsify preservation.
    encoded = [json.dumps(row, ensure_ascii=True, default=lambda v: {'blob': v.hex()},
                         separators=(',', ':')) for row in rows]
    return hashlib.sha256('\n'.join(sorted(encoded)).encode()).hexdigest()


def _sealed_reader(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Snapshot is not sealed: sidecars present')
    return sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)


def _snapshot_backup(root, source, destination):
    destination = _new_destination(root, destination)
    with destination.open('xb'):
        pass
    with closing(_sealed_reader(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy, pages=256, sleep=0.1)
        copy.commit()
        copy.execute('PRAGMA journal_mode=DELETE')
    inspect_db(destination)


def inspect_db(path):
    with closing(_sealed_reader(path)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('integrity_check failed')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('foreign_key_check failed')
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = {}
        for name in names:
            definitions = {r[1]: list(r[2:]) for r in conn.execute('PRAGMA table_info('+_quote(name)+')')}
            columns = list(definitions)
            rows = conn.execute('SELECT * FROM '+_quote(name)).fetchall()
            tables[name] = {'columns': columns, 'definitions': definitions,
                            'count': len(rows), 'sha256': _fingerprint(rows)}
        versions = [r[0] for r in conn.execute('SELECT version FROM schema_migrations ORDER BY version')] if 'schema_migrations' in tables else []
    return {'integrity': 'ok', 'foreign_keys': [], 'schema_versions': versions, 'tables': tables}


def assert_preserved(before, path):
    after = inspect_db(path)
    with closing(_sealed_reader(path)) as conn:
        for name, baseline in before['tables'].items():
            if name == 'schema_migrations':
                if not set(before['schema_versions']).issubset(after['schema_versions']):
                    raise ValueError('Legacy schema versions lost')
                continue
            if name not in after['tables'] or not set(baseline['columns']).issubset(after['tables'][name]['columns']):
                raise ValueError('Legacy table/columns lost: '+name)
            if any(after['tables'][name]['definitions'][column] != definition
                   for column, definition in baseline['definitions'].items()):
                raise ValueError('Legacy column definition changed: '+name)
            projection = ','.join(map(_quote, baseline['columns']))
            rows = conn.execute('SELECT '+projection+' FROM '+_quote(name)).fetchall()
            if len(rows) != baseline['count'] or _fingerprint(rows) != baseline['sha256']:
                raise ValueError('Legacy rows changed: '+name)
    return after


def assert_expected_additions(before, after, expected):
    additions = {'tables': {}, 'columns': {}}
    for name, table in after['tables'].items():
        if name not in before['tables']:
            additions['tables'][name] = table['columns']
        else:
            added = [c for c in table['columns'] if c not in before['tables'][name]['columns']]
            if added:
                additions['columns'][name] = added
    if additions != expected:
        raise ValueError('Unapproved schema additions')
    return additions


def _new_destination(root, path):
    path = _contained(root, path)
    if any(Path(str(path)+suffix).exists() for suffix in ('', '-wal', '-shm')):
        raise FileExistsError('Destination/sidecars must be absent')
    return path


def prepare_copy(root, source, expected_sha):
    source = _contained(root, source)
    if _sha(source) != expected_sha:
        raise ValueError('Source hash mismatch')
    if any(Path(str(source)+suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Source must be a sealed isolated snapshot, without active sidecars')
    inspect_db(source)
    sealed = _new_destination(root, Path(root)/'sealed.db')
    _snapshot_backup(root, source, sealed)
    evidence = inspect_db(sealed)
    evidence.update(sha256=_sha(sealed), bytes=sealed.stat().st_size,
                    source_sha256=expected_sha, utc=datetime.now(timezone.utc).isoformat())
    if _sha(source) != expected_sha or any(Path(str(source)+suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Source changed during backup')
    return evidence


def restore_sealed(root, backup, destination, expected_sha):
    backup = _contained(root, backup)
    destination = _new_destination(root, destination)
    if _sha(backup) != expected_sha:
        raise ValueError('Sealed backup hash mismatch')
    inspect_db(backup)
    # Exclusive creation, byte-identical restore into a NEW path, no sidecar deletion.
    with backup.open('rb') as source, destination.open('xb') as target:
        shutil.copyfileobj(source, target)
    if _sha(destination) != expected_sha:
        raise ValueError('Restore hash mismatch')
    inspect_db(destination)


def _export_artifact(root, sha, name):
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Exact artifact commit required')
    destination = _contained(root, Path(root)/name)
    destination.mkdir()  # no reuse of a prior mutable artifact directory
    raw = subprocess.check_output(['git', 'archive', '--format=tar', sha], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(destination, filter='data')
    return destination, hashlib.sha256(raw).hexdigest()


def _offline_open(artifact, path, root):
    output = _new_destination(root, Path(root)/(path.stem+'-open.json'))
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TOKEN_ENCRYPTION_KEY'}}
    env.update(PYTHONPATH=str(artifact), PYTHONIOENCODING='utf-8')
    # Same isolated runner imports Database from the exact exported artifact.
    command = [sys.executable, str(Path(__file__).resolve()), 'offline-open',
               '--db', str(path), '--output', str(output)]
    run = subprocess.run(command, cwd=artifact, env=env, capture_output=True, timeout=120)
    if run.returncode:
        raise ValueError('Offline artifact open failed (details intentionally not logged)')
    return json.loads(output.read_text())


def _disable_network():
    # Child-process-only audit hook keeps socket.socket a type (Windows IOCP
    # checks isinstance on its pre-existing event-loop self pipe).
    def guard(event, args):
        if event.startswith('socket.'):
            raise RuntimeError('Network prohibited in rehearsal')
    sys.addaudithook(guard)


async def offline_open(path, output):
    _disable_network()
    from bot.database import Database
    db = Database(str(path), token_encryption_key=os.environ.get('TOKEN_ENCRYPTION_KEY'))
    try:
        await db.connect()
        cipher_fields = []
        for table in ('twitch_user_tokens',):
            cols = {r[1] for r in await (await db.conn.execute('PRAGMA table_info('+_quote(table)+')')).fetchall()}
            if {'access_token', 'refresh_token'}.issubset(cols):
                rows = await (await db.conn.execute('SELECT access_token,refresh_token FROM '+_quote(table))).fetchall()
                for row in rows:
                    for value in row:
                        if value:
                            if not value.startswith('fernet:v1:'):
                                raise ValueError('Production tokens must use encryption')
                            cipher_fields.append(db._decrypt_token(value))
        # Only a digest and count are exported; token values never leave memory.
        digest = hashlib.sha256('\0'.join(sorted(cipher_fields)).encode()).hexdigest()
        versions = await db.schema_versions() if hasattr(db, 'schema_versions') else []
        from bot.report import build_report_html
        reports = await (await db.conn.execute(
            'SELECT chat_id,twitch_login,stream_id,started_at,duration_seconds,peak_viewers,avg_viewers FROM stream_history'
        )).fetchall()
        reader_samples = []
        html_count = 0
        for chat_id, login, stream_id, started, duration, peak, average in reports:
            samples = await db.get_stream_samples(chat_id, login, stream_id)
            reader_samples.append((chat_id, login, stream_id, samples))
            html = build_report_html(login, started or 'unknown', str(duration), peak, average, samples)
            if '<html' not in html.casefold() or '</html>' not in html.casefold():
                raise ValueError('Legacy HTML export failed')
            html_count += 1
        result = {'token_fields': len(cipher_fields), 'token_digest': digest, 'schema_versions': versions,
                  'html_reports': html_count, 'reader_samples_digest': _fingerprint(reader_samples),
                  'database_only': True, 'network': 'disabled'}
    finally:
        await db.close()
    with Path(output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream)


def rehearse(root, source, authorization):
    root = Path(root).resolve()
    contract = json.loads(_contained(root, authorization).read_text())
    if (contract.get('authorized_isolated_copy') is not True
            or contract.get('source_kind') not in {'owner_authorized_production_snapshot', 'synthetic_fixture'}
            or not contract.get('expected_schema_versions')
            or not isinstance(contract.get('expected_additions'), dict)):
        raise PermissionError('Explicit isolated copy authorization/expected schema required')
    old_sha, new_sha = contract['old_artifact_sha'], contract['new_artifact_sha']
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() != new_sha:
        raise ValueError('New artifact is not HEAD')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'bot', 'main.py'], cwd=REPO).returncode:
        raise ValueError('Runtime tree is not clean')
    old, old_digest = _export_artifact(root, old_sha, 'old-artifact')
    new, new_digest = _export_artifact(root, new_sha, 'new-artifact')
    backup = prepare_copy(root, source, contract['source_sha256'])
    before = inspect_db(Path(root)/'sealed.db')
    migration, baseline = root/'migration.db', root/'baseline.db'
    for path in (migration, baseline):
        restore_sealed(root, root/'sealed.db', path, backup['sha256'])
    # No old-artifact schema assertion is guessed; its exact opener is exercised.
    old_open = _offline_open(old, baseline, root)
    assert_preserved(before, baseline)
    new_open = _offline_open(new, migration, root)
    after = assert_preserved(before, migration)
    additions = assert_expected_additions(before, after, contract['expected_additions'])
    if new_open['schema_versions'] != contract['expected_schema_versions']:
        raise ValueError('Unexpected migrated schema versions')
    reopen = root/'reopen.db'
    _snapshot_backup(root, migration, reopen)
    reopen_open = _offline_open(new, reopen, root)
    assert_preserved(after, reopen)
    # New artifact connections have closed; restore pre-migration bytes into
    # another absent path and exercise the exact old artifact AFTER migration.
    rollback = root/'rollback.db'
    restore_sealed(root, root/'sealed.db', rollback, backup['sha256'])
    restored_sha = _sha(rollback)
    rollback_open = _offline_open(old, rollback, root)
    assert_preserved(before, rollback)
    if any(x['token_digest'] != old_open['token_digest'] or x['token_fields'] != old_open['token_fields']
           for x in (new_open, reopen_open, rollback_open)):
        raise ValueError('Token decryption/preservation mismatch')
    if any(x['reader_samples_digest'] != old_open['reader_samples_digest'] or x['html_reports'] != old_open['html_reports']
           for x in (new_open, reopen_open, rollback_open)):
        raise ValueError('Legacy report reader/HTML count mismatch')
    report = {'source_kind': contract['source_kind'], 'backup': backup, 'after': after,
        'schema_additions': additions,
        'old_artifact_sha': old_sha, 'new_artifact_sha': new_sha,
        'old_archive_sha256': old_digest, 'new_archive_sha256': new_digest,
        'restore_sha256': restored_sha, 'token_fields': old_open['token_fields'],
        'stop_reopen': 'PASS', 'external_backup': 'OWNER INPUT',
        'reports_html_compatibility': {'generated': old_open['html_reports'], 'reader_preserved': True,
                                      'owner_native': 'NOT TESTED'},
        'production_gate': 'NOT CLOSED' if contract['source_kind']=='synthetic_fixture' else 'REVIEW EVIDENCE'}
    output = _new_destination(root, root/'rehearsal-evidence.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('rehearse')
    run.add_argument('--isolated-root', type=Path, required=True)
    run.add_argument('--source', type=Path, required=True)
    run.add_argument('--authorization', type=Path, required=True)
    offline = sub.add_parser('offline-open')
    offline.add_argument('--db', type=Path, required=True)
    offline.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'offline-open':
        if args.db.name not in {'baseline.db', 'migration.db', 'rollback.db', 'reopen.db'}:
            raise PermissionError('Offline opener принимает только restored copy paths')
        _contained(args.db.parent, args.db)
        inspect_db(args.db)
        _new_destination(args.db.parent, args.output)
        asyncio.run(offline_open(args.db, args.output))
    else:
        rehearse(args.isolated_root, args.source, args.authorization)
        print('Isolated evidence saved; no production readiness claim.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Rehearsal STOP: '+type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
