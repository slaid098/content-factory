"""Запросы поиска: один файл ``<language>.txt`` на язык."""

from __future__ import annotations

import random
import re
from pathlib import Path

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.search import QuerySpec

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")


def parse_language_queries(text: str) -> list[str]:
    """Разобрать содержимое файла запросов без перемешивания.

    Пустые строки и комментарии (строки, начинающиеся с ``#``) пропускаются,
    пробелы внутри запроса нормализуются, порядок строк сохраняется.

    Args:
        text: Содержимое файла ``<language>.txt``.

    Returns:
        Запросы в исходном порядке; повторы не удаляются.
    """
    queries: list[str] = []
    for line in text.splitlines():
        value = " ".join(line.strip().split())
        if not value or value.startswith("#"):
            continue
        queries.append(value)
    return queries


def read_language_queries(directory: Path, language: str) -> list[str]:
    """Прочитать строки запросов одного языка из ``<language>.txt``.

    Args:
        directory: Каталог с файлами запросов.
        language: Код языка, например ``ru``.

    Returns:
        Запросы в порядке файла без комментариев и пустых строк. Пустой
        список означает, что файла нет или в нём нет строк запроса.

    Raises:
        SupplierError: Код языка недопустим или файл существует, но не читается.
    """
    if _LANGUAGE_RE.fullmatch(language) is None:
        raise SupplierError(f"недопустимый код языка: {language!r}")
    path = directory / f"{language}.txt"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as ex:
        raise SupplierError(f"не удалось прочитать {path}: {ex}") from ex
    return parse_language_queries(text)


def load_language_queries(
    directory: Path,
    language: str,
    *,
    max_queries: int | None = None,
) -> list[QuerySpec]:
    """Прочитать запросы одного языка из ``<language>.txt``.

    Запросы перемешиваются, чтобы каждый запуск начинал с разных строк
    файла, и ограничиваются ``max_queries``.

    Args:
        directory: Каталог с файлами запросов.
        language: Код языка, например ``ru``.
        max_queries: Максимум запросов; ``None`` означает без предела.

    Returns:
        Уникальные перемешанные запросы. Пустой список означает, что файла
        нет, в нём нет строк запроса или лимит равен нулю.

    Raises:
        SupplierError: Код языка недопустим или файл существует, но не читается.
    """
    path = directory / f"{language}.txt"
    seen: set[str] = set()
    result: list[QuerySpec] = []
    for query in read_language_queries(directory, language):
        if query in seen:
            continue
        seen.add(query)
        result.append(QuerySpec(query=query, language=language, source=path))

    random.shuffle(result)
    if max_queries is not None:
        result = result[: max(0, max_queries)]
    return result
