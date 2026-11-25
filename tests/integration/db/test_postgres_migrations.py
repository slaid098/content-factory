"""Versioned migrations on PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.db.postgres_guard import require_test_database

from content_factory.db import connection as connection_module
from content_factory.db.connection import connect
from content_factory.db.migrations.runner import applied_versions

_TABLES = (
    "downloads",
    "videos",
    "alert_cooldowns",
    "runtime_state",
    "profile_settings",
    "titles",
    "stock_items",
)


def test_migrations_apply_from_scratch_and_again(
    pg: Path,
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_test_database(postgres_url)
    connection = connect(pg)
    try:
        for table in (*_TABLES, "schema_migrations"):
            connection.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        connection.commit()
    finally:
        connection.close()

    connection_module._ready.clear()
    first = connect(pg)
    try:
        assert applied_versions(first) == ("0001",)
    finally:
        first.close()

    connection_module._ready.clear()
    second = connect(pg)
    try:
        assert applied_versions(second) == ("0001",)
    finally:
        second.close()


def test_adapter_reports_columns_and_tables(pg: Path) -> None:
    connection = connect(pg)
    try:
        assert "content_type" in connection.columns("stock_items")
        assert "language" in connection.columns("videos")
        assert connection.has_table("downloads") is True
        assert connection.has_table("missing_table") is False
    finally:
        connection.close()


def test_rows_support_column_names_and_positions(pg: Path) -> None:
    connection = connect(pg)
    try:
        connection.execute(
            "INSERT INTO titles (sha256, title, norm, created_at) VALUES (?, ?, ?, ?)",
            ("a" * 64, "Название", "название", "2026-05-01"),
        )
        connection.commit()
        row = connection.execute("SELECT sha256, title FROM titles").fetchone()
        assert row is not None
        assert row["title"] == "Название"
        assert row[0] == "a" * 64
    finally:
        connection.close()


def test_guard_rejects_a_database_without_the_test_suffix() -> None:
    with pytest.raises(pytest.fail.Exception, match="_test"):
        require_test_database("postgresql://user@localhost/content_factory")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u@localhost/content_factory_test?dbname=content_factory",
        "postgresql://u@localhost/content_factory_test?host=prod.example",
        "postgresql://u@prod.example/content_factory_test",
        "host=localhost dbname=content_factory_test",
        "postgresql://u@localhost/content_factory_test?service=production",
    ],
)
def test_guard_rejects_retargeting_and_keyword_dsn(url: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="CONTENT_FACTORY_TEST_DATABASE_URL"):
        require_test_database(url)


def test_guard_accepts_the_ci_dsn() -> None:
    require_test_database(
        "postgresql://content_factory:content_factory@localhost:5432/content_factory_test"
    )
    require_test_database("postgresql:///content_factory_test")
