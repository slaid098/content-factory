"""Versioned migrations on PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.device_trust_columns import TRUSTED_DEVICE_COLUMNS
from tests.integration.db.postgres_guard import (
    clear_libpq_environment,
    require_test_database,
)

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
    "source_items",
    "source_cursors",
    "trusted_devices",
    "generator_servers",
)


def test_migrations_apply_from_scratch_and_again(
    pg: Path,
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")
    clear_libpq_environment()
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
        assert applied_versions(first) == ("0001", "0002", "0003", "0004", "0005")
    finally:
        first.close()

    connection_module._ready.clear()
    second = connect(pg)
    try:
        assert applied_versions(second) == ("0001", "0002", "0003", "0004", "0005")
    finally:
        second.close()


def test_adapter_reports_columns_and_tables(pg: Path) -> None:
    connection = connect(pg)
    try:
        assert "content_type" in connection.columns("stock_items")
        assert "language" in connection.columns("videos")
        assert connection.has_table("downloads") is True
        assert connection.has_table("missing_table") is False
        assert connection.columns("trusted_devices") == TRUSTED_DEVICE_COLUMNS
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
        "postgresql://u@localhost/content_factory_test?hostaddr=10.0.0.1",
        "postgresql://u@prod.example/content_factory_test",
        "postgresql://u@localhost/content_factory_test#?dbname=content_factory",
        "host=localhost dbname=content_factory_test",
        "postgresql://u@localhost/content_factory_test?service=production",
        "postgresql:///content_factory_test",
        "postgresql://u@/content_factory_test",
        "postgresql://:5432/content_factory_test",
    ],
)
def test_guard_rejects_retargeting_hostless_and_keyword_dsn(url: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="CONTENT_FACTORY_TEST_DATABASE_URL"):
        require_test_database(url)


def test_guard_rejects_a_hostless_dsn_even_with_pghost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGHOST", "prod.example")

    with pytest.raises(pytest.fail.Exception, match="host"):
        require_test_database("postgresql:///content_factory_test")


def test_guard_accepts_the_ci_and_local_dsns() -> None:
    clear_libpq_environment()
    require_test_database(
        "postgresql://content_factory:content_factory@localhost:5432/content_factory_test"
    )
    require_test_database("postgresql://postgres@127.0.0.1:55432/content_factory_test")
    require_test_database("postgresql://postgres@[::1]:55432/content_factory_test")


@pytest.mark.parametrize("query", ["host=localhost,,", "hostaddr=127.0.0.1,,"])
def test_guard_rejects_empty_host_components(query: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="пустые компоненты"):
        require_test_database(f"postgresql://u@localhost/content_factory_test?{query}")


def test_guard_rejects_a_non_local_pghostaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.2")

    with pytest.raises(pytest.fail.Exception, match="PGHOSTADDR"):
        require_test_database("postgresql://u@localhost/content_factory_test")


@pytest.mark.parametrize("name", ["PGSERVICE", "PGSERVICEFILE"])
def test_guard_rejects_service_environment(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, "production")

    with pytest.raises(pytest.fail.Exception, match=name):
        require_test_database("postgresql://u@localhost/content_factory_test")


def test_guard_rejects_a_pgport_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGPORT", "5433")

    with pytest.raises(pytest.fail.Exception, match="PGPORT"):
        require_test_database("postgresql://u@localhost/content_factory_test")


def test_clear_environment_allows_the_ci_dsn_after_pgport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGPORT", "5433")

    clear_libpq_environment()

    assert "PGPORT" not in os.environ
    require_test_database(
        "postgresql://content_factory:content_factory@localhost:5432/content_factory_test"
    )


def test_guard_rejects_an_empty_service_parameter() -> None:
    with pytest.raises(pytest.fail.Exception, match="service"):
        require_test_database("postgresql://u@localhost/content_factory_test?service=")


def test_clear_environment_drops_libpq_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "PGHOST",
        "PGHOSTADDR",
        "PGPORT",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGDATABASE",
    ):
        monkeypatch.setenv(name, "prod.example")

    clear_libpq_environment()

    for name in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGDATABASE"):
        assert name not in os.environ
