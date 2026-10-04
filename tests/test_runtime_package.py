"""Runtime upload contract; the full source snapshot remains in Git."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import staging_deploy


ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def test_real_deploy_bundle_excludes_tracked_audit_and_design(monkeypatch):
    monkeypatch.chdir(ROOT)
    commit = git(ROOT, 'rev-parse', 'HEAD').decode().strip()
    with staging_deploy._committed_bundle(commit) as bundle:
        assert not (bundle / 'docs/audits').exists()
        assert not (bundle / 'docs/design').exists()
        assert not (bundle / '.agents').exists()
        assert not (bundle / 'tests').exists()
        assert (bundle / 'scripts/staging_target.json').is_file()
        legal = json.loads((bundle / 'docs/legal/manifest.json').read_bytes())
        for row in legal['documents']:
            assert (bundle / 'docs/legal' / row['filename']).is_file()
        for path in git(ROOT, 'ls-tree', '-r', '--name-only', commit, 'bot').decode().splitlines():
            assert (bundle / path).read_bytes() == git(ROOT, 'show', f'{commit}:{path}')


@pytest.fixture
def source(tmp_path):
    from scripts.runtime_package_manifest import REQUIRED_FILES
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'core.autocrlf', 'false')
    for path in REQUIRED_FILES:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'committed\x00\xff\r\n')
    (repo / 'docs/legal/manifest.json').write_text(json.dumps({'documents': [{'filename': 'USER-AGREEMENT.md'}]}))
    for path in ['docs/audits/screenshot.png', 'docs/design/concept.png', 'docs/superpowers/plan.md', '.agents/skills/x.md', 'tests/test_x.py', 'scripts/dev.py', 'output/tracked.png', '.env']:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('exclude')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'fixture')
    return repo, git(repo, 'rev-parse', 'HEAD').decode().strip()


def test_exact_deterministic_manifest_and_dirty_untracked_exclusion(source, tmp_path):
    from scripts.runtime_package_manifest import build_runtime_package, manifest_bytes
    repo, commit = source
    first = build_runtime_package(repo, commit, tmp_path / 'one')
    (repo / 'main.py').write_bytes(b'dirty runtime bytes')
    (repo / 'bot/untracked.py').write_text('local')
    (repo / 'output/untracked.db').write_text('private')
    second = build_runtime_package(repo, commit, tmp_path / 'two')
    assert first == second
    assert first['manifest_sha256'] == hashlib.sha256(manifest_bytes(first)).hexdigest()
    paths = {row['path'] for row in first['files']}
    assert paths == {p.relative_to(tmp_path / 'one').as_posix() for p in (tmp_path / 'one').rglob('*') if p.is_file()}
    assert not any(p.startswith(('docs/audits/', 'docs/design/', 'docs/superpowers/', '.agents/', 'tests/', 'output/')) for p in paths)
    assert '.env' not in paths and 'scripts/dev.py' not in paths and 'bot/untracked.py' not in paths
    for row in first['files']:
        raw = git(repo, 'show', f"{commit}:{row['path']}")
        assert (tmp_path / 'two' / row['path']).read_bytes() == raw
        assert row['size'] == len(raw)
        assert row['sha256'] == hashlib.sha256(raw).hexdigest()


def test_size_gate_stops_before_materialization(source, tmp_path):
    from scripts.runtime_package_manifest import build_runtime_package
    repo, commit = source
    destination = tmp_path / 'oversize'
    with pytest.raises(RuntimeError, match='size'):
        build_runtime_package(repo, commit, destination, max_bytes=1)
    assert not destination.exists()


@pytest.mark.parametrize('missing', ['main.py', 'scripts/staging_target.json', 'bot/mini_app_ui/app.js', 'docs/legal/USER-AGREEMENT.md', 'railpack.json'])
def test_missing_required_file_stops(source, tmp_path, missing):
    from scripts.runtime_package_manifest import build_runtime_package
    repo, _ = source
    git(repo, 'rm', missing)
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'missing')
    commit = git(repo, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(RuntimeError, match='required'):
        build_runtime_package(repo, commit, tmp_path / 'missing')


@pytest.mark.parametrize('path', ['bot/.env', 'bot/private.db', 'bot/key.pem'])
def test_forbidden_file_inside_allowed_root_stops(source, tmp_path, path):
    from scripts.runtime_package_manifest import build_runtime_package
    repo, _ = source
    (repo / path).write_text('must not upload')
    git(repo, 'add', path)
    git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-qm', 'forbidden')
    commit = git(repo, 'rev-parse', 'HEAD').decode().strip()
    with pytest.raises(RuntimeError, match='forbidden'):
        build_runtime_package(repo, commit, tmp_path / 'forbidden')


def test_unexpected_existing_destination_stops(source, tmp_path):
    from scripts.runtime_package_manifest import build_runtime_package
    repo, commit = source
    destination = tmp_path / 'existing'
    destination.mkdir()
    (destination / 'unexpected').write_text('untouched')
    with pytest.raises(RuntimeError, match='destination'):
        build_runtime_package(repo, commit, destination)
    assert (destination / 'unexpected').read_text() == 'untouched'
