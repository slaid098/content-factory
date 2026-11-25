"""Guard the destructive PostgreSQL fixtures against a non-test database."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

TEST_DSN_ENV = "CONTENT_FACTORY_TEST_DATABASE_URL"


def database_name(url: str) -> str:
    """Return the database name of a PostgreSQL DSN or an empty string."""
    try:
        return urlsplit(url).path.lstrip("/")
    except ValueError:
        return ""


def require_test_database(url: str) -> None:
    """Fail instead of deleting data from a database not named ``*_test``."""
    name = database_name(url)
    if not name.endswith("_test"):
        pytest.fail(
            f"{TEST_DSN_ENV} должен указывать на базу с именем, оканчивающимся "
            f"на _test; сейчас: {name or 'имя не найдено'}"
        )


__all__ = ["TEST_DSN_ENV", "database_name", "require_test_database"]
