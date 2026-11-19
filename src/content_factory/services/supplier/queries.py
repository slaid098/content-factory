"""Запросы поиска: один файл ``<language>.txt`` на язык."""

from __future__ import annotations

from pathlib import Path

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.search import QuerySpec


def _read_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as ex:
        raise SupplierError(f"не удалось прочитать {path}: {ex}") from ex
    queries: list[str] = []
    for line in text.splitlines():
        value = " ".join(line.strip().split())
        if not value or value.startswith("#"):
            continue
        queries.append(value)
    return queries


def load_language_queries(directory: Path, language: str) -> list[QuerySpec]:
    """Прочитать запросы одного языка из ``<language>.txt``.

    Args:
        directory: Каталог с файлами запросов.
        language: Код языка, например ``ru``.

    Returns:
        Уникальные запросы в порядке файла. Пустой список означает, что файла
        нет или в нём нет ни одной строки запроса.

    Raises:
        SupplierError: Файл существует, но не читается.
    """
    path = directory / f"{language}.txt"
    if not path.is_file():
        return []
    seen: set[str] = set()
    result: list[QuerySpec] = []
    for query in _read_file(path):
        if query in seen:
            continue
        seen.add(query)
        result.append(QuerySpec(query=query, language=language, source=path))
    return result
