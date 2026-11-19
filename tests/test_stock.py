from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from content_factory.schemas.stock.stock import LanguageStock, StockSnapshot
from content_factory.services.stock.service import count_stock


def _touch(path: Path, content: bytes = b"video") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_counts_only_mp4_files_of_each_language(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    _touch(content_dir / "ru" / "a.mp4")
    _touch(content_dir / "ru" / "b.mp4")
    _touch(content_dir / "ru" / "c.tmp")
    _touch(content_dir / "ru" / "d.part")
    _touch(content_dir / "ru" / "notes.txt")
    _touch(content_dir / "ru" / "nested" / "inside.mp4")
    (content_dir / "ru" / "directory.mp4").mkdir()

    counts = count_stock(content_dir, ["ru", "en"])

    assert counts == {"ru": 2, "en": 0}


def test_missing_language_directory_counts_as_zero(tmp_path: Path) -> None:
    assert count_stock(tmp_path / "missing", ["ru"]) == {"ru": 0}


def test_empty_language_directory_counts_as_zero(tmp_path: Path) -> None:
    (tmp_path / "content" / "ru").mkdir(parents=True)

    assert count_stock(tmp_path / "content", ["ru"]) == {"ru": 0}


def test_snapshot_keeps_language_positions() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = StockSnapshot(
        generated_at=moment,
        languages=(
            LanguageStock(language="ru", stock=3, target=150, deficit=147),
            LanguageStock(language="en", stock=100, target=100, deficit=0),
        ),
    )

    assert snapshot.generated_at == moment
    assert snapshot.languages[0].deficit == 147
