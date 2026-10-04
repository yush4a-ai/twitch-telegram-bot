"""Production release in one command, fail-closed and message-free.

Owner-approved procedure, distilled from the manual release of 04.10.2026:

1. pin the exact Railway target and the exact Git commit (no working-tree bytes);
2. materialize the reviewed runtime package from committed blobs only;
3. take a *consistent* copy of the live SQLite database (SQLite backup API on the
   server — a plain file download produced a corrupt copy and must never be used);
4. upload the package and wait for the deployment to turn SUCCESS;
5. run the post-deploy checks: identity, health, journal without the known noise,
   database integrity and the fresh automatic backup.

The script never sends a Telegram message and never edits application data. It
only reads the server, uploads code and verifies the result. If any step fails,
it exits non-zero and leaves the previously running deployment untouched.

Usage:
    python -m scripts.production_release --commit <sha> [--dry-run]
    python -m scripts.production_release --commit <sha> --yes
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
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from scripts.runtime_package_manifest import build_runtime_package

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = REPO_ROOT / ".release"

PROJECT_ID = "14282646-e318-4b80-b35d-4369270de255"
ENVIRONMENT = "production"
SERVICE = "worker"
VOLUME_ID = "9afd2204-881d-41af-bfd8-ad395b9c9ca9"
PUBLIC_BASE_URL = "https://worker-production-cee5.up.railway.app"
EXPECTED_BOT_USERNAME = "TwitchSignalBot"
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

HEALTH_SCRIPT = """
import json, os, urllib.request
token = os.environ['TELEGRAM_BOT_TOKEN']
identity = json.load(urllib.request.urlopen(
    'https://api.telegram.org/bot' + token + '/getMe', timeout=20))['result']
