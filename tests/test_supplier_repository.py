from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.video import (
    STATUS_APPROVED,
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_FOUND,
    STATUS_REJECTED,
    DownloadedVideo,
    Video,
    VideoMetadata,
)
from content_factory.services.supplier import repository
from content_factory.services.supplier.repository import Repository


def _video(
    video_id: str,
    author: str = "alice",
    query: str = "q",
    language: str = "ru",
    **overrides: object,
) -> Video:
    base = Video(
        video_id=video_id,
        url=f"https://www.tiktok.com/@{author}/video/{video_id}",
        author=author,
        query=query,
        language=language,
    )
    return replace(base, **overrides)


def _metadata(video_id: str, **overrides: object) -> VideoMetadata:
    base = VideoMetadata(
        video_id=video_id,
        url=f"https://www.tiktok.com/@alice/video/{video_id}",
        author_id="alice",
        author_name="Alice",
        description="description",
        views=100,
        followers=50,
        duration=12,
        is_aigc=False,
        aigc_description="",
        width=1080,
        height=1920,
        create_time=1_700_000_000,
    )
    return replace(base, **overrides)


def _repo(tmp_path: Path) -> Repository:
    repo = Repository(tmp_path / "supplier.db")
    repo.init_schema()
    return repo


def _statuses(repo: Repository) -> dict[str, int]:
    with sqlite3.connect(repo.db_path) as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM videos GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}


