"""Language search queries: file listing and append."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import tempfile
import threading
import unicodedata
from contextlib import suppress
from pathlib import Path

from loguru import logger

from content_factory.constants import DB_DIR, DB_FILENAME, QUERIES_DIR
from content_factory.db.connection import connect_readonly
from content_factory.exceptions.errors import DeliveryError, SupplierError
from content_factory.schemas.content.queries import QueriesResponse, QueryCount
from content_factory.services import supplier
from content_factory.services.delivery.topics import collect_topics

_HEADER = "# Добавлено из интерфейса"
_LANGUAGE_RE = re.compile(r"^[a-z]{2}$")
_MAX_QUERY_LENGTH = 200
_WRITE_LOCK = threading.Lock()


def list_language_queries(data_dir: Path, language: str) -> QueriesResponse:
    """List every search query of one language with its remaining stock.

    File queries keep their source order. Queries known only to the database
    are appended after them with ``in_file=False``. ``total`` is the whole
    language stock: every query count plus unclassified files.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.

    Returns:
        The query list, unclassified stock count and total stock count.

    Raises:
        ValueError: The language code is not two lowercase letters.
        DeliveryError: The query file exists but cannot be read.
    """
    _validate_language(language)
    try:
        file_queries = supplier.read_language_queries(data_dir / QUERIES_DIR, language)
    except SupplierError as ex:
        raise DeliveryError(f"не удалось прочитать запросы {language}: {ex}") from ex

    db_queries = _database_queries(data_dir / DB_DIR / DB_FILENAME, language)
    counts: dict[str, int] = {}
    unclassified = 0
    total = 0
    if db_queries is not None:
        topics = collect_topics(data_dir, language)
        counts = {topic.query: topic.count for topic in topics.topics}
        unclassified = topics.unclassified
        total = sum(counts.values()) + unclassified

    file_set = set(file_queries)
    queries = [
        QueryCount(query=query, count=counts.get(query, 0), in_file=True) for query in file_queries
    ]
    extra = sorted((set(db_queries or ()) | set(counts)) - file_set)
    queries.extend(
        QueryCount(query=query, count=counts.get(query, 0), in_file=False) for query in extra
    )
    return QueriesResponse(
        language=language,
        queries=tuple(queries),
        unclassified=unclassified,
        total=total,
    )


def normalize_query(value: str) -> str:
    """Normalize spaces and validate one search query.

    Args:
        value: Raw query from the request body.

    Returns:
        The query with collapsed whitespace and no surrounding spaces.

    Raises:
        ValueError: The query is empty, longer than 200 characters, starts
            with ``#`` or contains a control character.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("запрос не должен содержать переводы строк")
    if any(unicodedata.category(char) == "Cc" and char != "\t" for char in value):
        raise ValueError("запрос содержит управляющие символы")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("запрос не может быть пустым")
    if normalized.startswith("#"):
        raise ValueError("запрос не должен начинаться с #")
    if len(normalized) > _MAX_QUERY_LENGTH:
        raise ValueError("запрос длиннее 200 символов")
    return normalized


def add_language_query(data_dir: Path, language: str, query: str) -> tuple[str, bool]:
    """Append one validated query to the language file atomically.

    The query is skipped when the file already has it regardless of case. New
    queries go to the ``# Добавлено из интерфейса`` section; the file is
    rewritten through a temporary file in the same directory.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.
        query: Raw query from the request body.

    Returns:
        The normalized query and whether it was added.

    Raises:
        ValueError: The language code or the query is invalid.
        DeliveryError: The existing file cannot be read or the new one cannot
            be written.
    """
    _validate_language(language)
    normalized = normalize_query(query)
    directory = data_dir / QUERIES_DIR
    path = directory / f"{language}.txt"
    with _WRITE_LOCK:
        text, has_bom = _read_existing(path)
        duplicate = any(
            existing.casefold() == normalized.casefold()
            for existing in supplier.parse_language_queries(text)
        )
        if duplicate:
            return normalized, False
        _write_atomic(path, _append_to_section(text, normalized), has_bom=has_bom)
    return normalized, True


def _validate_language(language: str) -> None:
    """Reject language codes that cannot be used as file names."""
    if _LANGUAGE_RE.fullmatch(language) is None:
        raise ValueError(f"недопустимый код языка: {language!r}")


def _database_queries(db_path: Path, language: str) -> list[str] | None:
    """Return distinct database queries of a language, or ``None`` without a DB."""
    if not db_path.is_file():
        return None
    try:
        connection = connect_readonly(db_path)
        try:
            rows = connection.execute(
                "SELECT DISTINCT query FROM videos "
                "WHERE language = ? AND query <> '' ORDER BY query",
                (language,),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать запросы из базы"
        )
        return None
    return [str(row["query"]) for row in rows]


def _read_existing(path: Path) -> tuple[str, bool]:
    """Read the whole query file, keeping the optional UTF-8 BOM flag."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "", False
    except OSError as ex:
        raise DeliveryError(f"не удалось прочитать {path}: {ex}") from ex
    has_bom = data.startswith(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8-sig"), has_bom
    except UnicodeError as ex:
        raise DeliveryError(f"не удалось прочитать {path}: {ex}") from ex


def _append_to_section(text: str, query: str) -> str:
    """Insert a query at the end of the interface section."""
    newline = "\r\n" if "\r\n" in text else "\n"
    if not text:
        return f"{_HEADER}{newline}{query}{newline}"

    lines = text.splitlines(keepends=True)
    header_index = _header_index(lines)
    if header_index is None:
        prefix = text
        if not prefix.endswith(("\n", "\r")):
            prefix += newline
        if not prefix.endswith(newline + newline):
            prefix += newline
        return f"{prefix}{_HEADER}{newline}{query}{newline}"

    insert_at = _section_end(lines, header_index)
    if insert_at > 0 and not lines[insert_at - 1].endswith(("\n", "\r")):
        lines[insert_at - 1] += newline
    lines.insert(insert_at, f"{query}{newline}")
    return "".join(lines)


def _header_index(lines: list[str]) -> int | None:
    """Return the last interface section header index."""
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() == _HEADER:
            return index
    return None


def _section_end(lines: list[str], header_index: int) -> int:
    """Return the position after the last non-blank line of the section."""
    limit = len(lines)
    for index in range(header_index + 1, len(lines)):
        if lines[index].startswith("#"):
            limit = index
            break
    insert_at = limit
    while insert_at > header_index + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    return insert_at


def _write_atomic(path: Path, text: str, *, has_bom: bool) -> None:
    """Replace the query file through a temporary file in its directory."""
    encoding = "utf-8-sig" if has_bom else "utf-8"
    mode: int | None = None
    with suppress(OSError):
        mode = stat.S_IMODE(path.stat().st_mode)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as ex:
        raise DeliveryError(f"не удалось записать {path}: {ex}") from ex
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as writer:
            writer.write(text)
            writer.flush()
            os.fsync(writer.fileno())
        if mode is not None:
            Path(tmp_name).chmod(mode)
        Path(tmp_name).replace(path)
    except OSError as ex:
        raise DeliveryError(f"не удалось записать {path}: {ex}") from ex
    finally:
        with suppress(OSError):
            Path(tmp_name).unlink(missing_ok=True)
