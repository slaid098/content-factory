"""Validation and redaction of the PostgreSQL connection string.

``CONTENT_FACTORY_DATABASE_URL`` may carry a password, so it is treated as a
secret: settings validate and normalize it here, while the connection adapter
and the command line tools redact it before writing an error or a log line.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from content_factory.exceptions.errors import ConfigError

_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
_SECOND_SCHEME = re.compile(r"(?i)^[a-z][a-z0-9+.\-]*://")
_CREDENTIALS = re.compile(r"(?i)(?P<scheme>[a-z][a-z0-9+.\-]*://)[^/\s]+@")
_PASSWORD_FIELD = re.compile(r"(?i)\bpassword\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s;]+)")
_QUERY_TARGET_PARAMETERS = frozenset({"dbname", "host", "hostaddr", "port", "service"})
_MASK = "***"


def normalize_database_url(value: str) -> str:
    """Strip a PostgreSQL DSN and validate its scheme, host and database.

    Args:
        value: Raw value of ``CONTENT_FACTORY_DATABASE_URL``.

    Returns:
        The DSN without surrounding spaces and with a lower-case scheme.

    Raises:
        ConfigError: The value is not a PostgreSQL URL with a host and a
            database name, contains whitespace or repeats the scheme prefix.
    """
    text = value.strip()
    try:
        parts = urlsplit(text)
    except ValueError as ex:
        raise ConfigError("CONTENT_FACTORY_DATABASE_URL имеет неверный формат") from ex
    if any(char.isspace() for char in text):
        raise ConfigError("CONTENT_FACTORY_DATABASE_URL не должен содержать пробелов")
    scheme = parts.scheme.lower()
    if scheme not in _POSTGRES_SCHEMES:
        raise ConfigError(
            "CONTENT_FACTORY_DATABASE_URL должен начинаться с postgresql:// или postgres://"
        )
    if _SECOND_SCHEME.match(text[len(parts.scheme) + 3 :]):
        raise ConfigError("CONTENT_FACTORY_DATABASE_URL не должен содержать второй префикс схемы")
    if not parts.netloc or not parts.path.lstrip("/"):
        raise ConfigError("CONTENT_FACTORY_DATABASE_URL должен содержать хост и имя базы")
    try:
        _ = parts.port
    except ValueError as ex:
        raise ConfigError("CONTENT_FACTORY_DATABASE_URL содержит неверный порт") from ex
    query_parameters = {name.lower() for name, _ in parse_qsl(parts.query, keep_blank_values=True)}
    if "password" in query_parameters:
        raise ConfigError(
            "CONTENT_FACTORY_DATABASE_URL: пароль передавайте в userinfo, а не в query"
        )
    retargeting = query_parameters & _QUERY_TARGET_PARAMETERS
    if retargeting:
        names = ", ".join(sorted(retargeting))
        raise ConfigError(
            f"CONTENT_FACTORY_DATABASE_URL: параметры {names} в query не поддерживаются; "
            "укажите хост и имя базы в адресе"
        )
    return parts._replace(scheme=scheme).geturl()


def redact_database_url(value: str) -> str:
    """Return a DSN safe for output, with the userinfo replaced by ``***``.

    Non-PostgreSQL values are returned unchanged. A malformed value never
    raises: its credentials cannot be recovered and are hidden completely.
    """
    text = value.strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return f"postgresql://{_MASK}"
    scheme = parts.scheme.lower()
    if scheme not in _POSTGRES_SCHEMES:
        return text
    try:
        port = parts.port
    except ValueError:
        port = None
    hostname = parts.hostname or ""
    host = f"{hostname}:{port}" if port is not None else hostname
    database = parts.path.lstrip("/")
    userinfo = f"{_MASK}@" if parts.username is not None else ""
    target = f"{scheme}://{userinfo}{host}"
    return f"{target}/{database}" if database else target


def database_target_label(value: str) -> str:
    """Describe a DSN by host and database only, never by its credentials."""
    try:
        parts = urlsplit(value.strip())
        hostname = parts.hostname or "?"
        port = parts.port
    except ValueError:
        return "host=?, dbname=?"
    host = f"{hostname}:{port}" if port is not None else hostname
    database = parts.path.lstrip("/") or "?"
    return f"host={host}, dbname={database}"


def sanitize_database_text(text: str, dsn: str | None = None) -> str:
    """Mask database credentials that a driver may echo in an error.

    Args:
        text: Driver or command output.
        dsn: Configured DSN, if known. Its full text and password are removed
            in addition to the generic ``scheme://user:pass@`` pattern.

    Returns:
        The text with credentials replaced by ``***``.
    """
    sanitized = text
    secrets: set[str] = set()
    for candidate in _dsn_variants(dsn):
        sanitized = sanitized.replace(candidate, redact_database_url(candidate))
        password = _password_of(candidate)
        if password:
            secrets.add(password)
            decoded_password = unquote(password)
            if decoded_password:
                secrets.add(decoded_password)
        secrets.update(_query_passwords(candidate))
    secrets.update(_query_passwords(text))
    for secret in secrets:
        sanitized = sanitized.replace(secret, _MASK)
    sanitized = _CREDENTIALS.sub(lambda match: f"{match.group('scheme')}{_MASK}@", sanitized)
    return _PASSWORD_FIELD.sub(f"password={_MASK}", sanitized)


def _dsn_variants(dsn: str | None) -> tuple[str, ...]:
    """Return the configured DSN with and without surrounding spaces."""
    if not dsn:
        return ()
    candidates = {dsn, dsn.strip()}
    return tuple(candidate for candidate in candidates if candidate)


def _password_of(dsn: str) -> str | None:
    """Return the password of a DSN, tolerating a malformed value."""
    try:
        return urlsplit(dsn).password
    except ValueError:
        return None


def _query_passwords(value: str) -> set[str]:
    """Return raw and decoded ``password`` values of a DSN query string.

    The regex parser tolerates percent-encoding errors that libpq rejects:
    sanitizing must work on a malformed DSN too.
    """
    _, separator, query = value.partition("?")
    if not separator:
        return set()
    query = query.partition("#")[0]
    passwords: set[str] = set()
    for chunk in re.split(r"[&;]", query):
        name, has_value, raw = chunk.partition("=")
        if not has_value or unquote(name).lower() != "password" or not raw:
            continue
        passwords.add(raw)
        decoded = unquote(raw)
        if decoded:
            passwords.add(decoded)
    return passwords


__all__ = [
    "database_target_label",
    "normalize_database_url",
    "redact_database_url",
    "sanitize_database_text",
]
