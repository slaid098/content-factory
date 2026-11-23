from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.constants import CONTENT_DIR, QUERIES_DIR
from content_factory.exceptions import DeliveryError
from delivery_helpers import add_downloaded_video, touch_mp4

_TOKEN = "delivery-secret"
_HEADER = "# Добавлено из интерфейса"


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the app at a temporary data directory and configure a token."""
    monkeypatch.delenv("CONTENT_FACTORY_DATA_DIR", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN", raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", _TOKEN)
    return tmp_path


def _client() -> TestClient:
    return TestClient(create_app())


def _headers(token: str | None = _TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


def _get(
    client: TestClient,
    path: str,
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.get(path, headers=_headers(token))


def _post(
    client: TestClient,
    body: dict[str, object],
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.post("/api/v1/content/queries", json=body, headers=_headers(token))


def _write_queries(data_dir: Path, text: str) -> Path:
    path = data_dir / QUERIES_DIR / "ru.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _add_stock(data_dir: Path, sha256: str, query: str) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(data_dir / "db" / "db.sqlite3", sha256, "Описание", query=query)


def test_queries_without_authorization_returns_401(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/queries?language=ru", token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_create_query_without_authorization_returns_401(data_dir: Path) -> None:
    response = _post(
        _client(),
        {"language": "ru", "query": "новый"},
        token=None,
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_queries_without_configured_token_returns_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _get(_client(), "/api/v1/content/queries?language=ru")

    assert response.status_code == 503
    assert _TOKEN not in response.text


def test_create_query_without_configured_token_returns_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _post(_client(), {"language": "ru", "query": "новый"})

    assert response.status_code == 503
    assert _TOKEN not in response.text


def test_queries_returns_file_order_counts_and_database_queries(data_dir: Path) -> None:
    _write_queries(data_dir, "# Список\nпервый\nвторой\n")
    _add_stock(data_dir, "a" * 64, "первый")
    _add_stock(data_dir, "b" * 64, "первый")
    _add_stock(data_dir, "c" * 64, "старый")
    _add_stock(data_dir, "d" * 64, "")
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("e" * 64 + ".mp4"))

    response = _get(_client(), "/api/v1/content/queries?language=ru")

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "queries": [
            {"query": "первый", "count": 2, "in_file": True},
            {"query": "второй", "count": 0, "in_file": True},
            {"query": "старый", "count": 1, "in_file": False},
        ],
        "unclassified": 2,
        "total": 5,
    }


def test_queries_without_file_returns_database_queries(data_dir: Path) -> None:
    _add_stock(data_dir, "a" * 64, "старый запрос")

    response = _get(_client(), "/api/v1/content/queries?language=ru")

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "queries": [{"query": "старый запрос", "count": 1, "in_file": False}],
        "unclassified": 0,
        "total": 1,
    }


def test_queries_without_database_returns_zero_counts(data_dir: Path) -> None:
    _write_queries(data_dir, "первый\nвторой\n")
    db_path = data_dir / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    response = _get(_client(), "/api/v1/content/queries?language=ru")

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "queries": [
            {"query": "первый", "count": 0, "in_file": True},
            {"query": "второй", "count": 0, "in_file": True},
        ],
        "unclassified": 0,
        "total": 0,
    }


def test_queries_allows_configured_disabled_language(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/queries?language=en")

    assert response.status_code == 200
    assert response.json() == {"language": "en", "queries": [], "unclassified": 0, "total": 0}


@pytest.mark.parametrize("language", ["de", "eng"])
def test_queries_unknown_configured_language_returns_422(
    data_dir: Path,
    language: str,
) -> None:
    response = _get(_client(), f"/api/v1/content/queries?language={language}")

    assert response.status_code == 422


@pytest.mark.parametrize("language", ["RU", "r", "ru-RU", "ru/", "ru%2F.."])
def test_queries_invalid_language_returns_422(data_dir: Path, language: str) -> None:
    response = _get(
        _client(),
        f"/api/v1/content/queries?language={language}",
    )

    assert response.status_code == 422


def test_create_query_adds_one_normalized_line(data_dir: Path) -> None:
    response = _post(
        _client(),
        {"language": "ru", "query": "  новый   запрос "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "query": "новый запрос",
        "created": True,
    }
    path = data_dir / QUERIES_DIR / "ru.txt"
    assert path.read_text(encoding="utf-8") == f"{_HEADER}\nновый запрос\n"


def test_create_query_preserves_comments_and_appends_to_section(data_dir: Path) -> None:
    path = _write_queries(
        data_dir,
        f"# Список\nкурятник\n\n{_HEADER}\nстарый\n",
    )

    response = _post(_client(), {"language": "ru", "query": "новый"})

    assert response.status_code == 200
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# Список",
        "курятник",
        "",
        _HEADER,
        "старый",
        "новый",
    ]


def test_create_query_duplicate_returns_created_false(data_dir: Path) -> None:
    path = _write_queries(data_dir, "Курятник\n")
    original = path.read_text(encoding="utf-8")

    response = _post(_client(), {"language": "ru", "query": "  курятник "})

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "query": "курятник",
        "created": False,
    }
    assert path.read_text(encoding="utf-8") == original


def test_create_query_accepts_200_characters(data_dir: Path) -> None:
    query = "a" * 200

    response = _post(_client(), {"language": "ru", "query": query})

    assert response.status_code == 200
    assert response.json()["query"] == query
    assert response.json()["created"] is True


@pytest.mark.parametrize(
    "query",
    ["", "   ", "a" * 201, "a\x00b", "a\x07b", "a\nb", "a\rb", "#запрос", "  # запрос "],
)
def test_create_query_rejects_invalid_values(data_dir: Path, query: str) -> None:
    response = _post(_client(), {"language": "ru", "query": query})

    assert response.status_code == 422
    assert not (data_dir / QUERIES_DIR / "ru.txt").exists()


def test_create_query_unknown_configured_language_returns_422(data_dir: Path) -> None:
    response = _post(_client(), {"language": "de", "query": "новый"})

    assert response.status_code == 422
    assert not (data_dir / QUERIES_DIR / "de.txt").exists()


@pytest.mark.parametrize("language", ["RU", "r", "ru-RU", "ru/", ""])
def test_create_query_invalid_language_returns_422(data_dir: Path, language: str) -> None:
    response = _post(_client(), {"language": language, "query": "новый"})

    assert response.status_code == 422


def test_create_query_rejects_unknown_fields(data_dir: Path) -> None:
    response = _post(
        _client(),
        {"language": "ru", "query": "новый", "extra": True},
    )

    assert response.status_code == 422


def test_create_query_returns_500_on_write_error(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise DeliveryError("boom")

    monkeypatch.setattr(
        "content_factory.api.v1.routes.queries.add_language_query",
        fail,
    )

    response = _post(_client(), {"language": "ru", "query": "новый"})

    assert response.status_code == 500


def test_queries_returns_500_on_read_error(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise DeliveryError("boom")

    monkeypatch.setattr(
        "content_factory.api.v1.routes.queries.list_language_queries",
        fail,
    )

    response = _get(_client(), "/api/v1/content/queries?language=ru")

    assert response.status_code == 500
