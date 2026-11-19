from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((_ROOT / name).read_text(encoding="utf-8"))


def test_compose_contract() -> None:
    services = _load("compose.yaml")["services"]

    api = services["api"]
    worker = services["worker"]

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
