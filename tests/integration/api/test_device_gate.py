"""Trusted-device gate and one-time join route over HTTP."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.errors import DatabaseError
from content_factory.services.device_trust import issue_link, revoke_device

_DEVICE_TTL_DAYS = 180
_DEVICE_TTL_SECONDS = _DEVICE_TTL_DAYS * 24 * 3600


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build the app with the gate enabled against a fresh data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_GATE_ENABLED", "true")
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_COOKIE_SECURE", "false")
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    return TestClient(create_app())


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return the operational database path of the test app."""
    return tmp_path / DB_DIR / DB_FILENAME


def test_api_without_cookie_returns_json_403(client: TestClient) -> None:
    response = client.get("/api/v1/stock")

    assert response.status_code == 403
    assert response.json() == {"detail": "device not trusted"}


def test_version_endpoint_is_open_without_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_SHA", "a" * 40)
    monkeypatch.setenv("BUILT_AT", "2026-01-01T00:00:00Z")

    response = client.get("/api/version")

    assert response.status_code == 200
    assert response.json()["version"] == "a" * 40


def test_page_without_cookie_is_a_self_contained_html_403(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert 'lang="ru"' in response.text
    assert 'name="referrer" content="no-referrer"' in response.text
    assert "http://" not in response.text
    assert "https://" not in response.text


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost"])
def test_local_healthcheck_answers_status_without_stock(
    client: TestClient,
    host: str,
) -> None:
    response = client.get("/api/v1/stock", headers={"Host": host})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "testserver"},
        {"Host": "127.0.0.1:8000", "X-Forwarded-For": "203.0.113.10"},
        {"Host": "127.0.0.1:8000", "X-Real-IP": "203.0.113.10"},
    ],
)
def test_healthcheck_with_foreign_host_or_proxy_headers_is_denied(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    response = client.get("/api/v1/stock", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "device not trusted"}


def test_local_healthcheck_with_cookie_returns_the_real_stock(
    client: TestClient,
    db_path: Path,
) -> None:
    token = _device_cookie(client, db_path)

    response = client.get(
        "/api/v1/stock",
        headers={"Host": "127.0.0.1:8000"},
        cookies={"device_token": token},
    )

    assert response.status_code == 200
    assert "languages" in response.json()


def test_local_healthcheck_with_invalid_cookie_is_denied(client: TestClient) -> None:
    response = client.get(
        "/api/v1/stock",
        headers={"Host": "127.0.0.1:8000"},
        cookies={"device_token": "garbage"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "device not trusted"}


def test_bare_and_unknown_join_links_return_the_same_410(
    client: TestClient,
) -> None:
    bare = client.get("/join")
    unknown = client.get("/join/unknown-token")

    assert bare.status_code == 410
    assert unknown.status_code == 410
    assert bare.text == unknown.text
    assert bare.headers["cache-control"] == "no-store"


def test_join_sets_host_only_cookie_and_grants_access(
    client: TestClient,
    db_path: Path,
) -> None:
    issued = issue_link(db_path, "phone", 24, _DEVICE_TTL_DAYS)

    response = client.get(f"/join/{issued.token}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("device_token=")
    assert "Path=/" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    assert "Secure" not in cookie
    max_age = int(re.search(r"Max-Age=(\d+)", cookie).group(1))
    assert 0 < max_age <= _DEVICE_TTL_SECONDS

    token = response.cookies["device_token"]
    assert client.get("/docs", cookies={"device_token": token}).status_code == 200
    stock = client.get("/api/v1/stock", cookies={"device_token": token})
    assert stock.status_code == 200
    assert "languages" in stock.json()


def test_join_cookie_is_secure_when_enabled(
    client: TestClient,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_COOKIE_SECURE", "true")
    issued = issue_link(db_path, "phone", 24, _DEVICE_TTL_DAYS)

    response = client.get(f"/join/{issued.token}", follow_redirects=False)

    assert "Secure" in response.headers["set-cookie"]


def test_link_is_one_time_and_revoke_denies_the_next_request(
    client: TestClient,
    db_path: Path,
) -> None:
    issued = issue_link(db_path, "phone", 24, _DEVICE_TTL_DAYS)
    first = client.get(f"/join/{issued.token}", follow_redirects=False)
    token = first.cookies["device_token"]

    second = client.get(f"/join/{issued.token}", follow_redirects=False)
    assert second.status_code == 410
    assert revoke_device(db_path, issued.device_id)

    denied = client.get("/docs", cookies={"device_token": token})
    assert denied.status_code == 403
    assert client.get("/api/v1/stock", cookies={"device_token": token}).status_code == 403


def test_garbage_or_foreign_cookie_is_denied(client: TestClient) -> None:
    assert client.get("/docs", cookies={"device_token": "garbage"}).status_code == 403
    assert client.get("/docs", cookies={"other": "value"}).status_code == 403


def _device_cookie(client: TestClient, db_path: Path) -> str:
    """Issue a link, redeem it through the route and return the cookie value."""
    issued = issue_link(db_path, "phone", 24, _DEVICE_TTL_DAYS)
    response = client.get(f"/join/{issued.token}", follow_redirects=False)
    assert response.status_code == 303
    return response.cookies["device_token"]


def test_content_api_without_cookie_or_bearer_is_401(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")

    response = client.get("/api/v1/content/archives")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_content_api_with_trusted_cookie_needs_no_bearer(
    client: TestClient,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")
    token = _device_cookie(client, db_path)

    response = client.get(
        "/api/v1/content/archives",
        cookies={"device_token": token},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_content_api_with_garbage_cookie_is_403(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")

    response = client.get(
        "/api/v1/content/archives",
        cookies={"device_token": "garbage"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "device not trusted"}


def test_content_api_with_revoked_cookie_is_403(
    client: TestClient,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")
    issued = issue_link(db_path, "phone", 24, _DEVICE_TTL_DAYS)
    joined = client.get(f"/join/{issued.token}", follow_redirects=False)
    token = joined.cookies["device_token"]
    assert revoke_device(db_path, issued.device_id)

    response = client.get(
        "/api/v1/content/archives",
        cookies={"device_token": token},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "device not trusted"}


def test_content_api_cookie_database_failure_returns_503(
    client: TestClient,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")
    token = _device_cookie(client, db_path)

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise DatabaseError("database is down")

    monkeypatch.setattr("content_factory.api.device_gate.authenticate", _fail)

    response = client.get(
        "/api/v1/content/archives",
        cookies={"device_token": token},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "device check unavailable"}


def test_content_api_with_bearer_needs_no_cookie(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")

    response = client.get(
        "/api/v1/content/archives",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_database_failure_returns_503(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        raise DatabaseError("database is down")

    monkeypatch.setattr("content_factory.api.device_gate.authenticate", _fail)

    api = client.get("/api/v1/stock", cookies={"device_token": "any"})
    page = client.get("/docs", cookies={"device_token": "any"})

    assert api.status_code == 503
    assert api.json() == {"detail": "device check unavailable"}
    assert page.status_code == 503
    assert page.headers["content-type"].startswith("text/html")


def test_healthcheck_database_failure_returns_503(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        raise DatabaseError("database is down")

    monkeypatch.setattr("content_factory.api.device_gate.database_ready", _fail)

    response = client.get("/api/v1/stock", headers={"Host": "127.0.0.1:8000"})

    assert response.status_code == 503
    assert response.json() == {"detail": "device check unavailable"}


def test_join_database_failure_returns_503(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        raise DatabaseError("database is down")

    monkeypatch.setattr("content_factory.api.device_join.redeem_link", _fail)

    response = client.get("/join/any-token", follow_redirects=False)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
