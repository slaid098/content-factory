"""Versioned migrations on PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_migrations_apply_from_scratch_and_again(pg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
