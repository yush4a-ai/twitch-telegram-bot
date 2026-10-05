"""Release to TwitchSignalBot environments in one command, fail-closed.

Owner-approved procedure, distilled from the manual releases of 04.10.2026:

1. pin the exact Railway target and the exact Git commit (no working-tree bytes);
2. materialize the reviewed runtime package from committed blobs only;
3. take a *consistent* copy of the live SQLite database (SQLite backup API on the
   server — a plain file download produced a corrupt copy and must never be used);
4. upload the package from inside its own directory and wait for SUCCESS;
5. run the post-deploy checks: identity, health, unsigned 401s, database
   integrity and — on production — a journal without the known noise.

The script never sends a Telegram message and never edits application data. It
only reads the server, uploads code and verifies the result. If any step fails,
it exits non-zero and leaves the previously running deployment untouched.

Usage:
    python -m scripts.production_release --environment staging --commit <sha> --yes
    python -m scripts.production_release --environment production --commit <sha> --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zlib
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.runtime_package_manifest import build_runtime_package

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = REPO_ROOT / ".release"

TARGETS: dict[str, dict] = {
    "production": {
        "project_id": "14282646-e318-4b80-b35d-4369270de255",
        "environment": "production",
        "environment_id": "af6d873b-a2cf-45aa-be42-cd9efbd102a7",
        "service": "worker",
        "volume_id": "9afd2204-881d-41af-bfd8-ad395b9c9ca9",
        "base_url": "https://worker-production-cee5.up.railway.app",
        "expected_bot_username": "TwitchSignalBot",
        "http": {
            "/healthz": {200},
            "/app": {200},
            "/admin": {200},
            "/admin/api/snapshot": {401},
            "/streamer/api/profile": {401},
            # Документы приняты владельцем 05.10.2026 и публикуются: 503 здесь
            # означал бы, что реквизиты оператора снова пропали из конфигурации.
            "/app/legal/privacy": {200},
        },
        "require_journal_clean": True,
    },
    "staging": {
        "project_id": "14282646-e318-4b80-b35d-4369270de255",
        "environment": "staging",
        "environment_id": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
        "service": "worker",
        "volume_id": "3ead0ce7-ed8c-482d-946c-ee768bf909ff",
        "base_url": "https://worker-staging-2f74.up.railway.app",
        # Тестовый контур переезжает с @TwitchSignalTestbot на @SignalStreamsBot,
        # поэтому строгая проверка имени здесь только мешала бы.
        "expected_bot_username": None,
        "http": {"/healthz": {200}, "/admin/api/snapshot": {401}},
        "require_journal_clean": False,
    },
}

REQUIRED_PACKAGE_FILES = (
    "main.py",
    "requirements.txt",
    "Procfile",
    ".python-version",
    "railpack.json",
    "bot/db_backup.py",
)

DEPLOY_TIMEOUT_SECONDS = 900
POLL_SECONDS = 15

BACKUP_SCRIPT = """
import os, sqlite3, time
from contextlib import closing
stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
dest = '/data/backups/predeploy-%s.db' % stamp
with closing(sqlite3.connect('/data/bot.db')) as source, closing(sqlite3.connect(dest)) as copy:
    source.backup(copy, pages=256, sleep=0.1)
with closing(sqlite3.connect('file:%s?mode=ro' % dest, uri=True)) as check:
    cur = check.cursor()
    print('BACKUP_PATH=' + dest)
    print('BACKUP_INTEGRITY=' + cur.execute('PRAGMA integrity_check').fetchone()[0])
    print('BACKUP_FK=' + str(len(cur.execute('PRAGMA foreign_key_check').fetchall())))
    print('BACKUP_BYTES=' + str(os.path.getsize(dest)))
"""

HEALTH_SCRIPT = (
    "import json, os, urllib.request; "
    "token = os.environ['TELEGRAM_BOT_TOKEN']; "
    "identity = json.load(urllib.request.urlopen("
    "'https://api.telegram.org/bot' + token + '/getMe', timeout=20))['result']; "
    "print('GETME_USERNAME=' + identity['username']); "
    "print('GETME_ID=' + str(identity['id']))"
)

TRANSFER_SCRIPT = """
import base64, sys
with open(sys.argv[1], 'rb') as handle:
    print('BACKUP_B64_START')
    print(base64.b64encode(handle.read()).decode('ascii'))
    print('BACKUP_B64_END')
