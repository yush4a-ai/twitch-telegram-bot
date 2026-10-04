"""Guard for the only environment that autonomous work may deploy to."""

import subprocess

from scripts import staging_deploy as deploy
from scripts.staging_deploy import (
    build_deploy_command,
    validate_git,
    validate_target,
)


TARGET = {
    "project_id": "project-1",
    "service_id": "service-1",
    "staging_environment_id": "staging-1",
    "production_environment_id": "production-1",
    "staging_volume_instance_id": "stage-volume-1",
    "production_volume_instance_id": "prod-volume-1",
    "staging_domain": "stage.example.test",
    "expected_production_repo": "owner/repo",
    "expected_production_branch": "main",
}


def _service(source, *, health="/healthz", status="SUCCESS"):
    return {
        "serviceId": "service-1",
        "serviceName": "worker",
        "source": source,
        "domains": {"serviceDomains": [{"domain": "stage.example.test"}]},
        "activeDeployments": [
            {
                "status": status,
                "meta": {
                    "repo": "owner/repo" if source else None,
                    "branch": "main" if source else None,
                    "serviceManifest": {
                        "deploy": {
                            "numReplicas": 1,
                            "healthcheckPath": health,
                            "healthcheckTimeout": 300,
                            "drainingSeconds": 30,
                            "overlapSeconds": 0,
                        }
                    },
                    "volumeMounts": ["/data"],
                },
            }
        ],
    }


def _environment(name, env_id, service, volume_id):
    return {
        "node": {
            "id": env_id,
            "name": name,
            "serviceInstances": {"edges": [{"node": service}]},
            "volumeInstances": {
                "edges": [
                    {
                        "node": {
                            "id": volume_id,
                            "serviceId": "service-1",
                            "mountPath": "/data",
                        }
                    }
                ]
            },
        }
    }


def _status():
    return {
        "id": "project-1",
        "environments": {
            "edges": [
                _environment("staging", "staging-1", _service(None), "stage-volume-1"),
                _environment(
                    "production",
                    "production-1",
                    _service({"repo": "owner/repo"}),
                    "prod-volume-1",
                ),
            ]
        },
    }


def test_accepts_exact_isolated_staging_target():
    assert validate_target(_status(), TARGET) == []


def test_rejects_wrong_project_or_environment_id():
    status = _status()
    status["id"] = "another-project"
    assert "project" in " ".join(validate_target(status, TARGET)).lower()
    status = _status()
    status["environments"]["edges"][0]["node"]["id"] = "wrong-stage"
    assert "staging" in " ".join(validate_target(status, TARGET)).lower()


def test_rejects_missing_or_shared_production_volume():
    status = _status()
    status["environments"]["edges"].pop()
    assert validate_target(status, TARGET)
    status = _status()
    status["environments"]["edges"][1]["node"]["volumeInstances"]["edges"][0]["node"]["id"] = "stage-volume-1"
    assert "volume" in " ".join(validate_target(status, TARGET)).lower()


def test_rejects_source_or_health_drift():
    status = _status()
    stage = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]
    stage["source"] = {"repo": "owner/repo"}
    assert "source" in " ".join(validate_target(status, TARGET)).lower()
    status = _status()
    stage = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]
    stage["activeDeployments"][0]["meta"]["serviceManifest"]["deploy"]["healthcheckPath"] = None
    assert "health" in " ".join(validate_target(status, TARGET)).lower()
    assert validate_target(status, TARGET, require_health=False) == []


def test_rejects_replica_drain_or_deployment_drift():
    for field, value in (("numReplicas", 2), ("drainingSeconds", 0), ("overlapSeconds", 5)):
        status = _status()
        deploy = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]
        deploy["meta"]["serviceManifest"]["deploy"][field] = value
        assert validate_target(status, TARGET), field
    status = _status()
    deploy = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]
    deploy["status"] = "FAILED"
    assert validate_target(status, TARGET)


