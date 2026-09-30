"""Guard for the only environment that autonomous work may deploy to."""

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
    command = build_deploy_command(TARGET, "a" * 40)
    assert command[:2] == ["railway", "up"]
    assert "." not in command
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
