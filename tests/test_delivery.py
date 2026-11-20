from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR
from content_factory.exceptions import DeliveryError
from content_factory.schemas.content.delivery import DeliveryJobInfo
from content_factory.services.delivery.archives import (
    find_archive,
    list_archives,
    resolve_archive,
)
from content_factory.services.delivery.metadata import VideoMeta, load_video_meta
from content_factory.services.delivery.service import (
    _build_archive,
    cleanup_expired_archives,
    count_archive_files,
    deliver,
)
from content_factory.services.delivery.sidecars import save_job
from content_factory.services.delivery.topics import collect_topics
from delivery_helpers import REQUEST_ID, add_downloaded_video, add_title, touch_mp4

_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _archive_path(data_dir: Path, language: str = "ru") -> Path:
    return data_dir / ARCHIVE_DIR / language / f"{REQUEST_ID}.zip"


def _touch_archive(
    data_dir: Path,
    *,
    language: str = "ru",
    request_id: UUID = REQUEST_ID,
    data: bytes = b"zip",
    created_at: datetime = _MOMENT,
) -> Path:
    path = data_dir / ARCHIVE_DIR / language / f"{request_id}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (created_at.timestamp(), created_at.timestamp()))
    return path


def _zip_entries(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.namelist()


def _sidecar(
    data_dir: Path,
    *,
    status: str,
    created_at: datetime = _MOMENT,
    expires_at: datetime | None = None,
    size: int = 0,
    files: int = 0,
    url: str | None = None,
    error: str | None = None,
    language: str = "ru",
    request_id: UUID = REQUEST_ID,
) -> DeliveryJobInfo:
    job = DeliveryJobInfo(
        request_id=request_id,
        language=language,
        status=status,  # type: ignore[arg-type]
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(hours=24),
        size=size,
        files=files,
        url=url,
        error=error,
    )
    save_job(data_dir, job)
    return job


def _zip_title(data: bytes, entry: str = "001.json") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return str(json.loads(archive.read(entry))["title"])


def _zip_bytes(data: bytes, entry: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(entry)


def test_deliver_builds_zip_with_stored_titles_and_removes_sources(tmp_path: Path) -> None:
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    add_title(tmp_path / "db" / "db.sqlite3", first_hash, "Первое название")
    add_title(tmp_path / "db" / "db.sqlite3", second_hash, "Второе название")

    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, retention_hours=24)

    assert archive == _archive_path(tmp_path)
    assert archive is not None
    data = archive.read_bytes()
    assert sorted(_zip_entries(data)) == ["001.json", "001.mp4", "002.json", "002.mp4"]
    assert _zip_bytes(data, "001.mp4") == b"first"
    assert _zip_bytes(data, "002.mp4") == b"second"
    assert _zip_title(data) == "Первое название"
    assert _zip_title(data, "002.json") == "Второе название"
    assert not first.exists()
    assert not second.exists()


def test_deliver_returns_none_for_untitled_stock_and_keeps_file(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()
    assert not _archive_path(tmp_path).exists()


def test_deliver_selects_only_titled_files_from_mixed_stock(tmp_path: Path) -> None:
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    untitled = [
        touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
        for sha256 in ("c" * 64, "d" * 64, "e" * 64)
    ]
    add_title(tmp_path / "db" / "db.sqlite3", first_hash, "Первое название")
    add_title(tmp_path / "db" / "db.sqlite3", second_hash, "Второе название")

    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, retention_hours=24)

    assert archive is not None
    data = archive.read_bytes()
    assert sorted(_zip_entries(data)) == ["001.json", "001.mp4", "002.json", "002.mp4"]
    assert _zip_bytes(data, "001.mp4") == b"first"
    assert _zip_bytes(data, "002.mp4") == b"second"
    assert not first.exists()
    assert not second.exists()
    assert all(path.is_file() for path in untitled)


def test_deliver_writes_stored_title_without_normalization(tmp_path: Path) -> None:
    sha256 = "a" * 64
    title = "  Тихий   вечер #рек  " + "я" * 100
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", sha256, title)

    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    assert _zip_title(archive.read_bytes()) == title


def test_deliver_returns_none_without_database(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()
    assert not (tmp_path / "db" / "db.sqlite3").exists()


def test_deliver_returns_none_without_titles_table(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(tmp_path / "db" / "db.sqlite3", sha256, "Описание без названия")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()


def test_deliver_returns_none_with_broken_database(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()


def test_deliver_skips_empty_title(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", sha256, "", norm="empty-title")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()


def test_deliver_skips_whitespace_only_title(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", sha256, " \t\n ", norm="whitespace-title")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()
    assert not _archive_path(tmp_path).exists()
    assert not (tmp_path / ARCHIVE_DIR / "ru").exists()


def test_deliver_after_title_is_added(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert source.is_file()
    add_title(tmp_path / "db" / "db.sqlite3", sha256, "Название после прогона")

    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert archive is not None
    data = archive.read_bytes()
    assert _zip_entries(data) == ["001.mp4", "001.json"]
    assert _zip_title(data) == "Название после прогона"
    assert not source.exists()


def test_deliver_returns_none_without_mp4(tmp_path: Path) -> None:
    assert deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24) is None
    assert not (tmp_path / ARCHIVE_DIR / "ru").exists()


def test_deliver_returns_existing_archive_and_keeps_new_stock(tmp_path: Path) -> None:
    directory = tmp_path / CONTENT_DIR / "ru"
    touch_mp4(directory / "a.mp4", b"old")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Старое название")
    first = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)
    assert first is not None
    original = first.read_bytes()
    touch_mp4(directory / "0.mp4", b"new")
    touch_mp4(directory / "b.mp4", b"other")
    add_title(tmp_path / "db" / "db.sqlite3", "0", "Новое название")
    add_title(tmp_path / "db" / "db.sqlite3", "b", "Третье название")

    second = deliver(tmp_path, "ru", 2, REQUEST_ID, retention_hours=24)

    assert second == first
    assert second.read_bytes() == original
    assert (directory / "0.mp4").is_file()
    assert (directory / "b.mp4").is_file()


def test_deliver_rebuilds_expired_archive(tmp_path: Path) -> None:
    directory = tmp_path / CONTENT_DIR / "ru"
    touch_mp4(directory / "a.mp4", b"old")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Старое название")
    archive = deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24, now=_MOMENT)
    assert archive is not None
    old = _MOMENT - timedelta(hours=25)
    os.utime(archive, (old.timestamp(), old.timestamp()))
    touch_mp4(directory / "new.mp4", b"new")
    add_title(tmp_path / "db" / "db.sqlite3", "new", "Новое название")

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
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Название для ошибки")

    def _fail(self: zipfile.ZipFile, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "write", _fail)

    with pytest.raises(DeliveryError):
        deliver(tmp_path, "ru", 1, REQUEST_ID, retention_hours=24)

    assert source.is_file()
    archive_dir = tmp_path / ARCHIVE_DIR / "ru"
    assert list(archive_dir.glob("*.tmp")) == []


def test_build_archive_discards_tmp_when_title_is_missing(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    archive_path = _archive_path(tmp_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with pytest.raises(KeyError):
        _build_archive(archive_path, [source], {})

    assert source.is_file()
    assert not archive_path.exists()
    assert not archive_path.with_suffix(".tmp").exists()


def test_deliver_returns_archive_when_source_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / "a.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Название без удаления")
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


def test_load_video_meta_maps_description_and_query(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    add_downloaded_video(db_path, "a" * 64, "Описание", query="курятник")

    meta = load_video_meta(db_path, ["a" * 64, "missing"])

    assert meta == {"a" * 64: VideoMeta(description="Описание", query="курятник")}


def test_collect_topics_counts_by_query_and_unclassified(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    directory = tmp_path / CONTENT_DIR / "ru"
    for sha256, query in (
        ("a" * 64, "курятник"),
        ("b" * 64, "курятник"),
        ("c" * 64, "заправка"),
        ("d" * 64, ""),
    ):
        touch_mp4(directory / f"{sha256}.mp4")
        add_downloaded_video(db_path, sha256, "Описание", query=query)
    touch_mp4(directory / ("e" * 64 + ".mp4"))

    result = collect_topics(tmp_path, "ru")

    assert result.language == "ru"
    assert [(topic.query, topic.count) for topic in result.topics] == [
        ("заправка", 1),
        ("курятник", 2),
    ]
    assert result.unclassified == 2


def test_collect_topics_without_language_directory_returns_empty(tmp_path: Path) -> None:
    result = collect_topics(tmp_path, "ru")

    assert result.topics == ()
    assert result.unclassified == 0


def test_collect_topics_with_broken_database_classifies_everything(tmp_path: Path) -> None:
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    result = collect_topics(tmp_path, "ru")

    assert result.topics == ()
    assert result.unclassified == 1


def test_deliver_with_query_keeps_other_topics_in_stock(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    first_hash = "a" * 64
    second_hash = "b" * 64
    other_hash = "c" * 64
    first = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    other = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{other_hash}.mp4", b"other")
    add_downloaded_video(db_path, first_hash, "Первое", query="курятник")
    add_downloaded_video(db_path, second_hash, "Второе", query="курятник")
    add_downloaded_video(db_path, other_hash, "Другое", query="заправка")
    add_title(db_path, first_hash, "Первое название")
    add_title(db_path, second_hash, "Второе название")
    add_title(db_path, other_hash, "Другое название")

    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, "курятник", retention_hours=24)

    assert archive is not None
    data = archive.read_bytes()
    assert _zip_bytes(data, "001.mp4") == b"first"
    assert _zip_bytes(data, "002.mp4") == b"second"
    assert sorted(_zip_entries(data)) == ["001.json", "001.mp4", "002.json", "002.mp4"]
    assert not first.exists()
    assert not second.exists()
    assert other.is_file()


def test_deliver_query_is_case_sensitive(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(
        tmp_path / "db" / "db.sqlite3",
        sha256,
        "Описание",
        query="Курятник",
    )
    add_title(tmp_path / "db" / "db.sqlite3", sha256, "Название с регистром")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, "курятник", retention_hours=24) is None
    assert source.is_file()


def test_deliver_returns_none_when_query_has_no_matches(tmp_path: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(
        tmp_path / "db" / "db.sqlite3",
        sha256,
        "Описание",
        query="заправка",
    )
    add_title(tmp_path / "db" / "db.sqlite3", sha256, "Название другой темы")

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, "курятник", retention_hours=24) is None
    assert source.is_file()
    assert not _archive_path(tmp_path).exists()


def test_deliver_query_searches_only_titled_files(tmp_path: Path) -> None:
    named_hash = "a" * 64
    untitled_hash = "b" * 64
    other_hash = "c" * 64
    named = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{named_hash}.mp4", b"named")
    untitled = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{untitled_hash}.mp4", b"untitled")
    other = touch_mp4(tmp_path / CONTENT_DIR / "ru" / f"{other_hash}.mp4", b"other")
    db_path = tmp_path / "db" / "db.sqlite3"
    add_downloaded_video(db_path, named_hash, "Первое", query="курятник")
    add_downloaded_video(db_path, untitled_hash, "Второе", query="курятник")
    add_downloaded_video(db_path, other_hash, "Третье", query="заправка")
    add_title(db_path, named_hash, "Названное про курятник")
    add_title(db_path, other_hash, "Названное про заправку")

    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, "курятник", retention_hours=24)

    assert archive is not None
    data = archive.read_bytes()
    assert _zip_entries(data) == ["001.mp4", "001.json"]
    assert _zip_bytes(data, "001.mp4") == b"named"
    assert _zip_title(data) == "Названное про курятник"
    assert not named.exists()
    assert untitled.is_file()
    assert other.is_file()


def test_deliver_with_query_and_missing_database_returns_none(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))

    assert deliver(tmp_path, "ru", 1, REQUEST_ID, "курятник", retention_hours=24) is None
    assert source.is_file()


def test_list_archives_returns_live_entries_newest_first(tmp_path: Path) -> None:
    older = _touch_archive(tmp_path, data=b"older")
    newer = _touch_archive(
        tmp_path,
        language="en",
        data=b"newer",
        created_at=_MOMENT + timedelta(hours=1),
    )

    archives = list_archives(tmp_path, 24, now=_MOMENT + timedelta(hours=2))

    assert [archive.language for archive in archives] == ["en", "ru"]
    assert archives[0].request_id == REQUEST_ID
    assert archives[0].size == len(b"newer")
    assert archives[0].created_at == _MOMENT + timedelta(hours=1)
    assert archives[0].expires_at == _MOMENT + timedelta(hours=25)
    assert archives[1].size == len(b"older")
    assert older.is_file()
    assert newer.is_file()


def test_list_archives_skips_expired_tmp_and_non_uuid_files(tmp_path: Path) -> None:
    fresh = _touch_archive(tmp_path, data=b"fresh")
    expired = _touch_archive(
        tmp_path,
        request_id=UUID(int=2),
        data=b"expired",
        created_at=_MOMENT - timedelta(hours=25),
    )
    directory = fresh.parent
    tmp_file = directory / f"{REQUEST_ID}.zip.tmp"
    tmp_file.write_bytes(b"tmp")
    invalid = directory / "not-a-uuid.zip"
    invalid.write_bytes(b"invalid")
    notes = directory / "notes.txt"
    notes.write_text("keep", encoding="utf-8")

    archives = list_archives(tmp_path, 24, now=_MOMENT)

    assert [archive.request_id for archive in archives] == [REQUEST_ID]
    assert fresh.is_file()
    assert expired.is_file()
    assert tmp_file.is_file()
    assert invalid.is_file()
    assert notes.is_file()


def test_list_archives_without_archive_directory_returns_empty(tmp_path: Path) -> None:
    assert list_archives(tmp_path, 24, now=_MOMENT) == ()


def test_list_archives_logs_archive_directory_error(
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

    assert list_archives(tmp_path, 24, now=_MOMENT) == ()


def test_list_archives_logs_language_directory_error(
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

    assert list_archives(tmp_path, 24, now=_MOMENT) == ()


def test_list_archives_skips_archive_with_unreadable_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _touch_archive(tmp_path)
    original_is_file = Path.is_file
    original_stat = Path.stat

    def _present(self: Path) -> bool:
        if self == archive:
            return True
        return original_is_file(self)

    def _deny_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == archive:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", _present)
    monkeypatch.setattr(Path, "stat", _deny_stat)

    assert list_archives(tmp_path, 24, now=_MOMENT) == ()


def test_find_archive_returns_fresh_file(tmp_path: Path) -> None:
    archive = _touch_archive(tmp_path)

    found = find_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT)

    assert found == archive


def test_find_archive_returns_none_for_missing_or_expired_file(tmp_path: Path) -> None:
    assert find_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None

    archive = _touch_archive(tmp_path, created_at=_MOMENT - timedelta(hours=25))

    assert find_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None
    assert archive.is_file()


def test_find_archive_rejects_invalid_language(tmp_path: Path) -> None:
    _touch_archive(tmp_path)

    assert find_archive(tmp_path, "../ru", REQUEST_ID, 24, now=_MOMENT) is None


def test_find_archive_returns_none_when_stat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _touch_archive(tmp_path)
    original_stat = Path.stat

    def _deny(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == archive:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(Path, "stat", _deny)

    assert find_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None


def test_count_archive_files_counts_mp4_entries(tmp_path: Path) -> None:
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / "a.mp4", b"first")
    touch_mp4(tmp_path / CONTENT_DIR / "ru" / "b.mp4", b"second")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Первое название")
    add_title(tmp_path / "db" / "db.sqlite3", "b", "Второе название")
    archive = deliver(tmp_path, "ru", 10, REQUEST_ID, retention_hours=24)
    assert archive is not None

    assert count_archive_files(archive) == 2


def test_list_archives_includes_remote_sidecar_without_zip(tmp_path: Path) -> None:
    _sidecar(
        tmp_path,
        status="ready",
        size=303,
        files=2,
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
    )

    archives = list_archives(tmp_path, 24, now=_MOMENT)

    assert len(archives) == 1
    assert archives[0].storage == "remote"
    assert (
        archives[0].url
        == "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    )
    assert archives[0].size == 303
    assert archives[0].files == 2
    assert archives[0].created_at == _MOMENT
    assert archives[0].expires_at == _MOMENT + timedelta(hours=24)


def test_list_archives_includes_ready_local_sidecar_with_zip(tmp_path: Path) -> None:
    _touch_archive(tmp_path, data=b"local")
    _sidecar(tmp_path, status="ready_local", size=5, files=1)

    archives = list_archives(tmp_path, 24, now=_MOMENT)

    assert len(archives) == 1
    assert archives[0].storage == "local"
    assert archives[0].url is None
    assert archives[0].files == 1


def test_list_archives_skips_ready_local_without_zip(tmp_path: Path) -> None:
    _sidecar(tmp_path, status="ready_local", size=5, files=1)

    assert list_archives(tmp_path, 24, now=_MOMENT) == ()


def test_list_archives_prefers_remote_sidecar_over_local_zip(tmp_path: Path) -> None:
    _touch_archive(tmp_path, data=b"still-here")
    _sidecar(
        tmp_path,
        status="ready",
        size=10,
        files=1,
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
    )

    archives = list_archives(tmp_path, 24, now=_MOMENT)

    assert len(archives) == 1
    assert archives[0].storage == "remote"


def test_resolve_archive_returns_remote_url_without_zip(tmp_path: Path) -> None:
    _sidecar(
        tmp_path,
        status="ready",
        size=10,
        files=1,
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
    )

    location = resolve_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT)

    assert location is not None
    assert location.storage == "remote"
    assert (
        location.url
        == "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    )
    assert location.path is None
    assert find_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None


def test_resolve_archive_ready_local_without_zip_is_missing(tmp_path: Path) -> None:
    _sidecar(tmp_path, status="ready_local", size=10, files=1)

    assert resolve_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None


def test_resolve_archive_removes_expired_sidecar(tmp_path: Path) -> None:
    _sidecar(
        tmp_path,
        status="ready",
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
        created_at=_MOMENT - timedelta(hours=25),
        expires_at=_MOMENT - timedelta(hours=1),
    )

    assert resolve_archive(tmp_path, "ru", REQUEST_ID, 24, now=_MOMENT) is None
    assert not (tmp_path / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").exists()


def test_cleanup_removes_expired_sidecar_and_local_zip(tmp_path: Path) -> None:
    archive = _touch_archive(
        tmp_path,
        data=b"local",
        created_at=_MOMENT - timedelta(hours=25),
    )
    _sidecar(
        tmp_path,
        status="ready_local",
        created_at=_MOMENT - timedelta(hours=25),
        expires_at=_MOMENT - timedelta(hours=1),
    )

    cleanup_expired_archives(tmp_path, 24, now=_MOMENT)

    assert not archive.exists()
    assert not (tmp_path / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").exists()


def test_cleanup_keeps_fresh_sidecar(tmp_path: Path) -> None:
    _sidecar(
        tmp_path,
        status="ready",
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
    )

    cleanup_expired_archives(tmp_path, 24, now=_MOMENT)

    assert (tmp_path / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").is_file()
