"""Canonical, fail-closed runtime upload manifest (never the full source archive).

All bot files are retained; REQUIRED_FILES pins the reviewed minimum dependency
inventory. Update it deliberately when adding/removing runtime inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
ALLOWED_ROOTS = ("bot/", "docs/legal/")
ALLOWED_FILES = ("main.py", "requirements.txt", "railpack.json", "railway.json",
                 "Procfile", ".python-version", "scripts/staging_target.json")
REQUIRED_FILES = (
    '.python-version',
    'Procfile',
    'bot/__init__.py',
    'bot/admin_auth.py',
    'bot/admin_metrics.py',
    'bot/admin_ui/index.html',
    'bot/admin_ui/login.css',
    'bot/admin_ui/login.js',
    'bot/admin_ui/panel.css',
    'bot/admin_ui/panel.js',
    'bot/admin_web.py',
    'bot/assets/telegram-channel-guide.png',
    'bot/assets/telegram-home.png',
    'bot/assets/telegram-welcome.png',
    'bot/billing.py',
    'bot/billing_migrations.py',
    'bot/billing_models.py',
    'bot/billing_provider.py',
    'bot/billing_reconciliation.py',
    'bot/billing_store.py',
    'bot/capabilities.py',
    'bot/category_alert_store.py',
    'bot/category_alerts.py',
    'bot/chat_listener.py',
    'bot/config.py',
    'bot/database.py',
    'bot/deep_links.py',
    'bot/entitlements.py',
    'bot/follow_listener.py',
    'bot/handlers/__init__.py',
    'bot/handlers/auth.py',
    'bot/handlers/navigation.py',
    'bot/handlers/payments.py',
    'bot/handlers/streams.py',
    'bot/handlers/telegram_add.py',
    'bot/handlers/telegram_help.py',
    'bot/handlers/telegram_plus.py',
    'bot/handlers/telegram_streamer.py',
    'bot/legal_documents.py',
    'bot/legal_ui/index.html',
    'bot/legal_ui/legal.css',
    'bot/legal_web.py',
    'bot/live_post.py',
    'bot/live_preview_provider.py',
    'bot/logging_utils.py',
    'bot/login_states.py',
    'bot/middlewares.py',
    'bot/mini_app_auth.py',
    'bot/mini_app_avatars.py',
    'bot/mini_app_billing.py',
    'bot/mini_app_reports.py',
    'bot/mini_app_streamer.py',
    'bot/mini_app_streamer_plus.py',
    'bot/mini_app_ui/api.js',
    'bot/mini_app_ui/app.css',
    'bot/mini_app_ui/app.js',
    'bot/mini_app_ui/components.js',
    'bot/mini_app_ui/index.html',
    'bot/mini_app_ui/mascot-cutout.png',
    'bot/mini_app_ui/plus-mascot.png',
    'bot/mini_app_ui/profile.js',
    'bot/mini_app_ui/purchase.js',
    'bot/mini_app_ui/reports.js',
    'bot/mini_app_ui/router.js',
    'bot/mini_app_ui/streamer.js',
    'bot/mini_app_ui/streamer_posts.js',
    'bot/mini_app_ui/subscription.js',
    'bot/mini_app_ui/support.js',
    'bot/mini_app_ui/telegram.js',
    'bot/mini_app_ui/theme.js',
    'bot/mini_app_ui/viewer.js',
    'bot/mini_app_viewer.py',
    'bot/mini_app_web.py',
    'bot/notification_queue.py',
    'bot/notification_worker.py',
    'bot/oauth.py',
    'bot/oauth_result.py',
    'bot/oauth_result_ui/ui.css',
    'bot/oauth_result_ui/ui.js',
    'bot/payment_web.py',
    'bot/plan_catalog.py',
    'bot/platega_provider.py',
    'bot/poller.py',
    'bot/preview_analysis/__init__.py',
    'bot/preview_analysis/concat.py',
    'bot/preview_analysis/metrics.py',
    'bot/preview_analysis/models.py',
    'bot/preview_analysis/process.py',
    'bot/preview_analysis/selector.py',
    'bot/preview_analysis/service.py',
    'bot/preview_capture/__init__.py',
    'bot/preview_capture/buffer.py',
    'bot/preview_capture/models.py',
    'bot/preview_capture/process.py',
    'bot/preview_capture/service.py',
    'bot/preview_render/__init__.py',
    'bot/preview_render/input.py',
    'bot/preview_render/models.py',
    'bot/preview_render/probe.py',
    'bot/preview_render/process.py',
    'bot/preview_render/renderer.py',
    'bot/preview_render/storage.py',
    'bot/preview_runtime.py',
    'bot/preview_source/__init__.py',
    'bot/preview_source/models.py',
    'bot/preview_source/process.py',
    'bot/preview_source/streamlink.py',
    'bot/preview_source/twitch.py',
    'bot/production_admission.py',
    'bot/report.py',
    'bot/report_delivery.py',
    'bot/stars_provider.py',
    'bot/stream_thumbnail.py',
    'bot/streamer_auth.py',
    'bot/streamer_community.py',
    'bot/streamer_post.py',
    'bot/streamer_presets.py',
    'bot/streamer_template.py',
    'bot/streamer_ui/index.html',
    'bot/streamer_ui/login.css',
    'bot/streamer_ui/login.js',
    'bot/streamer_ui/panel.css',
    'bot/streamer_ui/panel.js',
    'bot/streamer_web.py',
    'bot/subscription_state.py',
    'bot/telegram_home.py',
    'bot/telegram_identity.py',
    'bot/telegram_lists.py',
    'bot/telegram_replay.py',
    'bot/telegram_send_budget.py',
    'bot/telegram_ui.py',
    'bot/token_store.py',
    'bot/twitch.py',
    'bot/viewer_filter.py',
    'bot/viewer_folders.py',
    'bot/viewer_history.py',
    'bot/viewer_preferences.py',
    'bot/viewer_reminders.py',
    'bot/viewer_trial.py',
    'bot/viewer_ui/app.css',
    'bot/viewer_ui/app.js',
    'bot/viewer_ui/index.html',
    'bot/viewer_undo.py',
    'bot/viewer_web.py',
    'docs/legal/PAYMENTS.md',
    'docs/legal/PRIVACY-POLICY.md',
    'docs/legal/SUPPORT.md',
    'docs/legal/TARIFFS.md',
    'docs/legal/USER-AGREEMENT.md',
    'docs/legal/manifest.json',
    'main.py',
    'railpack.json',
    'railway.json',
    'requirements.txt',
    'scripts/staging_target.json',
)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repo), *args])


def manifest_bytes(manifest: dict) -> bytes:
    """Canonical fingerprint payload; excludes only the fingerprint itself."""
    body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _validate_path(path: str) -> None:
    pure = PurePosixPath(path)
    if (pure.is_absolute() or any(part in {'', '.', '..'} for part in path.split('/'))
        or '\\' in path or ':' in path):
        raise RuntimeError('forbidden package path')
    if (any(part.lower() in {'__pycache__', '.git', '.env', 'backups', 'backup', 'output'}
            or part.lower().startswith('.env.') for part in pure.parts)
        or pure.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.pyc'}
        or path.lower().endswith(('-wal', '-shm'))):
        raise RuntimeError('forbidden runtime input')


def build_runtime_package(repo: Path, commit: str, destination: Path, *,
                          max_bytes: int = MAX_PACKAGE_BYTES) -> dict:
    """Read only committed blobs. No archive/working-tree fallback is permitted."""
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    if destination.exists():
        raise RuntimeError('package destination must not exist')
    exact_commit = _git(repo, 'rev-parse', '--verify', f'{commit}^{{commit}}').decode().strip()
    entries = []
    for record in _git(repo, 'ls-tree', '-rlz', exact_commit).split(b'\0'):
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        path = raw_path.decode('utf-8')
        if path not in ALLOWED_FILES and not path.startswith(ALLOWED_ROOTS):
            continue
        _validate_path(path)
        mode, kind, oid, size = metadata.split()
        if mode not in {b'100644', b'100755'} or kind != b'blob':
            raise RuntimeError('forbidden runtime file type')
        entries.append((path, mode.decode(), oid.decode(), int(size)))
    entries.sort()
    selected = {row[0] for row in entries}
    if set(REQUIRED_FILES) - selected:
        raise RuntimeError('missing required runtime files')
    total = sum(row[3] for row in entries)
    if not isinstance(max_bytes, int) or max_bytes <= 0 or total > max_bytes:
        raise RuntimeError('runtime package size exceeds limit')
    # Stream one exact object at a time; never buffer the full repository archive.
    files = []
    destination.mkdir(parents=True)
    with subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            for path, mode, oid, size in entries:
                process.stdin.write((oid + '\n').encode('ascii'))
                process.stdin.flush()
                header = process.stdout.readline().decode('ascii').strip().split()
                if header != [oid, 'blob', str(size)]:
                    raise RuntimeError('Git blob header mismatch')
                raw = process.stdout.read(size)
                if len(raw) != size or process.stdout.read(1) != b'\n':
                    raise RuntimeError('Git blob truncated')
                if hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + raw).hexdigest() != oid:
                    raise RuntimeError('Git blob identity mismatch')
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                target.chmod(0o755 if mode == '100755' else 0o644)
                files.append({'path': path, 'size': size, 'sha256': hashlib.sha256(raw).hexdigest(),
                              'git_blob': oid, 'mode': mode})
        finally:
            process.stdin.close()
            process.wait()
        if process.returncode:
            raise RuntimeError('Git blob reader failed')
    actual = {file.relative_to(destination).as_posix() for file in destination.rglob('*') if file.is_file()}
    if actual != selected:
        raise RuntimeError('unexpected package files')
    for row in files:
        raw = (destination / row['path']).read_bytes()
        if len(raw) != row['size'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise RuntimeError('materialized package bytes mismatch')
    legal = json.loads((destination / 'docs/legal/manifest.json').read_bytes())
    for row in legal['documents']:
        filename = row['filename']
        if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
            raise RuntimeError('forbidden legal filename')
        if 'docs/legal/' + filename not in selected:
            raise RuntimeError('missing required legal source')
    manifest = {'schema': 1, 'commit': exact_commit, 'file_count': len(files),
                'total_bytes': total, 'size_limit_bytes': max_bytes,
                'allowed_roots': list(ALLOWED_ROOTS), 'allowed_files': list(ALLOWED_FILES),
                'files': files}
    manifest['manifest_sha256'] = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    return manifest
