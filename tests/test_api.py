from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.db.connection import connect
from content_factory.db.runtime_state import save_state
from content_factory.schemas.state.state import RuntimeState
from content_factory.services.stock import service as stock_service
from delivery_helpers import add_title

_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")


def _client() -> TestClient:
    return TestClient(create_app())


def test_stock_returns_enabled_languages_and_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    _touch(tmp_path / "content" / "ru" / "b.mp4")
    _touch(tmp_path / "content" / "en" / "ignored.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Название первого видео")
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(
            connection,
            replace(
                RuntimeState.empty(),
                last_production_at=_MOMENT - timedelta(hours=3),
                captcha_streak=2,
                zero_runs=1,
            ),
        )
    finally:
        connection.close()

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body["generated_at"]).utcoffset() is not None
    assert datetime.fromisoformat(body["last_production_at"]) == _MOMENT - timedelta(hours=3)
    assert body["captcha_streak"] == 2
    assert body["zero_runs"] == 1
    assert body["captcha_blocked_until"] is None
    assert body["languages"] == [
        {
            "language": "ru",
            "stock": 2,
            "target": 150,
            "deficit": 148,
            "titled": 1,
            "untitled": 1,
            "video_bytes": 10,
        },
    ]
    assert all(row["titled"] + row["untitled"] == row["stock"] for row in body["languages"])


def test_stock_reports_titled_untitled_and_video_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    for name, data in (("a.mp4", b"12345"), ("b.mp4", b"1234567"), ("c.mp4", b"123")):
        path = tmp_path / "content" / "ru" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Первое название")
    add_title(tmp_path / "db" / "db.sqlite3", "b", "Второе название")

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    row = response.json()["languages"][0]
    assert row["stock"] == 3
    assert row["titled"] == 2
    assert row["untitled"] == 1
    assert row["video_bytes"] == 15
    assert row["deficit"] == 147
    assert row["titled"] + row["untitled"] == row["stock"]


def test_stock_does_not_count_whitespace_only_titles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "   ", norm="whitespace")

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    row = response.json()["languages"][0]
    assert row["stock"] == 1
    assert row["titled"] == 0
    assert row["untitled"] == 1


def test_stock_survives_malformed_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(connection, RuntimeState.empty())
        connection.execute(
            "UPDATE runtime_state SET updated_at = ? WHERE id = 1",
            ("not-a-timestamp",),
        )
        connection.commit()
    finally:
        connection.close()

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    row = response.json()["languages"][0]
    assert row["stock"] == 1
    assert row["titled"] == 0
    assert row["untitled"] == 1
    assert row["video_bytes"] == 5


def test_stock_survives_unreadable_titles_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    _touch(tmp_path / "content" / "ru" / "a.mp4")

    def _fail(*_args: object) -> dict[str, str]:
        raise PermissionError("permission denied")

    monkeypatch.setattr(stock_service, "load_titles", _fail)

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    row = response.json()["languages"][0]
    assert row["stock"] == 1
    assert row["titled"] == 0
    assert row["untitled"] == 1
    assert not (tmp_path / "db").exists()


def test_stock_without_data_directory_is_empty_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(data_dir))

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    body = response.json()
    assert body["languages"] == [
        {
            "language": "ru",
            "stock": 0,
            "target": 150,
            "deficit": 150,
            "titled": 0,
            "untitled": 0,
            "video_bytes": 0,
        },
    ]
    assert body["last_production_at"] is None
    assert body["captcha_streak"] == 0
    assert not data_dir.exists()


def test_stock_reads_targets_from_existing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """
languages:
  ru:
    enabled: true
    stock_target: 4
  en:
    enabled: true
    stock_target: 3
""",
        encoding="utf-8",
    )
    _touch(tmp_path / "content" / "ru" / "a.mp4")

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    assert response.json()["languages"] == [
        {
            "language": "en",
            "stock": 0,
            "target": 3,
            "deficit": 3,
            "titled": 0,
            "untitled": 0,
            "video_bytes": 0,
        },
        {
            "language": "ru",
            "stock": 1,
            "target": 4,
            "deficit": 3,
            "titled": 0,
            "untitled": 1,
            "video_bytes": 5,
        },
    ]


def test_stock_does_not_create_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    config = tmp_path / "config" / "config.yaml"

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    assert not config.exists()


def test_stock_reports_state_without_content_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(
            connection,
            replace(
                RuntimeState.empty(),
                captcha_blocked_until=_MOMENT + timedelta(hours=1),
                captcha_streak=3,
            ),
        )
    finally:
        connection.close()

    response = _client().get("/api/v1/stock")

    assert response.status_code == 200
    body = response.json()
    assert body["captcha_streak"] == 3
    assert datetime.fromisoformat(body["captcha_blocked_until"]) == _MOMENT + timedelta(hours=1)
    assert body["languages"][0]["stock"] == 0
