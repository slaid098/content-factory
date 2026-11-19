from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.services.supplier.queries import load_language_queries


def test_load_language_queries_reads_one_language(tmp_path: Path) -> None:
    (tmp_path / "ru.txt").write_text(
        "# comment\nкак пережить расставание\n\nкак пережить расставание\n",
        encoding="utf-8",
    )
    (tmp_path / "en.txt").write_text("how to get over a breakup\n", encoding="utf-8")

    queries = load_language_queries(tmp_path, "ru")

    assert [(item.query, item.language) for item in queries] == [
        ("как пережить расставание", "ru"),
    ]
    assert queries[0].source == tmp_path / "ru.txt"


def test_load_language_queries_normalizes_whitespace(tmp_path: Path) -> None:
    (tmp_path / "ru.txt").write_text("  как   пережить  \n", encoding="utf-8")

    assert load_language_queries(tmp_path, "ru")[0].query == "как пережить"


def test_load_language_queries_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    (tmp_path / "ru.txt").write_text(
        "# only comment\n\n   \nзапрос\n  # indented comment\n",
        encoding="utf-8",
    )

    assert [item.query for item in load_language_queries(tmp_path, "ru")] == ["запрос"]


def test_load_language_queries_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_language_queries(tmp_path, "de") == []


def test_load_language_queries_empty_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "ru.txt").write_text("# nothing\n", encoding="utf-8")

    assert load_language_queries(tmp_path, "ru") == []


def test_load_language_queries_handles_bom(tmp_path: Path) -> None:
    (tmp_path / "ru.txt").write_bytes("\ufeffзапрос\n".encode())

    assert [item.query for item in load_language_queries(tmp_path, "ru")] == ["запрос"]


def test_load_language_queries_shuffles_and_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "ru.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(
        "content_factory.services.supplier.queries.random.shuffle",
        lambda items: items.reverse(),
    )

    queries = load_language_queries(tmp_path, "ru", max_queries=2)

    assert [item.query for item in queries] == ["three", "two"]


def test_load_language_queries_rejects_invalid_language(tmp_path: Path) -> None:
    with pytest.raises(SupplierError, match="недопустимый код языка"):
        load_language_queries(tmp_path, "../x")


def test_load_language_queries_unreadable_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "ru.txt").write_text("запрос\n", encoding="utf-8")

    def fail(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", fail)

    with pytest.raises(SupplierError, match="не удалось прочитать"):
        load_language_queries(tmp_path, "ru")
