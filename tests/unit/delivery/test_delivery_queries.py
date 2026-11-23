from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from tests.unit.common.delivery_helpers import add_downloaded_video, touch_mp4

from content_factory.constants import CONTENT_DIR, QUERIES_DIR
from content_factory.db.delivery import load_language_queries
from content_factory.exceptions import DeliveryError
from content_factory.services.delivery.queries import (
    add_language_query,
    list_language_queries,
    normalize_query,
)

_HEADER = "# Добавлено из интерфейса"


def _write_queries(data_dir: Path, text: str) -> Path:
    path = data_dir / QUERIES_DIR / "ru.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _add_stock(data_dir: Path, sha256: str, query: str, *, description: str = "Описание") -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(data_dir / "db" / "db.sqlite3", sha256, description, query=query)


def test_load_language_queries_returns_distinct_non_empty_queries(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    tricky = "кофе'; DROP TABLE videos; --"
    for sha256, query in (
        ("a" * 64, "курятник"),
        ("b" * 64, "курятник"),
        ("c" * 64, "заправка"),
        ("d" * 64, ""),
        ("e" * 64, tricky),
    ):
        add_downloaded_video(db_path, sha256, "Описание", query=query)
    add_downloaded_video(db_path, "f" * 64, "Описание", query="coop", language="en")

    ru = load_language_queries(db_path, "ru")

    assert ru is not None
    assert len(ru) == 3  # DISTINCT, пустая строка исключена
    assert set(ru) == {"заправка", "курятник", tricky}  # кавычка и ';' буквально
    assert ru == sorted(ru)  # ORDER BY query
    assert "coop" not in ru  # WHERE language = ? отсекает en
    assert load_language_queries(db_path, "en") == ["coop"]


def test_load_language_queries_returns_none_without_database(tmp_path: Path) -> None:
    assert load_language_queries(tmp_path / "db" / "db.sqlite3", "ru") is None


def test_load_language_queries_returns_none_on_broken_database(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    assert load_language_queries(db_path, "ru") is None


def test_list_returns_file_order_counts_and_database_queries(tmp_path: Path) -> None:
    _write_queries(
        tmp_path,
        "# Список запросов\nкурятник\nзаправка\n\n# Добавлено из интерфейса\nновый\n",
    )
    _add_stock(tmp_path, "a" * 64, "курятник")
    _add_stock(tmp_path, "b" * 64, "курятник")
    _add_stock(tmp_path, "c" * 64, "исторический")
    _add_stock(tmp_path, "d" * 64, "")
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("e" * 64 + ".mp4"))

    response = list_language_queries(tmp_path, "ru")

    assert response.language == "ru"
    assert [(item.query, item.count, item.in_file) for item in response.queries] == [
        ("курятник", 2, True),
        ("заправка", 0, True),
        ("новый", 0, True),
        ("исторический", 1, False),
    ]
    assert response.unclassified == 2
    assert response.total == 5


def test_list_without_file_returns_database_queries(tmp_path: Path) -> None:
    _add_stock(tmp_path, "a" * 64, "старый запрос")

    response = list_language_queries(tmp_path, "ru")

    assert [(item.query, item.count, item.in_file) for item in response.queries] == [
        ("старый запрос", 1, False),
    ]
    assert response.unclassified == 0
    assert response.total == 1


def test_list_without_database_returns_zero_counts(tmp_path: Path) -> None:
    _write_queries(tmp_path, "первый\nвторой\n")
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))

    response = list_language_queries(tmp_path, "ru")

    assert [(item.query, item.count, item.in_file) for item in response.queries] == [
        ("первый", 0, True),
        ("второй", 0, True),
    ]
    assert response.unclassified == 0
    assert response.total == 0


def test_list_with_broken_database_returns_zero_counts(tmp_path: Path) -> None:
    _write_queries(tmp_path, "первый\n")
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    response = list_language_queries(tmp_path, "ru")

    assert [(item.query, item.count) for item in response.queries] == [("первый", 0)]
    assert response.unclassified == 0
    assert response.total == 0


def test_list_without_stock_keeps_zero_counts(tmp_path: Path) -> None:
    _write_queries(tmp_path, "первый\nвторой\n")

    response = list_language_queries(tmp_path, "ru")

    assert [item.count for item in response.queries] == [0, 0]
    assert response.unclassified == 0
    assert response.total == 0


def test_list_accepts_three_letter_language(tmp_path: Path) -> None:
    path = tmp_path / QUERIES_DIR / "eng.txt"
    path.parent.mkdir(parents=True)
    path.write_text("query\n", encoding="utf-8")

    response = list_language_queries(tmp_path, "eng")

    assert response.language == "eng"
    assert [item.query for item in response.queries] == ["query"]


@pytest.mark.parametrize("language", ["r", "RU", "ru-RU", "ru/", "../ru", ""])
def test_list_rejects_invalid_language(tmp_path: Path, language: str) -> None:
    with pytest.raises(ValueError, match="недопустимый код языка"):
        list_language_queries(tmp_path, language)