def test_init_schema_is_idempotent_and_creates_parent(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "supplier.db"
    repo = Repository(db_path)
    repo.init_schema()
    repo.init_schema()

    assert repo.db_path == db_path
    assert _statuses(repo) == {}


def test_init_schema_migrates_language_column(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                author TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'found',
                discovered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    repo = Repository(db_path)
    repo.init_schema()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
    assert "language" in columns

    video = _video("1", language="ru")
    assert repo.add_found(video) is True
    assert repo.list_videos(language="ru") == [video]


def test_add_found_is_idempotent_and_preserves_status(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    video = _video("1")

    assert repo.add_found(video) is True
    assert repo.add_found(video) is False
    assert repo.list_videos() == [video]

    repo.save_decision(video, _metadata("1"), FilterDecision(True))
    assert repo.add_found(video) is False
    assert [item.video_id for item in repo.list_videos(STATUS_APPROVED)] == ["1"]


def test_save_decision_persists_metadata_and_reason(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    approved = _video("1")
    rejected = _video("2")
    repo.add_found(approved)
    repo.add_found(rejected)

    repo.save_decision(approved, _metadata("1"), FilterDecision(True))
    repo.save_decision(
        rejected,
        _metadata("2", views=5000),
        FilterDecision(False, "too_many_views"),
    )

    with sqlite3.connect(repo.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM videos WHERE video_id = '1'").fetchone()
    assert row is not None
    assert row["views"] == 100
    assert row["followers"] == 50
    assert row["duration"] == 12
    assert row["width"] == 1080
    assert row["height"] == 1920
    assert row["create_time"] == 1_700_000_000
    assert row["description"] == "description"
    assert row["is_aigc"] == 0

    assert [item.video_id for item in repo.list_videos(STATUS_APPROVED)] == ["1"]
    assert [item.video_id for item in repo.list_videos(STATUS_REJECTED)] == ["2"]


def test_save_decision_marks_aigc_description(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    video = _video("1")
    repo.add_found(video)

    repo.save_decision(
        video,
        _metadata("1", is_aigc=False, aigc_description="AI generated"),
        FilterDecision(False, "aigc"),
    )

    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("SELECT is_aigc FROM videos WHERE video_id = '1'").fetchone()[0] == 1


def test_save_decision_missing_video(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SupplierError, match="видео не найдено"):
        repo.save_decision(_video("missing"), _metadata("missing"), FilterDecision(True))


def test_save_rejection_and_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rejected = _video("1")
    failed = _video("2")
    repo.add_found(rejected)
    repo.add_found(failed)

    repo.save_rejection(rejected, "author_used")
    repo.save_failure(failed, "metadata_error")

    assert _statuses(repo) == {STATUS_REJECTED: 1, STATUS_FAILED: 1}
    with sqlite3.connect(repo.db_path) as conn:
        row = conn.execute("SELECT reject_reason FROM videos WHERE video_id = '1'").fetchone()
    assert row is not None
    assert row[0] == "author_used"


def test_status_helpers_missing_video(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SupplierError, match="видео не найдено"):
        repo.save_rejection(_video("missing"), "author_used")
    with pytest.raises(SupplierError, match="видео не найдено"):
        repo.save_failure(_video("missing"), "metadata_error")


def test_list_videos_filters_limits_and_validates_status(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _video("1", discovered_at="2026-01-01T00:00:00+00:00")
    second = _video("2", discovered_at="2026-01-02T00:00:00+00:00")
    repo.add_found(first)
    repo.add_found(second)
    repo.save_decision(first, _metadata("1"), FilterDecision(True))

    assert [item.video_id for item in repo.list_videos()] == ["1", "2"]
    assert [item.video_id for item in repo.list_videos(STATUS_FOUND)] == ["2"]
    assert [item.video_id for item in repo.list_videos(limit=1)] == ["1"]

    with pytest.raises(ValueError, match="неизвестный статус"):
        repo.list_videos("unknown")


def test_list_videos_isolates_languages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    russian = _video("1", language="ru")
    english = _video("2", language="en", author="bob")
    repo.add_found(russian)
    repo.add_found(english)

    assert [item.video_id for item in repo.list_videos(STATUS_FOUND, language="ru")] == ["1"]
    assert [item.video_id for item in repo.list_videos(STATUS_FOUND, language="en")] == ["2"]
    assert len(repo.list_videos(STATUS_FOUND)) == 2


def test_decided_ids_and_used_authors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    found = _video("1", author="alice")
    approved = _video("2", author="bob")
    downloaded = _video("3", author="carol")
    repo.add_found(found)
    repo.add_found(approved)
    repo.add_found(downloaded)
    repo.save_decision(approved, _metadata("2"), FilterDecision(True))
    repo.save_download(
        DownloadedVideo(video=downloaded, path="videos/3.mp4", sha256="abc", size=10)
    )

    assert repo.decided_video_ids() == {"2", "3"}
    assert repo.used_authors() == {"carol"}


def test_save_download_rejects_siblings_from_same_author(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _video("1", author="alice")
    second = _video("2", author="alice")
    third = _video("3", author="alice")
    other = _video("4", author="bob")
    for video in (first, second, third, other):
        repo.add_found(video)
    repo.save_decision(second, _metadata("2"), FilterDecision(True))
    repo.save_decision(other, _metadata("4"), FilterDecision(True))

    repo.save_download(DownloadedVideo(video=first, path="videos/1.mp4", sha256="abc", size=10))

    assert _statuses(repo) == {
        STATUS_DOWNLOADED: 1,
        STATUS_REJECTED: 2,
        STATUS_APPROVED: 1,
    }
    assert repo.has_sha256("abc") is True
    assert repo.has_sha256("def") is False
    assert repo.used_authors() == {"alice"}


def test_save_download_rejects_duplicate_sha256(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _video("1", author="alice")
    second = _video("2", author="bob")
    repo.add_found(first)
    repo.add_found(second)
    repo.save_download(DownloadedVideo(video=first, path="videos/1.mp4", sha256="abc", size=10))

    with pytest.raises(SupplierError, match="не удалось записать скачивание"):
        repo.save_download(
            DownloadedVideo(video=second, path="videos/2.mp4", sha256="abc", size=10)
        )

    assert [item.video_id for item in repo.list_videos(STATUS_FOUND)] == ["2"]


def test_save_download_rejects_second_downloaded_author(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _video("1", author="alice")
    second = _video("2", author="alice")
    repo.add_found(first)
    repo.add_found(second)
    repo.save_download(DownloadedVideo(video=first, path="videos/1.mp4", sha256="abc", size=10))

    with pytest.raises(SupplierError, match="не удалось записать скачивание"):
        repo.save_download(
            DownloadedVideo(video=second, path="videos/2.mp4", sha256="def", size=10)
        )

    assert repo.used_authors() == {"alice"}


def test_save_download_missing_video(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SupplierError, match="не удалось записать скачивание"):
        repo.save_download(
            DownloadedVideo(
                video=_video("missing"),
                path="videos/missing.mp4",
                sha256="abc",
                size=10,
            )
        )


def test_downloaded_count_is_scoped_by_language(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    russian = _video("1", author="alice", language="ru")
    english = _video("2", author="bob", language="en")
    for video in (russian, english):
        repo.add_found(video)
    repo.save_download(DownloadedVideo(video=russian, path="ru/1.mp4", sha256="abc", size=10))
    repo.save_download(DownloadedVideo(video=english, path="en/2.mp4", sha256="def", size=10))

    assert repo.downloaded_count("ru") == 1
    assert repo.downloaded_count("en") == 1
    assert repo.downloaded_count("de") == 0


def test_repository_is_thread_safe(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    videos = [_video(str(index), author=f"author-{index}") for index in range(40)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(repo.add_found, videos))

    assert all(results)
    assert len(repo.list_videos()) == 40


def test_database_errors_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository(tmp_path / "broken.db")
    video = _video("1")
    item = DownloadedVideo(video=video, path="videos/1.mp4", sha256="abc", size=10)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "content_factory.services.supplier.repository.sqlite3.connect",
        fail,
    )

    with pytest.raises(SupplierError, match="схему поставщика"):
        repo.init_schema()
    with pytest.raises(SupplierError, match="сохранить видео"):
        repo.add_found(video)
    with pytest.raises(SupplierError, match="сохранить решение"):
        repo.save_decision(video, _metadata("1"), FilterDecision(True))
    with pytest.raises(SupplierError, match="обновить видео"):
        repo.save_rejection(video, "author_used")
    with pytest.raises(SupplierError, match="записать скачивание"):
        repo.save_download(item)
    with pytest.raises(SupplierError, match="проверить SHA-256"):
        repo.has_sha256("abc")


def test_database_read_errors_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "content_factory.services.supplier.repository.sqlite3.connect",
        fail,
    )

    with pytest.raises(SupplierError, match="прочитать решения"):
        repo.decided_video_ids()
    with pytest.raises(SupplierError, match="прочитать авторов"):
        repo.used_authors()
    with pytest.raises(SupplierError, match="посчитать скачанные видео"):
        repo.downloaded_count("ru")
    with pytest.raises(SupplierError, match="прочитать видео"):
        repo.list_videos(STATUS_FOUND)


def test_database_directory_errors_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(Path, "mkdir", fail)

    with pytest.raises(SupplierError, match="не удалось создать каталог"):
        Repository(tmp_path / "nested" / "supplier.db").init_schema()


def test_language_migration_tolerates_duplicate_column(tmp_path: Path) -> None:
    db_path = tmp_path / "duplicate.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE videos (video_id TEXT PRIMARY KEY, language TEXT NOT NULL DEFAULT '')"
        )

        repository._add_language_column(conn)

    assert db_path.is_file()


def test_language_migration_reraises_other_errors() -> None:
    with (
        sqlite3.connect(":memory:") as conn,
        pytest.raises(sqlite3.OperationalError, match="no such table"),
    ):
        repository._add_language_column(conn)
