"""Guard the destructive PostgreSQL fixtures against a non-test database."""

from __future__ import annotations

from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

TEST_DSN_ENV = "CONTENT_FACTORY_TEST_DATABASE_URL"

_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def database_name(url: str) -> str:
    """Return the effective database name of a PostgreSQL URI, if valid."""
    try:
        values = conninfo_to_dict(url)
    except (psycopg.Error, ValueError):
        return ""
    return str(values.get("dbname", ""))


def require_test_database(url: str) -> None:
    """Fail instead of running destructive SQL against a non-test database.

    The effective libpq ``dbname``, ``host`` and ``hostaddr`` are read from
    the whole URI, so a query parameter or fragment cannot hide a production
    target from the guard. A missing host is rejected: libpq would otherwise
    take ``PGHOST`` or ``PGSERVICE`` from the environment.
    """
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in _POSTGRES_SCHEMES:
        pytest.fail(f"{TEST_DSN_ENV} должен быть URI postgresql://…, а не keyword-DSN")
    try:
        values = conninfo_to_dict(url)
    except (psycopg.Error, ValueError):
        pytest.fail(f"{TEST_DSN_ENV} не удалось разобрать как conninfo")
    name = str(values.get("dbname", ""))
    if not name.endswith("_test"):
        pytest.fail(
            f"{TEST_DSN_ENV} должен указывать на базу с именем, оканчивающимся "
            f"на _test; сейчас: {name or 'имя не найдено'}"
        )
    if str(values.get("service", "")):
        pytest.fail(f"{TEST_DSN_ENV} не должен использовать service: цель не видна в DSN")
    hosts = [
        host.strip().lower()
        for key in ("host", "hostaddr")
        for host in str(values.get(key, "")).split(",")
        if host.strip()
    ]
    if not hosts:
        pytest.fail(
            f"{TEST_DSN_ENV} должен явно указывать host или hostaddr: "
            "иначе libpq возьмёт хост из PGHOST или PGSERVICE"
        )
    if any(host not in _LOCAL_HOSTS for host in hosts):
        pytest.fail(
            f"{TEST_DSN_ENV}: host и hostaddr должны указывать только "
            "на localhost, 127.0.0.1 или ::1"
        )


__all__ = ["TEST_DSN_ENV", "database_name", "require_test_database"]
