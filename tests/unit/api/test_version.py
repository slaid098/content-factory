"""Release identity endpoint and its build-time environment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.config.settings import Settings

_SHA = "b185893" + "0" * 33


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Build the app against an empty data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def test_version_returns_sha_and_build_time(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", _SHA)
    monkeypatch.setenv("BUILT_AT", "2026-01-01T00:00:00Z")

    response = client.get("/api/version")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == _SHA
    assert datetime.fromisoformat(body["built_at"]) == datetime(2026, 1, 1, tzinfo=UTC)


def test_version_defaults_to_unknown_without_build_env(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILT_AT", raising=False)

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json() == {"version": "unknown", "built_at": None}


def test_version_accepts_the_build_arg_from_the_issue(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", "test")
    monkeypatch.setenv("BUILT_AT", "2026-01-01T00:00:00Z")

    response = client.get("/api/version")

    assert response.json()["version"] == "test"


@pytest.mark.parametrize("built_at", ["unknown", "", "not-a-date"])
def test_version_maps_missing_or_invalid_build_time_to_null(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    built_at: str,
) -> None:
    monkeypatch.setenv("GIT_SHA", _SHA)
    monkeypatch.setenv("BUILT_AT", built_at)

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json()["built_at"] is None


def test_version_adds_utc_to_a_naive_build_time(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", _SHA)
    monkeypatch.setenv("BUILT_AT", "2026-01-01T00:00:00")

    response = client.get("/api/version")

    moment = datetime.fromisoformat(response.json()["built_at"])
    assert moment.utcoffset() == timedelta(0)


def test_blank_git_sha_setting_falls_back_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", "   ")

    assert Settings().git_sha == "unknown"
