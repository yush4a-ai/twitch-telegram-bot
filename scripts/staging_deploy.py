"""Fail-closed Railway deployment gate for TwitchSignalBot staging."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

if __package__:
    from .runtime_package_manifest import build_runtime_package
else:
    from runtime_package_manifest import build_runtime_package


EXPECTED_BRANCH = "autonomous/twitchsignal-roadmap"
TARGET_FILE = Path(__file__).with_name("staging_target.json")
BOOTSTRAP_CONFIG_FILE = Path(__file__).resolve().parent.parent / "railway.json"
BOOTSTRAP_DEPLOY = {
    "healthcheckPath": "/healthz",
    "healthcheckTimeout": 300,
    "drainingSeconds": 30,
    "overlapSeconds": 0,
}


def _nodes(container: dict, key: str) -> list[dict]:
    edges = container.get(key, {}).get("edges", [])
    return [edge["node"] for edge in edges if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]


def _instance(environment: dict, service_id: str) -> dict | None:
    matches = [node for node in _nodes(environment, "serviceInstances") if node.get("serviceId") == service_id]
    return matches[0] if len(matches) == 1 else None


def _volume(environment: dict, service_id: str) -> dict | None:
    matches = [node for node in _nodes(environment, "volumeInstances") if node.get("serviceId") == service_id]
    return matches[0] if len(matches) == 1 else None


def validate_target(status: dict, target: dict, require_health: bool = True) -> list[str]:
    """Return all safe-to-display reasons why this is not the pinned staging target."""
    errors: list[str] = []
    if status.get("id") != target["project_id"]:
        errors.append("Railway project ID не совпадает")

    environments = _nodes(status, "environments")
    stage_matches = [e for e in environments if e.get("name") == "staging"]
    prod_matches = [e for e in environments if e.get("name") == "production"]
    stage = stage_matches[0] if len(stage_matches) == 1 else None
    prod = prod_matches[0] if len(prod_matches) == 1 else None
    if stage is None or stage.get("id") != target["staging_environment_id"]:
        errors.append("staging environment ID не совпадает или отсутствует")
    if prod is None or prod.get("id") != target["production_environment_id"]:
        errors.append("production environment ID не совпадает или отсутствует")
    if stage is None or prod is None:
        return errors

    stage_service = _instance(stage, target["service_id"])
    prod_service = _instance(prod, target["service_id"])
    if stage_service is None:
        errors.append("staging service ID не совпадает или неоднозначен")
    if prod_service is None:
        errors.append("production service ID не совпадает или неоднозначен")
    stage_volume = _volume(stage, target["service_id"])
    prod_volume = _volume(prod, target["service_id"])
    if stage_volume is None or stage_volume.get("id") != target["staging_volume_instance_id"]:
        errors.append("staging Volume ID не совпадает или отсутствует")
    if prod_volume is None or prod_volume.get("id") != target["production_volume_instance_id"]:
        errors.append("production Volume ID не совпадает или отсутствует")
    if stage_volume and prod_volume and stage_volume.get("id") == prod_volume.get("id"):
        errors.append("staging и production используют один Volume ID")
    if stage_volume and stage_volume.get("mountPath") != "/data":
        errors.append("staging Volume mount должен быть /data")
    if prod_volume and prod_volume.get("mountPath") != "/data":
        errors.append("production Volume mount должен быть /data")

    if stage_service is not None:
        if "source" not in stage_service or stage_service["source"] is not None:
            errors.append("staging source должен быть CLI, без GitHub repo")
        domains = stage_service.get("domains", {}).get("serviceDomains", [])
        if target["staging_domain"] not in [d.get("domain") for d in domains]:
            errors.append("staging domain не совпадает")
        active = stage_service.get("activeDeployments", [])
        if len(active) != 1 or active[0].get("status") != "SUCCESS":
            errors.append("staging active deployment не имеет однозначного SUCCESS")
        else:
            meta = active[0].get("meta") or {}
            settings = (meta.get("serviceManifest") or {}).get("deploy") or {}
            mapping = meta.get("propertyFileMapping") or {}
            file_override = bool(mapping) and all(
                mapping.get(f"deploy.{key}") == f"$.environments.staging.deploy.{key}"
                for key in BOOTSTRAP_DEPLOY
            )
            if mapping and not file_override:
                errors.append("staging config mapping не совпадает")
            if settings.get("numReplicas") != 1:
                errors.append("staging replicas должно быть ровно 1")
            if settings.get("overlapSeconds") not in (None, 0):
                errors.append("staging overlap должен быть 0")
            if "/data" not in (meta.get("volumeMounts") or []):
                errors.append("staging deployment не содержит /data Volume mount")
            if require_health:
                def setting_matches(key: str, expected: object) -> bool:
                    value = settings.get(key)
                    return value == expected or (file_override and value is None)

                if not setting_matches("healthcheckPath", "/healthz"):
                    errors.append("staging healthcheckPath должен быть /healthz")
                timeout = settings.get("healthcheckTimeout")
                if not ((isinstance(timeout, int) and timeout >= 180) or (file_override and timeout is None)):
                    errors.append("staging healthcheckTimeout должен быть не меньше 180")
                draining = settings.get("drainingSeconds")
                if not ((isinstance(draining, int) and draining >= 30) or (file_override and draining is None)):
                    errors.append("staging drainingSeconds должен быть не меньше 30")

    if prod_service is not None:
        source = prod_service.get("source") or {}
        if source.get("repo") != target["expected_production_repo"]:
            errors.append("production source repo изменён")
        active = prod_service.get("activeDeployments", [])
        if len(active) != 1 or (active[0].get("meta") or {}).get("branch") != target["expected_production_branch"]:
            errors.append("production branch не совпадает с main")
    return errors


def validate_git(branch: str, porcelain: str) -> list[str]:
    errors: list[str] = []
    if branch.strip() != EXPECTED_BRANCH:
        errors.append(f"ветка должна быть {EXPECTED_BRANCH}")
    if porcelain.strip():
        errors.append("Git tree должен быть чистым перед staging deploy")
    return errors


def validate_bootstrap_config(config: dict) -> list[str]:
    """Allow a one-time bootstrap only with a staging-only deployment override."""
    schema = config.get("$schema")
    if set(config) - {"$schema", "environments"} or (schema is not None and schema != "https://railway.com/railway.schema.json"):
        return ["railway.json содержит общие или неизвестные настройки"]
    environments = config.get("environments")
    if not isinstance(environments, dict) or set(environments) != {"staging"}:
        return ["railway.json должен настраивать только staging"]
    staging = environments["staging"]
    if not isinstance(staging, dict) or set(staging) != {"deploy"} or staging["deploy"] != BOOTSTRAP_DEPLOY:
        return ["railway.json staging deploy не совпадает с проверенным bootstrap"]
    return []


def validate_bootstrap_target(status: dict, target: dict) -> list[str]:
    errors = validate_target(status, target, require_health=False)
    stage = next((e for e in _nodes(status, "environments") if e.get("name") == "staging"), None)
    instance = _instance(stage, target["service_id"]) if stage else None
    deployments = instance.get("activeDeployments", []) if instance else []
    if len(deployments) == 1:
        meta = deployments[0].get("meta") or {}
        settings = (meta.get("serviceManifest") or {}).get("deploy") or {}
        mapping = meta.get("propertyFileMapping") or {}
        has_settings = any(settings.get(key) is not None for key in BOOTSTRAP_DEPLOY)
        has_mapping = any(f"deploy.{key}" in mapping for key in BOOTSTRAP_DEPLOY)
        if has_settings or has_mapping:
            errors.append("bootstrap допустим только до первой настройки staging health/drain")
    return errors


def build_deploy_command(target: dict, commit: str, bundle_path: str) -> list[str]:
    return [
        "railway", "up", bundle_path, "--path-as-root",
        "--project", target["project_id"],
        "--environment", target["staging_environment_id"],
        "--service", target["service_id"],
        "--message", f"staging {commit}",
        "--yes",
    ]


@contextmanager
def _committed_bundle(commit: str):
    """Upload exact Git runtime blobs using the canonical bounded allowlist."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="twitchsignal-staging-", dir=temp_root, ignore_cleanup_errors=True) as name:
        temporary = Path(name).resolve()
        if temporary.parent != temp_root:
            raise RuntimeError("Временный каталог deploy вне системного TEMP")
        bundle_path = temporary / "repo"
        manifest = build_runtime_package(Path.cwd(), commit, bundle_path)
        # Evidence is outside the upload directory: every uploaded file is a Git blob.
        (temporary / "runtime-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
        print(f"Runtime package: commit={manifest['commit']}; files={manifest['file_count']}; "
              f"bytes={manifest['total_bytes']}; manifest={manifest['manifest_sha256']}")
        yield bundle_path


def _load_target() -> dict:
    return json.loads(TARGET_FILE.read_text(encoding="utf-8"))


def _resolve_executable(argv: list[str]) -> list[str]:
    if argv[0] == "railway":
        executable = shutil.which("railway")
        if executable is None:
            raise RuntimeError("Railway CLI не найден")
        return [executable, *argv[1:]]
    return argv


def _capture(argv: list[str]) -> str:
    result = subprocess.run(_resolve_executable(argv), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Команда {argv[0]} завершилась с кодом {result.returncode}")
    return result.stdout.strip()


def _stream(argv: list[str]) -> int:
    return subprocess.run(_resolve_executable(argv), check=False).returncode


def _status(target: dict, require_health: bool, bootstrap: bool = False) -> list[str]:
    status = json.loads(_capture(["railway", "status", "--json"]))
    return validate_bootstrap_target(status, target) if bootstrap else validate_target(status, target, require_health=require_health)


def _active_deployment_check(status: dict, target: dict, deployment_id: str) -> list[str]:
    errors = validate_target(status, target, require_health=True)
    stage = next((e for e in _nodes(status, "environments") if e.get("name") == "staging"), None)
    instance = _instance(stage, target["service_id"]) if stage else None
    active = instance.get("activeDeployments", []) if instance else []
    if len(active) != 1 or active[0].get("id") != deployment_id:
        errors.append("новый staging deployment ещё не активен")
    return errors


def _deployment_list(target: dict) -> list[dict]:
    return json.loads(_capture([
        "railway", "deployment", "list",
        "--project", target["project_id"],
        "--environment", target["staging_environment_id"],
        "--service", target["service_id"],
        "--limit", "5", "--json",
    ]))


def _wait_for_deployment(
    target: dict, commit: str, known_ids: set[str] | None = None, timeout_seconds: int = 600
) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_state: str | None = None
    previous = known_ids or set()
    while True:
        deployments = _deployment_list(target)
        current = next(
            (
                item for item in deployments
                if item.get("id") not in previous
                and (item.get("meta") or {}).get("cliMessage") == f"staging {commit}"
            ),
            None,
        )
        if current is not None:
            deployment_id = current["id"]
            state = current["status"]
            if state != last_state:
                print(f"Staging deployment {deployment_id}: {state}")
                last_state = state
            if state in {"FAILED", "CRASHED", "REMOVED", "CANCELED"}:
                raise RuntimeError(f"Staging deployment {deployment_id}: {state}")
            if state == "SUCCESS":
                try:
                    status = json.loads(_capture(["railway", "status", "--json"]))
                except (RuntimeError, json.JSONDecodeError):
                    # Railway API can time out after reporting terminal SUCCESS;
                    # keep polling until active target validation succeeds.
                    status = None
                if status is not None and not _active_deployment_check(status, target, deployment_id):
                    return deployment_id
        if time.monotonic() >= deadline:
            raise TimeoutError("Staging deployment не достиг активного SUCCESS за отведённое время")
        time.sleep(5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка и deploy только Railway staging")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="полная проверка без deploy")
    mode.add_argument("--check-target-only", action="store_true", help="проверка target до настройки healthcheck")
    mode.add_argument("--deploy", action="store_true", help="тесты и staging deploy")
    mode.add_argument("--bootstrap-deploy", action="store_true", help="однократный staging deploy для настройки healthcheck")
    args = parser.parse_args(argv)
    try:
        target = _load_target()
        branch = _capture(["git", "branch", "--show-current"])
        porcelain = _capture(["git", "status", "--porcelain", "--untracked-files=normal"])
        errors = validate_git(branch, porcelain)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 2
        commit = _capture(["git", "rev-parse", "HEAD"])
        bootstrap = args.bootstrap_deploy
        errors += validate_bootstrap_config(json.loads(BOOTSTRAP_CONFIG_FILE.read_text(encoding="utf-8")))
        errors += _status(target, require_health=not args.check_target_only, bootstrap=bootstrap)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 2
        print(f"Staging target проверен; commit={commit[:12]}")
        if not (args.deploy or bootstrap):
            return 0
        test_code = _stream([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
        if test_code != 0:
            print("Полный suite не прошёл; deploy отменён", file=sys.stderr)
            return 1
        # Recheck both mutable state and target after the long test run.
        errors = validate_git(
            _capture(["git", "branch", "--show-current"]),
            _capture(["git", "status", "--porcelain", "--untracked-files=normal"]),
        ) + _status(target, require_health=not bootstrap, bootstrap=bootstrap)
        errors += validate_bootstrap_config(json.loads(BOOTSTRAP_CONFIG_FILE.read_text(encoding="utf-8")))
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 2
        if _capture(["git", "rev-parse", "HEAD"]) != commit:
            print("Git commit изменился во время тестов", file=sys.stderr)
            return 2
        known_ids = {item["id"] for item in _deployment_list(target)}
        with _committed_bundle(commit) as bundle:
            upload_code = _stream(build_deploy_command(target, commit, str(bundle)))
        if upload_code != 0:
            return upload_code
        deployment_id = _wait_for_deployment(target, commit, known_ids=known_ids)
        print(f"Staging deploy подтверждён: {deployment_id}")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"Staging deploy остановлен: {type(error).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