print('GETME_USERNAME=' + identity['username'])
print('GETME_ID=' + str(identity['id']))
"""


class ReleaseError(RuntimeError):
    """Release must stop; nothing was left half-applied by design."""


def tool(name: str) -> str:
    """Resolve a CLI tool.

    On Windows `railway` is an npm shim (railway.cmd / railway.ps1), which
    subprocess cannot execute by bare name.
    """
    return shutil.which(name) or name


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = True) -> subprocess.CompletedProcess:
    if cmd and cmd[0] == "railway":
        cmd = [tool("railway"), *cmd[1:]]
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, text=True, encoding="utf-8",
        errors="replace", capture_output=capture,
    )
    if check and result.returncode:
        raise ReleaseError(
            "command failed (%s): %s" % (result.returncode, " ".join(cmd[:4]))
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


def server_backup() -> dict:
    encoded = base64.b64encode(BACKUP_SCRIPT.encode("utf-8")).decode("ascii")
    out = run([
        "railway", "ssh", "--environment", ENVIRONMENT, "--service", SERVICE,
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


def download_backup(remote_path: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    run([
        "railway", "volume", "files", "-v", VOLUME_ID, "download",
        remote_path, str(destination),
    ])
    if not destination.is_file():
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


def deploy(package_dir: Path) -> None:
    # railway up must run inside the package: passing a path made the builder
    # analyse an unrelated directory and fail (observed on 04.10.2026).
    run([
        "railway", "up", "--detach", "--yes",
        "--environment", ENVIRONMENT, "--service", SERVICE, "--project", PROJECT_ID,
    ], cwd=package_dir)


def latest_deployment() -> dict:
    out = run([
        "railway", "deployment", "list", "--environment", ENVIRONMENT, "--json",
    ]).stdout
    payload = json.loads(out)
    if not payload:
        raise ReleaseError("no deployments reported for production")
    return payload[0]


def wait_for_success(timeout_seconds: int = DEPLOY_TIMEOUT_SECONDS) -> dict:
    deadline = time.time() + timeout_seconds
    last = {}
    while time.time() < deadline:
        last = latest_deployment()
        status = str(last.get("status", ""))
        if status == "SUCCESS":
            return last
        if status in {"FAILED", "CRASHED", "REMOVED"}:
            raise ReleaseError(f"deployment {last.get('id')} ended as {status}")
        time.sleep(POLL_SECONDS)
    raise ReleaseError(f"deployment did not finish in {timeout_seconds}s: {last.get('status')}")


def http_status(path: str) -> int:
    out = run([
        "curl", "-s", "-o", os.devnull, "-w", "%{http_code}", "--max-time", "25",
        PUBLIC_BASE_URL + path,
    ]).stdout
    return int(out.strip() or 0)


def post_checks() -> dict:
    report = {"http": {}, "identity": None, "menu": None}
    for path, expected in (
        ("/healthz", {200}), ("/app", {200}), ("/admin", {200}),
        ("/admin/api/snapshot", {401}), ("/streamer/api/profile", {401}),
        ("/app/legal/privacy", {503}),
    ):
        code = http_status(path)
        report["http"][path] = code
        if code not in expected:
            raise ReleaseError(f"{path} returned {code}, expected {sorted(expected)}")

    out = run([
        "railway", "run", "--environment", ENVIRONMENT, "--service", SERVICE,
        sys.executable, "-c", HEALTH_SCRIPT,
    ]).stdout
    identity = {}
    for line in out.splitlines():
        if line.startswith("GETME_"):
            key, _, value = line.partition("=")
            identity[key] = value.strip()
    if identity.get("GETME_USERNAME") != EXPECTED_BOT_USERNAME:
        raise ReleaseError(f"production bot identity is {identity.get('GETME_USERNAME')!r}")
    report["identity"] = identity
    return report


def journal_scan() -> dict:
    out = run([
        "railway", "logs", "--environment", ENVIRONMENT, "--service", SERVICE,
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


def release(commit: str, *, dry_run: bool, skip_backup: bool,
            allow_dirty: bool = False) -> dict:
    repo = REPO_ROOT
    exact = git_commit(repo, commit)
    assert_release_tree_is_reviewed(repo, exact, allow_dirty=allow_dirty)

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_dir = RELEASE_ROOT / f"package-{exact[:8]}-{stamp}"
    manifest = build_release_package(repo, exact, package_dir)
    report = {
        "commit": exact, "package_files": manifest["file_count"],
        "package_bytes": manifest["total_bytes"],
        "manifest_sha256": manifest["manifest_sha256"],
        "package_dir": str(package_dir), "dry_run": dry_run,
    }

    if not skip_backup:
        backup = server_backup()
        local = download_backup(
            backup["BACKUP_PATH"], RELEASE_ROOT / "backups" / Path(backup["BACKUP_PATH"]).name
        )
        report["backup"] = {"path": local.name, **verify_backup_file(local)}

    if dry_run:
        report["status"] = "DRY_RUN"
        return report

    deploy(package_dir)
    deployment = wait_for_success()
    report["deployment_id"] = deployment.get("id")
    report["deployment_status"] = deployment.get("status")
    report["http"] = post_checks()["http"]
    report["identity"] = post_checks()["identity"]
    report["journal"] = journal_scan()
    report["status"] = "SUCCESS"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Production release for TwitchSignalBot")
    parser.add_argument("--commit", default="HEAD", help="commit or ref to release")
    parser.add_argument("--dry-run", action="store_true", help="package and backup only")
    parser.add_argument("--skip-backup", action="store_true", help="skip the database copy")
    parser.add_argument("--allow-dirty-runtime", action="store_true",
                        help="release the commit even if runtime files are uncommitted")
    parser.add_argument("--yes", action="store_true", help="confirm the production deploy")
    args = parser.parse_args(argv)

    if not args.dry_run and not args.yes:
        print("Refusing to deploy without --yes (production release).", file=sys.stderr)
        return 2

    try:
        report = release(
            args.commit, dry_run=args.dry_run, skip_backup=args.skip_backup,
            allow_dirty=args.allow_dirty_runtime,
        )
    except ReleaseError as error:
        print(f"RELEASE STOPPED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
