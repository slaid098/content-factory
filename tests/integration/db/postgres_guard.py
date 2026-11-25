"""Guard the destructive PostgreSQL fixtures against a non-test database."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

TEST_DSN_ENV = "CONTENT_FACTORY_TEST_DATABASE_URL"

_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_LIBPQ_OVERRIDES = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGDATABASE",
)


def clear_libpq_environment() -> None:
    """Drop libpq variables that could fill or override the test DSN slots."""
    for name in _LIBPQ_OVERRIDES:
        os.environ.pop(name, None)


def database_name(url: str) -> str:
    """Return the effective database name of a PostgreSQL URI, if valid."""
    try:
        values = conninfo_to_dict(url)
    except (psycopg.Error, ValueError):
        return ""
    return str(values.get("dbname", ""))


def _check_host_list(key: str, value: str) -> None:
    """Fail unless every component of a libpq host list is an explicit local host."""
    hosts = value.split(",")
    if any(not host.strip() for host in hosts):
        pytest.fail(f"{TEST_DSN_ENV}: {key} не должен содержать пустые компоненты")
    if any(host.strip().lower() not in _LOCAL_HOSTS for host in hosts):
        pytest.fail(
            f"{TEST_DSN_ENV}: {key} должен указывать только на localhost, 127.0.0.1 или ::1"
        )


def require_test_database(url: str) -> None:
    """Fail instead of running destructive SQL against a non-test database.

    The effective libpq ``dbname``, ``host`` and ``hostaddr`` are read from
    the whole URI, so a query parameter or fragment cannot hide a production
    target from the guard. An explicit local host is required: libpq would
    otherwise take ``PGHOST``, ``PGHOSTADDR``, ``PGPORT`` or ``PGSERVICE``
    from the environment. Empty host-list components, service settings and a
    port from ``PGPORT`` are rejected.
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
    if "service" in values:
        pytest.fail(f"{TEST_DSN_ENV} не должен использовать service: цель не видна в DSN")
    targets = {
        key: value for key in ("host", "hostaddr") if (value := str(values.get(key, "")).strip())
    }
    if not targets:
        pytest.fail(
            f"{TEST_DSN_ENV} должен явно указывать host или hostaddr: "
            "иначе libpq возьмёт хост из PGHOST или PGSERVICE"
        )
    for key, value in targets.items():
        _check_host_list(key, value)
    for name in ("PGSERVICE", "PGSERVICEFILE"):
        if os.environ.get(name):
            pytest.fail(f"{TEST_DSN_ENV}: {name} не должен задавать цель тестовой базы")
    if os.environ.get("PGPORT", "").strip():
        pytest.fail(f"{TEST_DSN_ENV}: PGPORT не должен задавать порт тестовой базы")
    if env_hostaddr := os.environ.get("PGHOSTADDR", "").strip():
        _check_host_list("PGHOSTADDR", env_hostaddr)


__all__ = [
    "TEST_DSN_ENV",
    "clear_libpq_environment",
    "database_name",
    "require_test_database",
]