def test_rejects_service_domain_mount_or_production_branch_drift():
    status = _status()
    stage = status["environments"]["edges"][0]["node"]
    stage["serviceInstances"]["edges"][0]["node"]["serviceId"] = "wrong-service"
    assert validate_target(status, TARGET)
    status = _status()
    stage = status["environments"]["edges"][0]["node"]
    stage["serviceInstances"]["edges"][0]["node"]["domains"]["serviceDomains"][0]["domain"] = "wrong.example.test"
    assert validate_target(status, TARGET)
    status = _status()
    stage = status["environments"]["edges"][0]["node"]
    stage["volumeInstances"]["edges"][0]["node"]["mountPath"] = "/other"
    assert validate_target(status, TARGET)
    status = _status()
    prod = status["environments"]["edges"][1]["node"]
    prod["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]["meta"]["branch"] = "feature"
    assert validate_target(status, TARGET)


def test_git_gate_rejects_dirty_and_main():
    assert validate_git("autonomous/twitchsignal-roadmap", "") == []
    assert validate_git("main", "")
    assert validate_git("autonomous/twitchsignal-roadmap", "?? private.txt\n")


def test_deploy_command_pins_all_three_ids_and_commit():
    command = build_deploy_command(TARGET, "a" * 40, "C:/Temp/staging-bundle/repo")
    assert command[:2] == ["railway", "up"]
    assert "." not in command
    assert command[2] == "C:/Temp/staging-bundle/repo"
    assert "--path-as-root" in command
    assert command[command.index("--project") + 1] == "project-1"
    assert command[command.index("--environment") + 1] == "staging-1"
    assert command[command.index("--service") + 1] == "service-1"
    assert "staging " + "a" * 40 in command
    assert "--no-gitignore" not in command


def test_main_refuses_dirty_tree_before_railway_upload(monkeypatch):
    commands = []

    def capture(argv):
        commands.append(argv)
        if argv[1:3] == ["branch", "--show-current"]:
            return "autonomous/twitchsignal-roadmap"
        if argv[1:3] == ["status", "--porcelain"]:
            return "?? unknown-file.txt\n"
        raise AssertionError(f"unexpected external call: {argv}")

    monkeypatch.setattr(deploy, "_capture", capture, raising=False)
    monkeypatch.setattr(deploy, "_load_target", lambda: TARGET, raising=False)
    assert deploy.main(["--deploy"]) == 2
    assert all(argv[0] != "railway" for argv in commands)


def test_railway_command_uses_windows_cmd_shim(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda name: "C:/tools/railway.CMD")
    assert deploy._resolve_executable(["railway", "status", "--json"])[0] == "C:/tools/railway.CMD"


def test_bootstrap_config_is_staging_only_and_exact():
    expected = {
        "environments": {
            "staging": {
                "deploy": {
                    "healthcheckPath": "/healthz",
                    "healthcheckTimeout": 300,
                    "drainingSeconds": 30,
                    "overlapSeconds": 0,
                }
            }
        }
    }
    assert deploy.validate_bootstrap_config(expected) == []
    assert deploy.validate_bootstrap_config({**expected, "deploy": {"healthcheckPath": "/healthz"}})
    assert deploy.validate_bootstrap_config({"environments": {"production": expected["environments"]["staging"]}})
    wrong = {"environments": {"staging": {"deploy": {**expected["environments"]["staging"]["deploy"], "overlapSeconds": 15}}}}
    assert deploy.validate_bootstrap_config(wrong)


def test_bootstrap_requires_exact_legacy_missing_health_state():
    status = _status()
    settings = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]["meta"]["serviceManifest"]["deploy"]
    settings.update(healthcheckPath=None, healthcheckTimeout=None, drainingSeconds=None, overlapSeconds=None)
    assert deploy.validate_bootstrap_target(status, TARGET) == []
    settings["healthcheckPath"] = "/wrong"
    assert deploy.validate_bootstrap_target(status, TARGET)


def test_accepts_staging_config_file_mapping_from_active_deployment():
    status = _status()
    active = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]
    settings = active["meta"]["serviceManifest"]["deploy"]
    for key in deploy.BOOTSTRAP_DEPLOY:
        settings[key] = None
    active["meta"]["propertyFileMapping"] = {
        f"deploy.{key}": f"$.environments.staging.deploy.{key}"
        for key in deploy.BOOTSTRAP_DEPLOY
    }
    assert validate_target(status, TARGET) == []
    assert deploy.validate_bootstrap_target(status, TARGET)
    active["meta"]["propertyFileMapping"]["deploy.healthcheckPath"] = "$.environments.production.deploy.healthcheckPath"
    assert validate_target(status, TARGET)


