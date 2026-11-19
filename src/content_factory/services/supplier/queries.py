"""Запросы поиска: один файл ``<language>.txt`` на язык."""

from __future__ import annotations

import random
import re
from pathlib import Path

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.search import QuerySpec

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")


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
    if _LANGUAGE_RE.fullmatch(language) is None:
        raise SupplierError(f"недопустимый код языка: {language!r}")
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

    random.shuffle(result)
    if max_queries is not None:
        result = result[: max(0, max_queries)]
    return result
