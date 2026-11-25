"""Source import: layout scan, hashes, duplicates, missing flags and CLI."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.config.settings import get_settings
from content_factory.constants import SOURCES_DIR
from content_factory.db.connection import connect
from content_factory.db.sources import mark_used
from content_factory.exceptions.generator import GeneratorError
from content_factory.services.sources import main as sources_main
from content_factory.services.sources import service as sources_service
from content_factory.services.sources.service import import_sources

_PROFILE = "dating/profile_2"
_KEY = f"ru/{_PROFILE}"


@pytest.fixture(autouse=True)
def _sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the import on an isolated SQLite database."""
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fake_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read files locally instead of requiring ffprobe."""

    def probe(path: Path) -> SimpleNamespace:
        size = path.stat().st_size
        return SimpleNamespace(has_video=True, size=size, duration=4.0)

    monkeypatch.setattr(sources_service, "probe", probe)


def _source(data_dir: Path, name: str = "a.mp4", content: bytes = b"video-a") -> Path:
    path = data_dir / SOURCES_DIR / "ru" / "dating_profile_2" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _db(data_dir: Path):
    return connect(data_dir / "db" / "db.sqlite3")


def _rows(data_dir: Path) -> list[tuple[object, ...]]:
    connection = _db(data_dir)
    try:
        rows = connection.execute(
            "SELECT path, sha256, used_at, use_count, missing_at FROM source_items ORDER BY id"
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        connection.close()


def test_import_adds_recursive_files_with_relative_paths(
    tmp_path: Path,
) -> None:
    make_profile(tmp_path / "profiles")
    _source(tmp_path, "a.mp4", b"first")
    _source(tmp_path, "nested/b.mp4", b"second")

    stats = import_sources(tmp_path)

    profile = stats.profiles[_KEY]
    assert (profile.added, profile.updated, profile.duplicates) == (2, 0, 0)
    assert stats.skipped == 0
    rows = _rows(tmp_path)
    assert [row[0] for row in rows] == [
        "sources/ru/dating_profile_2/a.mp4",
        "sources/ru/dating_profile_2/nested/b.mp4",
    ]
    assert all(row[2] is None and row[3] == 0 and row[4] is None for row in rows)


def test_repeated_import_is_idempotent(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    _source(tmp_path)

    first = import_sources(tmp_path)
    second = import_sources(tmp_path)

    assert first.profiles[_KEY].added == 1
    assert second.profiles[_KEY].added == 0
    assert second.profiles[_KEY].unchanged == 1
    assert len(_rows(tmp_path)) == 1


def test_duplicate_hash_is_not_added_again(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    _source(tmp_path, "a.mp4", b"same")
    _source(tmp_path, "b.mp4", b"same")

    stats = import_sources(tmp_path)

    assert stats.profiles[_KEY].added == 1
    assert stats.profiles[_KEY].duplicates == 1
    assert len(_rows(tmp_path)) == 1


def test_changed_hash_at_the_same_path_resets_usage(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    path = _source(tmp_path, content=b"old")
    import_sources(tmp_path)
    connection = _db(tmp_path)
    try:
        row = connection.execute("SELECT id FROM source_items").fetchone()
        assert row is not None
        mark_used(connection, int(row[0]), "2026-05-01T00:00:00+00:00")
        connection.commit()
    finally:
        connection.close()
    path.write_bytes(b"new")

    stats = import_sources(tmp_path)

    assert stats.profiles[_KEY].updated == 1
    row = _rows(tmp_path)[0]
    assert row[2] is None
    assert row[3] == 0
    assert row[1] != hashlib.sha256(b"old").hexdigest()


def test_missing_file_is_marked_and_restored(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    first = _source(tmp_path, "a.mp4", b"first")
    second = _source(tmp_path, "b.mp4", b"second")
    import_sources(tmp_path)
    second.unlink()

    missing = import_sources(tmp_path)

    assert missing.profiles[_KEY].missing == 1
    rows = _rows(tmp_path)
    assert rows[0][4] is None
    assert rows[1][4] is not None

    second.write_bytes(b"second")
    restored = import_sources(tmp_path)

    assert restored.profiles[_KEY].restored == 1
    assert _rows(tmp_path)[1][4] is None
    assert first.is_file()


def test_import_from_another_root_leaves_default_pool_untouched(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    make_profile(tmp_path / "profiles", name="dating/profile_3")
    _source(tmp_path, "a.mp4", b"video-a")
    alternate = tmp_path / "alt" / "ru" / "dating_profile_3" / "b.mp4"
    alternate.parent.mkdir(parents=True)
    alternate.write_bytes(b"video-b")
    import_sources(tmp_path)

    stats = import_sources(tmp_path, tmp_path / "alt")

    assert stats.profiles["ru/dating/profile_3"].added == 1
    assert all(row[4] is None for row in _rows(tmp_path))


def test_broken_file_is_skipped_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _source(tmp_path, "broken.mp4", b"broken")
    monkeypatch.setattr(sources_service, "probe", lambda path: None)

    stats = import_sources(tmp_path)

    assert stats.skipped == 1
    assert stats.warnings
    assert stats.profiles == {}
    assert _rows(tmp_path) == []


def test_foreign_language_folder_is_skipped(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    path = tmp_path / SOURCES_DIR / "en" / "dating_profile_2" / "a.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")

    stats = import_sources(tmp_path)

    assert stats.skipped == 1
    assert "не совпадает" in stats.warnings[0]
    assert _rows(tmp_path) == []


def test_unknown_profile_slug_is_skipped(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    path = tmp_path / SOURCES_DIR / "ru" / "absent_profile" / "a.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")

    stats = import_sources(tmp_path)

    assert stats.skipped == 1
    assert "не найден" in stats.warnings[0]


def test_source_root_must_stay_inside_data_dir(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    outside = tmp_path.parent / "outside-sources"
    outside.mkdir(exist_ok=True)

    with pytest.raises(GeneratorError, match="внутри"):
        import_sources(tmp_path, outside)


def test_cli_prints_counters_and_warns(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_profile(tmp_path / "profiles")
    _source(tmp_path)
    broken = _source(tmp_path, "broken.mp4", b"broken")

    def probe(path: Path) -> SimpleNamespace | None:
        if path == broken:
            return None
        return SimpleNamespace(has_video=True, size=path.stat().st_size, duration=4.0)

    monkeypatch.setattr(sources_service, "probe", probe)

    assert sources_main.main([]) == 0
    captured = capsys.readouterr()
    assert f"ru/{_PROFILE}: добавлено 1" in captured.out
    assert "пропущено файлов: 1" in captured.out
    assert "broken.mp4" in captured.err


def test_cli_fails_on_a_missing_source_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_profile(tmp_path / "profiles")

    assert sources_main.main(["--sources", str(tmp_path / "absent")]) == 1
    assert "каталог исходников не найден" in capsys.readouterr().err
