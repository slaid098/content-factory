from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR
from content_factory.exceptions import DeliveryError
from content_factory.services.delivery.service import cleanup_expired_archives, deliver
from content_factory.services.delivery.titles import load_descriptions, normalize_title
from delivery_helpers import REQUEST_ID, add_downloaded_video, touch_mp4

_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _archive_path(data_dir: Path, language: str = "ru") -> Path:
    return data_dir / ARCHIVE_DIR / language / f"{REQUEST_ID}.zip"


def _zip_entries(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.namelist()


def _zip_title(data: bytes, entry: str = "001.json") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return str(json.loads(archive.read(entry))["title"])


def _zip_bytes(data: bytes, entry: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(entry)


def test_normalize_title_collapses_whitespace() -> None:
    assert normalize_title("  Привет \n\t мир  ", "ru") == "Привет мир"


def test_normalize_title_removes_hashtags() -> None:
    assert normalize_title("Лучшее видео #рек #fyp", "ru") == "Лучшее видео"


@pytest.mark.parametrize(
    ("description", "language", "expected"),
    [
        (None, "ru", "Видео"),
        ("   ", "ru", "Видео"),
        ("#рек", "ru", "Видео"),
        (None, "en", "Video"),
        ("", "de", "Video"),
    ],
)
def test_normalize_title_falls_back_per_language(
    description: str | None,
    language: str,
    expected: str,
) -> None:
    assert normalize_title(description, language) == expected


def test_normalize_title_truncates_to_100_characters_with_ellipsis() -> None:
    title = normalize_title("а" * 150, "ru")

    assert len(title) == 100
    assert title.endswith("…")
    assert title == "а" * 99 + "…"


def test_normalize_title_keeps_short_text() -> None:
    assert normalize_title("Короткий заголовок", "ru") == "Короткий заголовок"


def test_load_descriptions_maps_sha_to_video_description(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    add_downloaded_video(db_path, "a" * 64, "Описание видео")
    add_downloaded_video(db_path, "b" * 64, "Другое описание")

    descriptions = load_descriptions(db_path, ["a" * 64, "missing"])

    assert descriptions == {"a" * 64: "Описание видео"}


def test_load_descriptions_without_hashes_returns_empty(tmp_path: Path) -> None:
    assert load_descriptions(tmp_path / "missing.sqlite3", []) == {}


def test_load_descriptions_without_database_returns_empty(tmp_path: Path) -> None:
    assert load_descriptions(tmp_path / "missing.sqlite3", ["a" * 64]) == {}


def test_load_descriptions_with_broken_database_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    assert load_descriptions(db_path, ["a" * 64]) == {}


def test_deliver_builds_zip_with_real_titles_and_removes_sources(tmp_path: Path) -> None:
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    add_downloaded_video(tmp_path / "db" / "db.sqlite3", first_hash, "  Первое #рек ")
    add_downloaded_video(tmp_path / "db" / "db.sqlite3", second_hash, "#толькотеги")

    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, retention_hours=24)

    assert archive == _archive_path(tmp_path)
    assert archive is not None
    data = archive.read_bytes()
    assert sorted(_zip_entries(data)) == ["001.json", "001.mp4", "002.json", "002.mp4"]
    assert _zip_bytes(data, "001.mp4") == b"first"
    assert _zip_bytes(data, "002.mp4") == b"second"
    assert _zip_title(data) == "Первое"
    assert _zip_title(data, "002.json") == "Видео"
    assert not first.exists()
    assert not second.exists()


def test_deliver_returns_none_without_mp4(tmp_path: Path) -> None:
    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert not (tmp_path / ARCHIVE_DIR / "ru").exists()


def test_deliver_uses_fallback_title_without_database(tmp_path: Path) -> None:
    touch_mp4(tmp_path / CONTENT_DIR / "en" / ("a" * 64 + ".mp4"))

    archive = deliver(tmp_path, "en", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    assert _zip_title(archive.read_bytes()) == "Video"


def test_deliver_uses_fallback_title_for_empty_description(tmp_path: Path) -> None:
    sha256 = "a" * 64
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(tmp_path / "db" / "db.sqlite3", sha256, "")

    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    assert _zip_title(archive.read_bytes()) == "Видео"


def test_deliver_uses_fallback_titles_for_broken_database(tmp_path: Path) -> None:
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    assert _zip_title(archive.read_bytes()) == "Видео"


def test_deliver_returns_existing_archive_and_keeps_new_stock(tmp_path: Path) -> None:
    directory = tmp_path / CONTENT_DIR / "ru"
    touch_mp4(directory / "a.mp4", b"old")
    first = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)
    assert first is not None
    original = first.read_bytes()
    touch_mp4(directory / "0.mp4", b"new")
    touch_mp4(directory / "b.mp4", b"other")

    second = deliver(tmp_path, "ru", 2, REQUEST_ID, retention_hours=24)

    assert second == first
    assert second.read_bytes() == original
    assert (directory / "0.mp4").is_file()
    assert (directory / "b.mp4").is_file()


def test_deliver_rebuilds_expired_archive(tmp_path: Path) -> None:
    directory = tmp_path / CONTENT_DIR / "ru"
    touch_mp4(directory / "a.mp4", b"old")
    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24, now=_MOMENT)
    assert archive is not None
    old = _MOMENT - timedelta(hours=25)
    os.utime(archive, (old.timestamp(), old.timestamp()))
    touch_mp4(directory / "new.mp4", b"new")

    rebuilt = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24, now=_MOMENT)

    assert rebuilt == archive
    data = rebuilt.read_bytes()
    assert _zip_bytes(data, "001.mp4") == b"new"
    assert not (directory / "new.mp4").exists()