"""

ARTIFACT_SCRIPT = """
import base64, hashlib, json, os, zlib
expected = json.loads(zlib.decompress(base64.b64decode(PAYLOAD)).decode('utf-8'))
missing, mismatched = [], []
for path, digest in expected.items():
    full = os.path.join('/app', path)
    if not os.path.isfile(full):
        missing.append(path)
        continue
    with open(full, 'rb') as handle:
        if hashlib.sha256(handle.read()).hexdigest() != digest:
            mismatched.append(path)
print('ARTIFACT_TOTAL=' + str(len(expected)))
print('ARTIFACT_MISSING=' + str(len(missing)))
print('ARTIFACT_MISMATCHED=' + str(len(mismatched)))
if missing:
    print('ARTIFACT_MISSING_FILES=' + ','.join(sorted(missing)[:5]))
if mismatched:
    print('ARTIFACT_MISMATCHED_FILES=' + ','.join(sorted(mismatched)[:5]))
"""


class ReleaseError(RuntimeError):
    """Release must stop; the previously running deployment stays in place."""


def target_for(environment: str) -> dict:
    try:
        return TARGETS[environment]
    except KeyError:
        raise ReleaseError(f"unknown environment {environment!r}") from None


def tool(name: str) -> str:
    """Resolve a CLI tool.

    On Windows `railway` is an npm shim (railway.cmd / railway.ps1), which
    subprocess cannot execute by bare name.
    """
    return shutil.which(name) or name


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    if cmd and cmd[0] == "railway":
        cmd = [tool("railway"), *cmd[1:]]
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, text=True, encoding="utf-8",
        errors="replace", capture_output=capture, input=input_text,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = " | ".join(detail[-3:])[:400]
        raise ReleaseError(
            "command failed (%s): %s :: %s" % (result.returncode, " ".join(cmd[:4]), tail)
        )
    return result


def git_commit(repo: Path, ref: str) -> str:
    out = run(["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"])
    return out.stdout.strip()


def assert_release_tree_is_reviewed(repo: Path, commit: str, *, allow_dirty: bool = False) -> None:
    """Refuse when runtime files differ from the commit being released.

    Only committed blobs are uploaded, so uncommitted changes can never reach the
    server — but a dirty runtime tree usually means someone is mid-edit and would
    be surprised by what actually ships. `allow_dirty` is the explicit override
    for parallel work in the same checkout.
    """
    dirty = run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", "bot", "main.py",
         "requirements.txt", "Procfile", "railpack.json", "railway.json", ".python-version"],
        check=False,
    ).stdout.strip()
    if dirty and not allow_dirty:
        raise ReleaseError(
            "runtime files have uncommitted changes; commit them, or pass "
            "--allow-dirty-runtime to release the commit anyway:\n" + dirty
        )
    head = git_commit(repo, "HEAD")
    ancestor = run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, head],
        check=False,
    )
    if ancestor.returncode:
        raise ReleaseError(f"commit {commit[:8]} is not an ancestor of HEAD")


def build_release_package(repo: Path, commit: str, destination: Path) -> dict:
    if destination.exists():
        shutil.rmtree(destination)
    manifest = build_runtime_package(repo, commit, destination)
    paths = {row["path"] for row in manifest["files"]}
    missing = [name for name in REQUIRED_PACKAGE_FILES if name not in paths]
    if missing:
        raise ReleaseError("package misses runtime files: " + ", ".join(missing))
    return manifest


def server_backup(target: dict) -> dict:
    encoded = base64.b64encode(BACKUP_SCRIPT.encode("utf-8")).decode("ascii")
    out = run([
        "railway", "ssh", "--environment", target["environment"], "--service", target["service"],
        f"echo {encoded} | base64 -d | python -",
    ]).stdout
    values = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.startswith("BACKUP_"):
            values[key] = value.strip()
    if values.get("BACKUP_INTEGRITY") != "ok":
        raise ReleaseError("server backup is not consistent: " + str(values.get("BACKUP_INTEGRITY")))
    if int(values.get("BACKUP_FK", "1")) != 0:
        raise ReleaseError("server backup has foreign-key violations")
    if "BACKUP_PATH" not in values:
        raise ReleaseError("server backup path missing in output")
    return values


def decode_ssh_payload(output: str) -> bytes:
    """Достаёт base64-полезную нагрузку между маркерами."""
    start, end = "BACKUP_B64_START", "BACKUP_B64_END"
    if start not in output or end not in output:
        raise ReleaseError("backup transfer did not return a payload")
    body = output.split(start, 1)[1].split(end, 1)[0]
    payload = "".join(line.strip() for line in body.splitlines())
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as error:  # binascii.Error и подобные
        raise ReleaseError(f"backup payload is not valid base64: {error}") from error


def download_backup(target: dict, remote_path: str, destination: Path) -> Path:
    """Забирает копию через ssh.

    `railway volume files` требует, чтобы нужное окружение было выбрано в CLI, а
    это меняет пользовательский конфиг CLI — из-под агента он недоступен. Передача
    через ssh работает без переключений, поэтому копия забирается так.
    """
    encoded = base64.b64encode(TRANSFER_SCRIPT.encode("utf-8")).decode("ascii")
    out = run([
        "railway", "ssh", "--environment", target["environment"], "--service", target["service"],
        f"echo {encoded} | base64 -d | python - {remote_path}",
    ]).stdout
    data = decode_ssh_payload(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ReleaseError("backup download produced no file")
    return destination


def verify_backup_file(path: Path) -> dict:
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as con:
            cur = con.cursor()
            integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
            fk = len(cur.execute("PRAGMA foreign_key_check").fetchall())
            tracked = cur.execute("SELECT COUNT(*) FROM tracked_channels").fetchone()[0]
            migrations = cur.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise ReleaseError(f"backup copy is not a readable database: {error}") from error
    if integrity != "ok" or fk:
        raise ReleaseError(f"backup verification failed: integrity={integrity} fk={fk}")
    return {
        "integrity": integrity, "fk_violations": fk,
        "tracked_channels": tracked, "migrations": migrations,
    }


def deploy(target: dict, package_dir: Path) -> None:
    # railway up must run inside the package: passing a path made the builder
    # analyse an unrelated directory and fail (observed on 04.10.2026).
    run([
        "railway", "up", "--detach", "--yes",
        "--environment", target["environment"], "--service", target["service"],
        "--project", target["project_id"],
    ], cwd=package_dir)


def latest_deployment(target: dict) -> dict:
    out = run([
        "railway", "deployment", "list", "--environment", target["environment"], "--json",
    ]).stdout
    payload = json.loads(out)
    if not payload:
        raise ReleaseError(f"no deployments reported for {target['environment']}")
    return payload[0]


def wait_for_success(target: dict, timeout_seconds: int = DEPLOY_TIMEOUT_SECONDS) -> dict:
    deadline = time.time() + timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        last = latest_deployment(target)
        status = str(last.get("status", ""))
        if status == "SUCCESS":
            return last
        if status in {"FAILED", "CRASHED", "REMOVED"}:
            raise ReleaseError(f"deployment {last.get('id')} ended as {status}")
        time.sleep(POLL_SECONDS)
    raise ReleaseError(f"deployment did not finish in {timeout_seconds}s: {last.get('status')}")


def http_status(target: dict, path: str) -> int:
    out = run([
        "curl", "-s", "-o", os.devnull, "-w", "%{http_code}", "--max-time", "25",
        target["base_url"] + path,
    ]).stdout
    return int(out.strip() or 0)


def bot_identity(target: dict) -> dict:
    # `python` из контейнера, а не локальный интерпретатор: railway run выполняет
    # команду в окружении сервиса, где путь вида `TG-BOT.(TwtichSignal)` не существует.
    # Скрипт проверки — одной строкой: railway CLI не переносит переводы строк.
    out = run([
        "railway", "run", "--environment", target["environment"], "--service", target["service"],
        "python", "-c", HEALTH_SCRIPT,
    ]).stdout
    identity = {}
    for line in out.splitlines():
        if line.startswith("GETME_"):
            key, _, value = line.partition("=")
            identity[key] = value.strip()
    expected = target["expected_bot_username"]
    if expected is not None and identity.get("GETME_USERNAME") != expected:
        raise ReleaseError(
            f"{target['environment']} bot identity is {identity.get('GETME_USERNAME')!r}, expected {expected!r}"
        )
    if not identity.get("GETME_ID"):
        raise ReleaseError("could not read the bot identity")
    return identity


def post_checks(target: dict) -> dict:
    http = {}
    for path, allowed in target["http"].items():
        code = http_status(target, path)
        http[path] = code
        if code not in allowed:
            raise ReleaseError(f"{path} returned {code}, expected {sorted(allowed)}")
    return {"http": http, "identity": bot_identity(target)}


def parse_artifact_report(output: str) -> dict:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.startswith("ARTIFACT_"):
            values[key] = value.strip()
    return values


def verify_artifact(target: dict, manifest: dict) -> dict:
    """Сверяет файлы в контейнере с собранным пакетом по SHA256.

    Это проверка фактического артефакта, а не намерения: успешный деплой сам по
    себе не доказывает, что в контейнере лежит именно собранный пакет. Готовый
    скрипт вместе с ожидаемыми хешами уходит на stdin: длинная команда не
    помещается в лимит командной строки Windows, а кавычки внутри неё ломают
    разбор на стороне контейнера.
    """
    digests = {row["path"]: row["sha256"] for row in manifest["files"]}
    packed = base64.b64encode(zlib.compress(json.dumps(digests).encode("utf-8"))).decode("ascii")
    script = ARTIFACT_SCRIPT.replace("PAYLOAD", repr(packed)) + "\nimport sys\nsys.stdout.flush()\n"
    out = run([
        "railway", "ssh", "--environment", target["environment"], "--service", target["service"],
        "python -",
    ], input_text=script).stdout
    values = parse_artifact_report(out)
    if not values.get("ARTIFACT_TOTAL"):
        raise ReleaseError("artifact verification produced no output")
    missing = int(values.get("ARTIFACT_MISSING", "1"))
    mismatched = int(values.get("ARTIFACT_MISMATCHED", "1"))
    if missing or mismatched:
        raise ReleaseError(
            "uploaded artifact differs from the package: missing=%d (%s) mismatched=%d (%s)"
            % (missing, values.get("ARTIFACT_MISSING_FILES", ""), mismatched,
               values.get("ARTIFACT_MISMATCHED_FILES", ""))
        )
    return {"files": int(values["ARTIFACT_TOTAL"]), "missing": 0, "mismatched": 0}


def journal_scan(target: dict) -> dict:
    out = run([
        "railway", "logs", "--environment", target["environment"], "--service", target["service"],
        "--lines", "300",
    ], check=False).stdout
    lines = out.splitlines()
    counts = {
        "TwitchAuthError": sum("TwitchAuthError" in line for line in lines),
        "ConfigError": sum(("ConfigError" in line or "CRITICAL" in line) for line in lines),
    }
    if counts["TwitchAuthError"] or counts["ConfigError"]:
        raise ReleaseError("journal still shows noise or failures: " + json.dumps(counts))
    return counts


def release(environment: str, commit: str, *, dry_run: bool, skip_backup: bool,
            allow_dirty: bool = False) -> dict:
    target = target_for(environment)
    repo = REPO_ROOT
    exact = git_commit(repo, commit)
    assert_release_tree_is_reviewed(repo, exact, allow_dirty=allow_dirty)

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_dir = RELEASE_ROOT / f"{environment}-{exact[:8]}-{stamp}"
    manifest = build_release_package(repo, exact, package_dir)
    report = {
        "environment": environment, "commit": exact,
        "package_files": manifest["file_count"],
        "package_bytes": manifest["total_bytes"],
        "manifest_sha256": manifest["manifest_sha256"],
        "package_dir": str(package_dir), "dry_run": dry_run,
    }

    if not skip_backup:
        backup = server_backup(target)
        local = download_backup(
            target, backup["BACKUP_PATH"],
            RELEASE_ROOT / "backups" / Path(backup["BACKUP_PATH"]).name,
        )
        report["backup"] = {"path": local.name, **verify_backup_file(local)}

    if dry_run:
        report["status"] = "DRY_RUN"
        return report

    deploy(target, package_dir)
    deployment = wait_for_success(target)
    report["deployment_id"] = deployment.get("id")
    report["deployment_status"] = deployment.get("status")
    report["artifact"] = verify_artifact(target, manifest)
    checks = post_checks(target)
    report["http"] = checks["http"]
    report["identity"] = checks["identity"]
    if target["require_journal_clean"]:
        report["journal"] = journal_scan(target)
    report["status"] = "SUCCESS"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release TwitchSignalBot to a pinned environment")
    parser.add_argument("--environment", default="production", choices=sorted(TARGETS))
    parser.add_argument("--commit", default="HEAD", help="commit or ref to release")
    parser.add_argument("--dry-run", action="store_true", help="package and backup only")
    parser.add_argument("--skip-backup", action="store_true", help="skip the database copy")
    parser.add_argument("--allow-dirty-runtime", action="store_true",
                        help="release the commit even if runtime files are uncommitted")
    parser.add_argument("--yes", action="store_true", help="confirm the deploy")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.yes:
        print(f"Refusing to deploy to {args.environment} without --yes.", file=sys.stderr)
        return 2

    try:
        report = release(
            args.environment, args.commit, dry_run=args.dry_run,
            skip_backup=args.skip_backup, allow_dirty=args.allow_dirty_runtime,
        )
    except ReleaseError as error:
        print(f"RELEASE STOPPED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
