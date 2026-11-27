"""Fixtures for the PostgreSQL suite.

The suite is skipped unless ``CONTENT_FACTORY_TEST_DATABASE_URL`` points to a
reachable empty database. Each test starts from empty operational tables; the
schema itself is applied by the first ``connect()`` of the process.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.integration.db.postgres_guard import (
    TEST_DSN_ENV,
    clear_libpq_environment,
    require_test_database,
)

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect

# Parent tables first: downloads references videos.
ALL_TABLES = (
    "stock_items",
    "titles",
    "profile_settings",
    "runtime_state",
    "alert_cooldowns",
    "videos",
    "downloads",
    "source_items",
    "source_cursors",
    "trusted_devices",
    "generator_runs",
    "generator_servers",
    "generator_items",
)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Return the test DSN or skip the PostgreSQL suite."""
    url = os.environ.get(TEST_DSN_ENV)
    if not url:
        pytest.skip(f"{TEST_DSN_ENV} не задан: тесты PostgreSQL пропущены")
    clear_libpq_environment()
    require_test_database(url)
    return url


@pytest.fixture
def pg(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Path]:
    """Point the service at PostgreSQL and empty its operational tables."""
    clear_libpq_environment()
    require_test_database(postgres_url)
    monkeypatch.setenv("CONTENT_FACTORY_DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    db_path = tmp_path / DB_DIR / DB_FILENAME
    connection = connect(db_path)
    try:
        for table in reversed(ALL_TABLES):
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    finally:
        connection.close()
    yield db_path


__all__ = ["ALL_TABLES", "TEST_DSN_ENV"]