def test_deliver_discards_tmp_and_keeps_sources_on_build_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / "a.mp4")

    def _fail(self: zipfile.ZipFile, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "write", _fail)

    with pytest.raises(DeliveryError):
        deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert source.is_file()
    archive_dir = tmp_path / ARCHIVE_DIR / "ru"
    assert list(archive_dir.glob("*.tmp")) == []


def test_deliver_returns_archive_when_source_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / "a.mp4")
    original_unlink = Path.unlink

    def _deny_mp4(self: Path, *args: object, **kwargs: object) -> None:
        if self.suffix == ".mp4":
            raise OSError("permission denied")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _deny_mp4)

    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    assert archive.is_file()
    assert source.is_file()


def test_cleanup_removes_expired_archives_and_keeps_fresh(tmp_path: Path) -> None:
    directory = tmp_path / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    old = directory / "old.zip"
    fresh = directory / "fresh.zip"
    old.write_bytes(b"old")
    fresh.write_bytes(b"fresh")
    stale = (_MOMENT - timedelta(hours=25)).timestamp()
    os.utime(old, (stale, stale))

    removed = cleanup_expired_archives(tmp_path, 24, now=_MOMENT)

    assert removed == 1
    assert not old.exists()
    assert fresh.is_file()


def test_cleanup_ignores_non_zip_files(tmp_path: Path) -> None:
    directory = tmp_path / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    notes = directory / "notes.txt"
    notes.write_text("keep", encoding="utf-8")
    stale = (_MOMENT - timedelta(hours=25)).timestamp()
    os.utime(notes, (stale, stale))

    assert cleanup_expired_archives(tmp_path, 24, now=_MOMENT) == 0
    assert notes.is_file()


def test_cleanup_without_archive_directory_returns_zero(tmp_path: Path) -> None:
    assert cleanup_expired_archives(tmp_path, 24, now=_MOMENT) == 0


def test_cleanup_logs_unlink_error_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    broken = directory / "broken.zip"
    expired = directory / "expired.zip"
    broken.write_bytes(b"broken")
    expired.write_bytes(b"expired")
    stale = (_MOMENT - timedelta(hours=25)).timestamp()
    os.utime(broken, (stale, stale))
    os.utime(expired, (stale, stale))
    original_unlink = Path.unlink

    def _deny_broken(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "broken.zip":
            raise OSError("permission denied")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _deny_broken)

    removed = cleanup_expired_archives(tmp_path, 24, now=_MOMENT)

    assert removed == 1
    assert broken.is_file()
    assert not expired.exists()


def test_cleanup_logs_archive_directory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / ARCHIVE_DIR
    archive_root.mkdir()
    original_iterdir = Path.iterdir

    def _deny_archive(self: Path) -> object:
        if self == archive_root:
            raise PermissionError("permission denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _deny_archive)

    assert cleanup_expired_archives(tmp_path, 24, now=_MOMENT) == 0


def test_cleanup_logs_language_directory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    original_glob = Path.glob

    def _deny_language(self: Path, pattern: str) -> object:
        if self == directory:
            raise PermissionError("permission denied")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", _deny_language)

    assert cleanup_expired_archives(tmp_path, 24, now=_MOMENT) == 0


def test_cleanup_skips_archive_removed_by_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    archive = directory / "gone.zip"
    archive.write_bytes(b"gone")
    original_stat = Path.stat

    def _missing_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == archive:
            raise FileNotFoundError(self)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _missing_stat)

    assert cleanup_expired_archives(tmp_path, 24, now=_MOMENT) == 0