def test_list_wraps_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_queries(tmp_path, "первый\n")

    def deny(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", deny)

    with pytest.raises(DeliveryError, match="не удалось прочитать"):
        list_language_queries(tmp_path, "ru")


def test_normalize_query_collapses_whitespace() -> None:
    assert normalize_query("  новый \t  запрос ") == "новый запрос"


def test_normalize_query_accepts_boundaries() -> None:
    assert normalize_query("a") == "a"
    assert normalize_query("  " + "a" * 200 + "  ") == "a" * 200


@pytest.mark.parametrize("value", ["", "   ", "\t", "a" * 201, "a\x00b", "a\x07b"])
def test_normalize_query_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_query(value)


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "\n"])
def test_normalize_query_rejects_line_breaks(value: str) -> None:
    with pytest.raises(ValueError, match="переводы строк"):
        normalize_query(value)


@pytest.mark.parametrize("value", ["#запрос", "  # запрос "])
def test_normalize_query_rejects_comment_like_values(value: str) -> None:
    with pytest.raises(ValueError, match="начинаться с #"):
        normalize_query(value)


def test_add_creates_file_with_header(tmp_path: Path) -> None:
    query, created = add_language_query(tmp_path, "ru", "  новый   запрос ")

    assert (query, created) == ("новый запрос", True)
    assert (tmp_path / QUERIES_DIR / "ru.txt").read_text(encoding="utf-8") == (
        f"{_HEADER}\nновый запрос\n"
    )


def test_add_preserves_comments_and_keeps_existing_order(tmp_path: Path) -> None:
    original = "# Список запросов\n# Второй комментарий\nкурятник"
    path = _write_queries(tmp_path, original)

    add_language_query(tmp_path, "ru", "новый")

    text = path.read_text(encoding="utf-8")
    assert text.startswith(f"{original}\n\n{_HEADER}\n")
    assert text.splitlines()[-1] == "новый"


def test_add_appends_to_existing_section(tmp_path: Path) -> None:
    path = _write_queries(
        tmp_path,
        f"# Список\nкурятник\n\n{_HEADER}\nстарый\n",
    )

    add_language_query(tmp_path, "ru", "новый")

    assert path.read_text(encoding="utf-8").splitlines() == [
        "# Список",
        "курятник",
        "",
        _HEADER,
        "старый",
        "новый",
    ]


def test_add_keeps_section_before_next_heading(tmp_path: Path) -> None:
    path = _write_queries(
        tmp_path,
        f"{_HEADER}\nстарый\n# Другой раздел\nдругое\n",
    )

    add_language_query(tmp_path, "ru", "новый")

    assert path.read_text(encoding="utf-8").splitlines() == [
        _HEADER,
        "старый",
        "новый",
        "# Другой раздел",
        "другое",
    ]


def test_add_duplicate_is_case_and_space_insensitive(tmp_path: Path) -> None:
    path = _write_queries(tmp_path, "Курятник\n")
    original = path.read_text(encoding="utf-8")

    query, created = add_language_query(tmp_path, "ru", "  курятник  ")

    assert (query, created) == ("курятник", False)
    assert path.read_text(encoding="utf-8") == original


def test_add_duplicate_inside_interface_section_is_skipped(tmp_path: Path) -> None:
    path = _write_queries(tmp_path, f"{_HEADER}\nСтарый Запрос\n")
    original = path.read_text(encoding="utf-8")

    assert add_language_query(tmp_path, "ru", "старый запрос") == ("старый запрос", False)
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("language", ["r", "RU", "ru-RU", "../ru"])
def test_add_rejects_invalid_language(tmp_path: Path, language: str) -> None:
    with pytest.raises(ValueError, match="недопустимый код языка"):
        add_language_query(tmp_path, language, "запрос")


def test_add_wraps_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_queries(tmp_path, "старый\n")
    original_read = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self == path:
            raise OSError("denied")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", deny)

    with pytest.raises(DeliveryError, match="не удалось прочитать"):
        add_language_query(tmp_path, "ru", "новый")


def test_add_wraps_write_error_and_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_queries(tmp_path, "старый\n")
    original = path.read_text(encoding="utf-8")

    def deny(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", deny)

    with pytest.raises(DeliveryError, match="не удалось записать"):
        add_language_query(tmp_path, "ru", "новый")

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob(".*.tmp")) == []


def test_add_keeps_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / QUERIES_DIR / "ru.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes("\ufeffстарый\n".encode())

    add_language_query(tmp_path, "ru", "новый")

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "старый",
        "",
        _HEADER,
        "новый",
    ]


def test_add_is_atomic_under_concurrency(tmp_path: Path) -> None:
    def append(_index: int) -> bool:
        return add_language_query(tmp_path, "ru", "одновременно")[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(append, range(8)))

    assert results.count(True) == 1
    text = (tmp_path / QUERIES_DIR / "ru.txt").read_text(encoding="utf-8")
    assert text.count("одновременно") == 1
    assert text.count(_HEADER) == 1