def test_committed_bundle_excludes_ignored_and_untracked_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    from scripts.runtime_package_manifest import REQUIRED_FILES
    for path in REQUIRED_FILES:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("committed\n", encoding="utf-8")
    (tmp_path / 'docs/legal/manifest.json').write_text('{"documents": []}', encoding='utf-8')
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "snapshot"], check=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("do not upload", encoding="utf-8")
    with deploy._committed_bundle(commit) as bundle:
        assert (bundle / "main.py").read_bytes() == subprocess.check_output(['git', 'show', f'{commit}:main.py'])
        assert not (bundle / ".env").exists()
        assert not (bundle / "untracked.txt").exists()


def test_wait_for_deployment_requires_terminal_success_and_active_id(monkeypatch):
    states = ["DEPLOYING", "SUCCESS", "SUCCESS"]
    active_ids = ["old", "new"]

    def capture(argv):
        if argv[1:3] == ["deployment", "list"]:
            state = states.pop(0)
            return deploy.json.dumps([{"id": "new", "status": state, "meta": {"cliMessage": "staging abc"}}])
        if argv[1:3] == ["status", "--json"]:
            return deploy.json.dumps({"active": active_ids.pop(0)})
        raise AssertionError(argv)

    monkeypatch.setattr(deploy, "_capture", capture)
    monkeypatch.setattr(deploy, "_active_deployment_check", lambda status, target, deployment_id: [] if status["active"] == deployment_id else ["not active"])
    monkeypatch.setattr(deploy.time, "sleep", lambda seconds: None)
    assert deploy._wait_for_deployment(TARGET, "abc", timeout_seconds=30) == "new"


def test_wait_for_deployment_rejects_failed_terminal_state(monkeypatch):
    monkeypatch.setattr(deploy, "_capture", lambda argv: deploy.json.dumps([
        {"id": "new", "status": "FAILED", "meta": {"cliMessage": "staging abc"}}
    ]))
    try:
        deploy._wait_for_deployment(TARGET, "abc", timeout_seconds=30)
    except RuntimeError as error:
        assert "FAILED" in str(error)
    else:
        raise AssertionError("FAILED deployment was accepted")


def test_wait_retries_transient_status_read_after_terminal_success(monkeypatch):
    status_attempts = 0

    def capture(argv):
        nonlocal status_attempts
        if argv[1:3] == ["deployment", "list"]:
            return deploy.json.dumps([
                {"id": "new", "status": "SUCCESS", "meta": {"cliMessage": "staging abc"}}
            ])
        if argv[1:3] == ["status", "--json"]:
            status_attempts += 1
            if status_attempts == 1:
                raise RuntimeError("Railway status temporarily unavailable")
            return deploy.json.dumps({"active": "new"})
        raise AssertionError(argv)

    monkeypatch.setattr(deploy, "_capture", capture)
    monkeypatch.setattr(
        deploy, "_active_deployment_check",
        lambda status, target, deployment_id: [] if status["active"] == deployment_id else ["not active"],
    )
    monkeypatch.setattr(deploy.time, "sleep", lambda seconds: None)
    assert deploy._wait_for_deployment(TARGET, "abc", timeout_seconds=30) == "new"
    assert status_attempts == 2


def test_active_deployment_check_rejects_an_old_success():
    status = _status()
    active = status["environments"]["edges"][0]["node"]["serviceInstances"]["edges"][0]["node"]["activeDeployments"][0]
    active["id"] = "old"
    assert deploy._active_deployment_check(status, TARGET, "new")
    assert deploy._active_deployment_check(status, TARGET, "old") == []


def test_wait_ignores_previous_deployment_of_same_commit(monkeypatch):
    responses = [
        [{"id": "old", "status": "SUCCESS", "meta": {"cliMessage": "staging abc"}}],
        [{"id": "new", "status": "SUCCESS", "meta": {"cliMessage": "staging abc"}}],
    ]
    def capture(argv):
        if argv[1:3] == ["deployment", "list"]:
            return deploy.json.dumps(responses.pop(0))
        if argv[1:3] == ["status", "--json"]:
            return "{}"
        raise AssertionError(argv)

    monkeypatch.setattr(deploy, "_capture", capture)
    monkeypatch.setattr(deploy, "_active_deployment_check", lambda status, target, deployment_id: [])
    monkeypatch.setattr(deploy.time, "sleep", lambda seconds: None)
    assert deploy._wait_for_deployment(TARGET, "abc", known_ids={"old"}, timeout_seconds=30) == "new"
