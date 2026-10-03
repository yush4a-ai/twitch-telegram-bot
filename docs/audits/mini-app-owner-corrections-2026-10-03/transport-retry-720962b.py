"""One-time transport retry of the already-tested 720962b staging snapshot."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(r'C:/Users/yusha/Desktop/cloude/TG-BOT.(TwtichSignal)').resolve()
SHA = '720962b69c0703177437422cf29400985c529081'
EXCLUDED = ('docs/audits/', 'docs/design/')
TEMP = Path(tempfile.gettempdir()).resolve()
MANIFEST = TEMP / 'twitchsignal-corrections-package-manifest.json'
LOG = TEMP / 'twitchsignal-corrections-full-guard.log'
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from scripts import staging_deploy as guard


def digest(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def package(source, destination, expected):
    temporary = source.parent.resolve()
    if temporary.parent != TEMP or not temporary.name.startswith('twitchsignal-staging-'):
        raise RuntimeError('Bundle must be strictly within its own system TEMP directory')
    if source.resolve() != temporary / 'repo' or destination.resolve() != temporary / 'upload':
        raise RuntimeError('Unexpected bundle paths')
    if destination.exists():
        raise RuntimeError('Refusing to reuse an upload directory')
    files = {}
    for path in source.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('Symlinks are not allowed')
        if path.is_file():
            files[path.relative_to(source).as_posix()] = path
    if set(files) != set(expected):
        raise RuntimeError('Committed bundle path set differs from Git tree')
    destination.mkdir()
    kept, excluded = {}, {}
    for name, path in files.items():
        info = digest(path)
        if name.startswith(EXCLUDED):
            excluded[name] = info
            continue
        output = destination / name
        if not output.resolve().is_relative_to(destination.resolve()):
            raise RuntimeError('Path escapes upload directory')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, output)
        if digest(output) != info:
            raise RuntimeError('Copied file differs from immutable committed bundle')
        kept[name] = info
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    if actual != {n for n in expected if not n.startswith(EXCLUDED)} or actual != set(kept):
        raise RuntimeError('Filtered upload path set differs from permitted exclusions')
    return kept, excluded


def selftest():
    with tempfile.TemporaryDirectory(prefix='twitchsignal-staging-', dir=TEMP) as name:
        base = Path(name).resolve()
        source = base / 'repo'
        fixtures = {'docs/audits/screen.png': b'old image', 'docs/design/concept.png': b'concept',
                    'docs/legal/privacy.md': b'keep\r\nlegal', 'docs/audits-other.txt': b'keep',
                    'bot/mascot.png': bytes(range(256)), 'scripts/staging_target.json': b'{}',
                    'railpack.json': b'build'}
        for rel, data in fixtures.items():
            path = source / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        kept, excluded = package(source, base / 'upload', set(fixtures))
        assert set(excluded) == {'docs/audits/screen.png', 'docs/design/concept.png'}
        assert len(kept) == 5
        assert all((source / rel).read_bytes() == data for rel, data in fixtures.items())
        assert all((base / 'upload' / rel).read_bytes() == fixtures[rel] for rel in kept)
        try:
            package(source, base / 'upload', set(fixtures))
        except RuntimeError:
            pass
        else:
            raise AssertionError('Existing destination was accepted')
    print('Packaging selftest: PASS; exact exclusions, retained bytes, untouched source, fresh destination')


def checks(target, production):
    errors = guard.validate_git(guard._capture(['git', 'branch', '--show-current']),
                               guard._capture(['git', 'status', '--porcelain', '--untracked-files=normal']))
    if guard._capture(['git', 'rev-parse', 'HEAD']) != SHA:
        errors.append('HEAD differs from the already-tested snapshot')
    errors += guard.validate_bootstrap_config(json.loads(guard.BOOTSTRAP_CONFIG_FILE.read_text(encoding='utf-8')))
    status = json.loads(guard._capture(['railway', 'status', '--json']))
    errors += guard.validate_target(status, target, require_health=True)
    environment = next(e for e in guard._nodes(status, 'environments') if e['name'] == 'production')
    active = guard._instance(environment, target['service_id'])['activeDeployments']
    actual = [{'id': d['id'], 'status': d['status'], 'commit': (d.get('meta') or {}).get('commitHash'),
               'cliMessage': (d.get('meta') or {}).get('cliMessage')} for d in active]
    if actual != production:
        errors.append('Production baseline changed')
    if errors:
        raise RuntimeError('; '.join(errors))
    return actual


def main():
    selftest()
    if sys.argv[1:] == ['--selftest']:
        return
    if sys.argv[1:] != ['--retry-upload']:
        raise RuntimeError('Explicit --retry-upload required')
    raw = LOG.read_bytes()
    log = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8', errors='replace')
    required = ['commit=720962b69c07', '1491 passed, 2 skipped, 3627 subtests passed in 939.27s',
                'Failed to upload code. File too large', '413 Payload Too Large']
    if not all(s in log for s in required):
        raise RuntimeError('Missing exact successful full-suite and upload-failure evidence')
    before = json.loads((ROOT / 'docs/audits/mini-app-owner-corrections-2026-10-03/STAGING-staging-before.json').read_text(encoding='utf-8'))
    production = before['status']['production']
    target = guard._load_target()
    checks(target, production)
    names = set(guard._capture(['git', '-c', 'core.quotepath=false', 'ls-tree', '-r', '--name-only', SHA]).splitlines())
    manifest = {'status': 'PACKAGING', 'sha': SHA, 'created_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'One-time upload-only retry after HTTP 413; no application changes or permanent skip-tests path',
                'full_suite': {'result': required[1], 'log_sha256': hashlib.sha256(raw).hexdigest()},
                'excluded_prefixes': list(EXCLUDED), 'production_before': production,
                'target': {key: target[key] for key in ('project_id', 'staging_environment_id', 'service_id', 'staging_domain')},
                'byte_verification': 'SHA256 and size of every retained file against the standard immutable Git archive bundle, preserving archive newline handling'}
    def save():
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    save()
    with guard._committed_bundle(SHA) as source:
        upload = source.parent / 'upload'
        kept, excluded = package(source, upload, names)
        for required_name in ['railpack.json', 'railway.json', 'Procfile', '.python-version', 'main.py', 'requirements.txt', 'scripts/staging_target.json']:
            if required_name not in kept:
                raise RuntimeError('Missing required build/runtime file')
        manifest.update(retained_files=kept, excluded_files=excluded,
                        retained_count=len(kept), excluded_count=len(excluded),
                        retained_bytes=sum(i['bytes'] for i in kept.values()),
                        excluded_bytes=sum(i['bytes'] for i in excluded.values()))
        save()
        print(f"Verified package: {len(kept)} files / {manifest['retained_bytes']} bytes; excluded only two evidence directories", flush=True)
        checks(target, production)
        known_ids = {item['id'] for item in guard._deployment_list(target)}
        manifest.update(status='UPLOADING', known_deployments=sorted(known_ids), clean_exact_head_rechecked=True)
        save()
        code = guard._stream(guard.build_deploy_command(target, SHA, str(upload)))
        manifest['upload_exit_code'] = code
        if code:
            manifest['status'] = 'UPLOAD_FAILED'
            save()
            raise SystemExit(code)
    manifest['status'] = 'WAITING_FOR_ACTIVE_SUCCESS'
    save()
    deployment = guard._wait_for_deployment(target, SHA, known_ids=known_ids)
    checks(target, production)
    manifest.update(status='ACTIVE_SUCCESS', deployment=deployment,
                    production_after_equal=True, completed_utc=datetime.now(timezone.utc).isoformat())
    save()
    print(f'Staging transport retry confirmed: {deployment}', flush=True)


if __name__ == '__main__':
    main()
