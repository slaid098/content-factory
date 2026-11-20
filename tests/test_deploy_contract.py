from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = Path(".github/workflows/ci.yml")
_DEPLOY_SCRIPT = Path("scripts/deploy.sh")
_DEPLOY_README = Path("deploy/README.md")
_SECRETS = (
    "DEPLOY_HOST",
    "DEPLOY_PORT",
    "DEPLOY_USER",
    "DEPLOY_SSH_KEY",
    "DEPLOY_KNOWN_HOSTS",
)


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((_ROOT / name).read_text(encoding="utf-8"))


def _deploy_script() -> str:
    return (_ROOT / _DEPLOY_SCRIPT).read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    workflow = _load(str(_WORKFLOW))
    # PyYAML по правилам YAML 1.1 читает ключ `on` как boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return {**workflow, "on": triggers}


def test_compose_contract() -> None:
    services = _load("compose.yaml")["services"]

    api = services["api"]
    worker = services["worker"]

    assert api["image"] == "ghcr.io/slaid098/content-factory:${IMAGE_TAG:-latest}"
    assert api["build"] == {"context": "."}
    assert api["command"] == ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
    assert api["ports"] == ["127.0.0.1:8000:8000"]
    assert api["healthcheck"]["test"] == [
        "CMD",
        "curl",
        "-f",
        "http://127.0.0.1:8000/api/v1/stock",
    ]
    assert api["restart"] == "unless-stopped"
    assert worker["command"] == ["prefect", "worker", "start", "--pool", "content-factory"]
    assert worker["restart"] == "unless-stopped"
    assert "./app_data:/app/app_data" in api["volumes"]
    assert "./app_data:/app/app_data" in worker["volumes"]


def test_compose_delivery_contract() -> None:
    compose = _load("compose.yaml")
    services = compose["services"]
    api = services["api"]

    assert "/app/app_data/archive" in services["init"]["command"][0]
    assert api["environment"]["CONTENT_FACTORY_DELIVERY_TOKEN"] == (
        "${CONTENT_FACTORY_DELIVERY_TOKEN:-}"
    )
    assert api["environment"]["CONTENT_FACTORY_DELIVERY_RETENTION_HOURS"] == (
        "${CONTENT_FACTORY_DELIVERY_RETENTION_HOURS:-24}"
    )
    assert compose["networks"]["content-bus"]["internal"] is True
    assert api["networks"]["content-bus"]["aliases"] == ["content-factory-api"]
    assert api["ports"] == ["127.0.0.1:8000:8000"]


def test_compose_omits_security_opt_for_snap_docker() -> None:
    services = _load("compose.yaml")["services"]

    # snapd LP#1908448: AppArmor на Linux 1 запрещает exec контейнерам с
    # ограничением привилегий, поэтому security_opt в compose не задаётся.
    for name in ("init", "api", "worker"):
        assert "security_opt" not in services[name]

    assert services["api"]["cap_drop"] == ["ALL"]
    assert services["worker"]["cap_drop"] == ["ALL"]


def test_prefect_client_matches_server() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "prefect>=3.6,<3.7" in pyproject["project"]["dependencies"]


def test_workflow_triggers() -> None:
    triggers = _workflow()["on"]

    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers


def test_workflow_jobs_and_permissions() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["build-push"]["needs"] == "ci"
    assert jobs["deploy"]["needs"] == "build-push"
    assert jobs["build-push"]["permissions"] == {"contents": "read", "packages": "write"}

    for job_name in ("build-push", "deploy"):
        condition = jobs[job_name]["if"]
        assert "github.ref == 'refs/heads/main'" in condition
        assert "github.event_name == 'push'" in condition
        assert "github.event_name == 'workflow_dispatch'" in condition


def test_build_push_publishes_latest_and_short_sha() -> None:
    steps = _workflow()["jobs"]["build-push"]["steps"]
    short_sha = next(step for step in steps if step.get("id") == "image")
    build = next(step for step in steps if step.get("uses") == "docker/build-push-action@v6")

    assert "sha-${GITHUB_SHA::7}" in short_sha["run"]
    tags = build["with"]["tags"]
    assert "ghcr.io/slaid098/content-factory:latest" in tags
    assert "ghcr.io/slaid098/content-factory:${{ steps.image.outputs.tag }}" in tags


def test_deploy_job_contract() -> None:
    job = _workflow()["jobs"]["deploy"]

    assert job["concurrency"] == {"group": "deploy-production", "cancel-in-progress": False}
    for name in _SECRETS:
        assert job["env"][name] == f"${{{{ secrets.{name} }}}}"

    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    assert "sudo -n /usr/local/sbin/content-factory-deploy ${GITHUB_SHA}" in deploy["run"]
    assert "StrictHostKeyChecking=yes" in deploy["run"]


def test_deploy_script_contract() -> None:
    script = _deploy_script()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert "readonly SHA_PATTERN='^[0-9a-f]{40}$'" in script
    assert 'tag="sha-${sha:0:7}"' in script
    assert "/root/dockers/content-factory" in script
    assert "git fetch" in script
    assert "git reset --hard" in script
    assert "docker compose pull" in script
    assert "docker compose up -d --remove-orphans" in script
    assert "HEALTHCHECK_ATTEMPTS=30" in script
    assert "HEALTHCHECK_INTERVAL=2" in script
    assert "http://127.0.0.1:8000/api/v1/stock" in script
    assert 'export IMAGE_TAG="$previous_tag"' in script
    assert "prefect deploy --all" in script
    assert "EUID != 0" in script
    assert "exit 2" in script
    assert "set -x" not in script


def test_deploy_script_checks_worker() -> None:
    script = _deploy_script()

    assert "docker compose ps -q worker" in script
    assert "docker inspect" in script
    assert ".State.Status" in script
    assert ".State.RestartCount" in script
    assert ".State.ExitCode" in script
    assert "WORKER_STARTUP_SECONDS=60" in script
    assert "WORKER_STABILITY_SECONDS=15" in script
    assert "docker compose logs" in script
    assert "--tail" in script
    assert "ps -q init" not in script


def test_deploy_script_checks_worker_before_state_write() -> None:
    script = _deploy_script()

    up = script.index("if ! docker compose up -d --remove-orphans; then")
    worker_check = script.index("if ! workercheck; then")
    state_write = script.index('printf \'%s\\n\' "$tag" >"$STATE_FILE"')

    assert up < worker_check < state_write


def test_deploy_script_rolls_back_when_worker_fails() -> None:
    script = _deploy_script()

    worker_check = script.index("if ! workercheck; then")
    state_write = script.index('printf \'%s\\n\' "$tag" >"$STATE_FILE"')
    failure_block = script[worker_check:state_write]

    assert "if rollback; then" in failure_block
    assert 'fail "проверка worker' in failure_block


def test_deploy_runbook_contract() -> None:
    runbook = (_ROOT / _DEPLOY_README).read_text(encoding="utf-8")

    assert "ghcr.io/slaid098/content-factory" in runbook
    assert "/root/dockers/content-factory" in runbook
    assert "/usr/local/sbin/content-factory-deploy" in runbook
    assert "docker login ghcr.io" in runbook
    assert "IMAGE_TAG" in runbook
    for name in _SECRETS:
        assert name in runbook


def test_deploy_runbook_documents_worker_contract() -> None:
    runbook = (_ROOT / _DEPLOY_README).read_text(encoding="utf-8")

    assert "docker compose ps -q worker" in runbook
    assert "State.Status" in runbook
    assert "RestartCount" in runbook
    assert "ExitCode" in runbook
    assert "60 с" in runbook
    assert "15 с" in runbook
    assert "one-shot" in runbook


def test_prefect_deployment_contract() -> None:
    deployment = _load("prefect.yaml")["deployments"][0]

    assert deployment["name"] == "stock_topup"
    assert deployment["entrypoint"] == "src/content_factory/flows/stock_topup/flow.py:stock_topup"
    assert deployment["work_pool"]["name"] == "content-factory"
    assert deployment["concurrency_limit"] == 1
    assert deployment["schedules"] == [
        {
            "slug": "every-15-minutes",
            "cron": "*/15 * * * *",
            "timezone": "UTC",
            "active": True,
        }
    ]

    module = importlib.import_module("content_factory.flows.stock_topup.flow")
    assert callable(module.stock_topup)
