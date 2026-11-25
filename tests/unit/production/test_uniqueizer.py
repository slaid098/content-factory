"""Uniqueizer import and queue: order, buffer, failures and isolation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from content_factory.config.settings import get_settings
from content_factory.constants import BUFFER_DIR, DB_DIR, DB_FILENAME, SOURCES_DIR, UNIQUE_DIR
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.db.sources import (
    get_cursor,
    get_source,
    mark_missing,
    mark_used,
    upsert_source,
)
from content_factory.schemas.generator.uniqueizer import SourceCandidate
from content_factory.services.uniqueizer import importer, main
from content_factory.services.uniqueizer.importer import import_profile, import_sources
from content_factory.services.uniqueizer.layout import buffer_directory, copy_name
from content_factory.services.uniqueizer.queue import UniqueizerQueue

_LANGUAGE = "ru"
_PROFILE = "dating/profile_1"
_OTHER_PROFILE = "dating/profile_2"
_NOW = "2026-05-01T12:00:00+00:00"


class _FakeEngine:
    """Engine double: writes fixed bytes and fails on a chosen call."""

    def __init__(
        self,
        *,
        write: bytes | None = b"ready",
        fail: bool = False,
        fail_on_call: int | None = None,
    ) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self._write = write
        self._fail = fail
        self._fail_on_call = fail_on_call

    def uniqueize(self, source: Path, target: Path) -> None:
        call_index = len(self.calls)
        self.calls.append((source, target))
        if self._write is not None:
            target.write_bytes(self._write)
        if self._fail or call_index == self._fail_on_call:
            raise RuntimeError("engine broke")


class _DirectoryEngine:
    """Engine double that creates a directory instead of a video file."""

    def uniqueize(self, source: Path, target: Path) -> None:
        target.mkdir()


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep these unit tests on the local SQLite backend."""
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _db_path(data_dir: Path) -> Path:
    return data_dir / DB_DIR / DB_FILENAME


def _source_path(data_dir: Path, name: str, *, profile_id: str = _PROFILE) -> Path:
    return data_dir / SOURCES_DIR / _LANGUAGE / profile_id.replace("/", "_") / name


def _buffer(data_dir: Path, profile_id: str = _PROFILE) -> Path:
    return data_dir / UNIQUE_DIR / _LANGUAGE / profile_id.replace("/", "_") / BUFFER_DIR


def _seed_sources(data_dir: Path, count: int, *, profile_id: str = _PROFILE) -> list[Path]:
    """Create *count* source files and their queue rows."""
    connection = connect(_db_path(data_dir))
    paths: list[Path] = []
    try:
        for index in range(1, count + 1):
            name = f"clip_{index:02d}.mp4"
            path = _source_path(data_dir, name, profile_id=profile_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"source-{profile_id}-{name}".encode()
            path.write_bytes(content)
            upsert_source(
                connection,
                SourceCandidate(
                    path=path.relative_to(data_dir).as_posix(),
                    language=_LANGUAGE,
                    profile_id=profile_id,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=len(content),
                    duration=5.0,
                ),
                _NOW,
            )
            paths.append(path)
        connection.commit()
    finally:
        connection.close()
    return paths


def _stored(data_dir: Path, path: Path) -> Any:
    connection = connect(_db_path(data_dir))
    try:
        return get_source(connection, path.relative_to(data_dir).as_posix())
    finally:
        connection.close()


def _stored_cursor(data_dir: Path, profile_id: str = _PROFILE) -> str | None:
    connection = connect(_db_path(data_dir))
    try:
        return get_cursor(connection, _LANGUAGE, profile_id)
    finally:
        connection.close()


def _ready(data_dir: Path, profile_id: str = _PROFILE) -> list[str]:
    directory = _buffer(data_dir, profile_id)
    if not directory.is_dir():
        return []
    return sorted(entry.name for entry in directory.iterdir() if entry.is_file())


def _consume(data_dir: Path, profile_id: str = _PROFILE) -> None:
    for path in _buffer(data_dir, profile_id).iterdir():
        path.unlink()


def _probe(duration: float = 8.5, *, skip: frozenset[str] = frozenset()) -> Any:
    def fake_probe(path: Path) -> object | None:
        if path.name in skip:
            return None
        return SimpleNamespace(duration=duration)

    return fake_probe


# Importer


def test_import_adds_sources_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    path = _source_path(tmp_path, "a.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a")
    _source_path(tmp_path, "b.mp4").write_bytes(b"b")

    first = import_profile(tmp_path, _LANGUAGE, _PROFILE)
    second = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    assert (first.added, first.unchanged) == (2, 0)
    assert (second.added, second.unchanged) == (0, 2)
    stored = _stored(tmp_path, path)
    assert stored is not None
    assert stored.duration == 8.5


def test_import_skips_a_file_without_a_video_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(importer, "probe", _probe(skip=frozenset({"broken.mp4"})))
    _source_path(tmp_path, "good.mp4").parent.mkdir(parents=True)
    _source_path(tmp_path, "good.mp4").write_bytes(b"good")
    _source_path(tmp_path, "broken.mp4").write_bytes(b"broken")

    result = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    assert (result.added, result.skipped) == (1, 1)
    assert [record for record in log_records if "пропущен" in record["message"]]


def test_import_deduplicates_by_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    _source_path(tmp_path, "a.mp4").parent.mkdir(parents=True)
    _source_path(tmp_path, "a.mp4").write_bytes(b"same")
    _source_path(tmp_path, "b.mp4").write_bytes(b"same")

    result = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    assert (result.added, result.duplicates) == (1, 1)
    assert _stored(tmp_path, _source_path(tmp_path, "b.mp4")) is None


def test_import_updates_changed_content_and_resets_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    path = _source_path(tmp_path, "clip.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"first")
    import_profile(tmp_path, _LANGUAGE, _PROFILE)
    connection = connect(_db_path(tmp_path))
    try:
        relative = path.relative_to(tmp_path).as_posix()
        mark_used(connection, relative, _NOW)
        mark_used(connection, relative, _NOW)
        connection.commit()
    finally:
        connection.close()

    path.write_bytes(b"second")
    result = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    item = _stored(tmp_path, path)
    assert result.updated == 1
    assert item is not None
    assert item.use_count == 0
    assert item.used_at is None
    assert item.sha256 == hashlib.sha256(b"second").hexdigest()


def test_import_marks_missing_and_clears_it_on_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    gone = _source_path(tmp_path, "gone.mp4")
    gone.parent.mkdir(parents=True)
    gone.write_bytes(b"gone")
    _source_path(tmp_path, "stay.mp4").write_bytes(b"stay")
    import_profile(tmp_path, _LANGUAGE, _PROFILE)

    gone.unlink()
    missing = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    item = _stored(tmp_path, gone)
    assert missing.missing == 1
    assert item is not None
    assert item.missing_at is not None

    gone.write_bytes(b"gone")
    returned = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    item = _stored(tmp_path, gone)
    assert returned.returned == 1
    assert item is not None
    assert item.missing_at is None


def test_import_isolates_languages_and_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    for profile_id in (_PROFILE, _OTHER_PROFILE):
        path = _source_path(tmp_path, "same.mp4", profile_id=profile_id)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"same")

    first = import_profile(tmp_path, _LANGUAGE, _PROFILE)
    second = import_profile(tmp_path, _LANGUAGE, _OTHER_PROFILE)

    assert (first.added, second.added) == (1, 1)
    assert _stored(tmp_path, _source_path(tmp_path, "same.mp4")) is not None
    assert (
        _stored(tmp_path, _source_path(tmp_path, "same.mp4", profile_id=_OTHER_PROFILE)) is not None
    )


def test_import_missing_folder_returns_empty_counters(tmp_path: Path) -> None:
    result = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    assert (result.added, result.updated, result.missing, result.skipped) == (0, 0, 0, 0)


def test_import_skips_a_file_that_cannot_be_hashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())

    def broken_hash(path: Path) -> str:
        raise OSError("cannot read")

    monkeypatch.setattr(importer, "_sha256", broken_hash)
    path = _source_path(tmp_path, "clip.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"clip")

    result = import_profile(tmp_path, _LANGUAGE, _PROFILE)

    assert (result.added, result.skipped) == (0, 1)


def test_import_sources_without_the_root_returns_nothing(tmp_path: Path) -> None:
    assert import_sources(tmp_path) == ()


def test_import_sources_skips_an_invalid_language_folder(tmp_path: Path) -> None:
    (tmp_path / SOURCES_DIR / "russian" / "dating_profile_1").mkdir(parents=True)

    assert import_sources(tmp_path) == ()


def test_import_sources_skips_an_unknown_profile_slug(tmp_path: Path) -> None:
    path = _source_path(tmp_path, "clip.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"clip")

    assert import_sources(tmp_path) == ()


def test_importer_cli_reports_an_empty_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main.main(["--data-dir", str(tmp_path)]) == 0

    assert "источники не найдены" in capsys.readouterr().out


def test_importer_cli_returns_one_on_a_database_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken_import(data_dir: Path) -> tuple[object, ...]:
        raise DatabaseError("database is down")

    monkeypatch.setattr(main, "import_sources", broken_import)

    assert main.main(["--data-dir", str(tmp_path)]) == 1

    assert "database is down" in capsys.readouterr().err


def test_import_sources_resolves_profile_slugs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    profile_dir = tmp_path / "profiles" / "dating" / "profile_1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text("width: 1080\nheight: 1920\n", encoding="utf-8")
    path = _source_path(tmp_path, "clip.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"clip")

    results = import_sources(tmp_path)

    assert len(results) == 1
    assert results[0].profile_id == _PROFILE
    assert results[0].added == 1


def test_importer_cli_prints_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(importer, "probe", _probe())
    profile_dir = tmp_path / "profiles" / "dating" / "profile_1"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text("width: 1080\nheight: 1920\n", encoding="utf-8")
    path = _source_path(tmp_path, "clip.mp4")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"clip")

    assert main.main(["--data-dir", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "ru dating/profile_1" in output
    assert "добавлено 1" in output


# Queue


def test_refill_creates_five_copies_at_a_time(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 50)
    engine = _FakeEngine()
    queue = UniqueizerQueue()

    first = queue.refill(_LANGUAGE, _PROFILE, 5, tmp_path, engine)

    assert (first.created, first.ready, first.exhausted) == (5, 5, False)
    assert _ready(tmp_path) == [f"clip_{index:02d}_001.mp4" for index in range(1, 6)]
    assert len(engine.calls) == 5

    _consume(tmp_path)
    second = queue.refill(_LANGUAGE, _PROFILE, 5, tmp_path, engine)

    assert (second.created, second.ready, second.exhausted) == (5, 5, False)
    assert _ready(tmp_path) == [f"clip_{index:02d}_001.mp4" for index in range(6, 11)]
    assert _stored_cursor(tmp_path) == "sources/ru/dating_profile_1/clip_10.mp4"
    assert all(
        _stored(tmp_path, _source_path(tmp_path, f"clip_{index:02d}.mp4")).use_count == 1
        for index in range(1, 11)
    )


def test_refill_does_not_create_more_than_target(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 5)
    engine = _FakeEngine()
    queue = UniqueizerQueue()

    result = queue.refill(_LANGUAGE, _PROFILE, 3, tmp_path, engine)
    again = queue.refill(_LANGUAGE, _PROFILE, 3, tmp_path, engine)

    assert (result.created, result.ready) == (3, 3)
    assert (again.created, again.ready) == (0, 3)
    assert len(engine.calls) == 3


def test_refill_rejects_a_non_positive_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target"):
        UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 0, tmp_path, _FakeEngine())


def test_refill_is_exhausted_after_the_last_source(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 2)
    engine = _FakeEngine()
    queue = UniqueizerQueue()

    first = queue.refill(_LANGUAGE, _PROFILE, 5, tmp_path, engine)
    second = queue.refill(_LANGUAGE, _PROFILE, 5, tmp_path, engine)

    assert (first.created, first.ready, first.exhausted) == (2, 2, True)
    assert (second.created, second.ready, second.exhausted) == (0, 2, True)
    assert len(engine.calls) == 2


def test_refill_without_sources_is_exhausted(tmp_path: Path) -> None:
    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 5, tmp_path, _FakeEngine())

    assert (result.created, result.ready, result.exhausted, result.failures) == (0, 0, True, ())


def test_refill_skips_a_missing_source(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 3)
    connection = connect(_db_path(tmp_path))
    try:
        mark_missing(
            connection,
            ["sources/ru/dating_profile_1/clip_02.mp4"],
            _NOW,
        )
        connection.commit()
    finally:
        connection.close()
    engine = _FakeEngine()

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 2, tmp_path, engine)

    assert result.created == 2
    assert [source.name for source, _ in engine.calls] == ["clip_01.mp4", "clip_03.mp4"]
    assert _stored_cursor(tmp_path) == "sources/ru/dating_profile_1/clip_03.mp4"


def test_engine_failure_removes_the_partial_copy_and_keeps_the_cursor(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 2)
    failing = _FakeEngine(write=b"partial", fail=True)
    queue = UniqueizerQueue()

    result = queue.refill(_LANGUAGE, _PROFILE, 2, tmp_path, failing)

    assert result.created == 0
    assert result.ready == 0
    assert len(result.failures) == 1
    assert result.failures[0].source == "sources/ru/dating_profile_1/clip_01.mp4"
    assert _ready(tmp_path) == []
    assert _stored_cursor(tmp_path) is None
    assert _stored(tmp_path, _source_path(tmp_path, "clip_01.mp4")).use_count == 0

    retry = queue.refill(_LANGUAGE, _PROFILE, 2, tmp_path, _FakeEngine())
    assert retry.created == 2


def test_engine_without_a_target_is_a_failure(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1)

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, _FakeEngine(write=None))

    assert result.created == 0
    assert len(result.failures) == 1
    assert "не создал файл" in result.failures[0].reason
    assert _stored_cursor(tmp_path) is None


def test_engine_empty_target_is_a_failure(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1)

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, _FakeEngine(write=b""))

    assert result.created == 0
    assert len(result.failures) == 1
    assert "пустой файл" in result.failures[0].reason
    assert _ready(tmp_path) == []
    assert _stored_cursor(tmp_path) is None


def test_engine_directory_target_is_a_failure(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1)

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, _DirectoryEngine())

    assert result.created == 0
    assert len(result.failures) == 1
    assert "не создал файл" in result.failures[0].reason
    assert _ready(tmp_path) == []
    assert _stored_cursor(tmp_path) is None


def test_mid_pass_failure_keeps_previous_copies_and_cursor(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 3)
    engine = _FakeEngine(fail_on_call=1)
    queue = UniqueizerQueue()

    result = queue.refill(_LANGUAGE, _PROFILE, 3, tmp_path, engine)

    assert (result.created, result.ready) == (1, 1)
    assert len(result.failures) == 1
    assert result.failures[0].source == "sources/ru/dating_profile_1/clip_02.mp4"
    assert _ready(tmp_path) == ["clip_01_001.mp4"]
    assert _stored_cursor(tmp_path) == "sources/ru/dating_profile_1/clip_01.mp4"
    assert _stored(tmp_path, _source_path(tmp_path, "clip_01.mp4")).use_count == 1
    assert _stored(tmp_path, _source_path(tmp_path, "clip_02.mp4")).use_count == 0

    retry = queue.refill(_LANGUAGE, _PROFILE, 3, tmp_path, _FakeEngine())
    assert (retry.created, retry.ready) == (2, 3)
    assert _stored_cursor(tmp_path) == "sources/ru/dating_profile_1/clip_03.mp4"


def test_refill_marks_a_source_that_vanished_after_seeding(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 2)
    _source_path(tmp_path, "clip_01.mp4").unlink()
    engine = _FakeEngine()

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 2, tmp_path, engine)

    assert (result.created, result.ready, result.exhausted) == (1, 1, True)
    assert [source.name for source, _ in engine.calls] == ["clip_02.mp4"]
    assert _stored(tmp_path, _source_path(tmp_path, "clip_01.mp4")).missing_at is not None
    assert _stored_cursor(tmp_path) == "sources/ru/dating_profile_1/clip_02.mp4"


def test_refill_rejects_unsafe_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="язык"):
        UniqueizerQueue().refill("russian", _PROFILE, 1, tmp_path, _FakeEngine())
    with pytest.raises(ValueError, match="профил"):
        UniqueizerQueue().refill(_LANGUAGE, "../escape", 1, tmp_path, _FakeEngine())


def test_copy_name_and_scope_validation(tmp_path: Path) -> None:
    assert copy_name("clip.mp4", 1) == "clip_001.mp4"
    assert copy_name("clip.mp4", 42) == "clip_042.mp4"
    with pytest.raises(ValueError, match="copy_index"):
        copy_name("clip.mp4", 0)
    with pytest.raises(ValueError, match="язык"):
        buffer_directory(tmp_path, "russian", _PROFILE)
    with pytest.raises(ValueError, match="профил"):
        buffer_directory(tmp_path, _LANGUAGE, "../escape")


def test_existing_target_is_not_overwritten(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1)
    buffer = _buffer(tmp_path)
    buffer.mkdir(parents=True)
    existing = buffer / "clip_01_001.mp4"
    existing.write_bytes(b"old")
    engine = _FakeEngine(write=b"new")

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 2, tmp_path, engine)

    assert result.created == 0
    assert len(result.failures) == 1
    assert "существует" in result.failures[0].reason
    assert engine.calls == []
    assert existing.read_bytes() == b"old"
    assert _stored_cursor(tmp_path) is None


def test_copy_index_follows_the_use_count(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1)
    connection = connect(_db_path(tmp_path))
    try:
        mark_used(connection, "sources/ru/dating_profile_1/clip_01.mp4", _NOW)
        mark_used(connection, "sources/ru/dating_profile_1/clip_01.mp4", _NOW)
        connection.commit()
    finally:
        connection.close()

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, _FakeEngine())

    assert result.created == 1
    assert _ready(tmp_path) == ["clip_01_003.mp4"]


def test_queue_is_isolated_by_profile(tmp_path: Path) -> None:
    _seed_sources(tmp_path, 1, profile_id=_PROFILE)
    _seed_sources(tmp_path, 1, profile_id=_OTHER_PROFILE)
    engine = _FakeEngine()

    result = UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, engine)

    assert result.created == 1
    assert _ready(tmp_path) == ["clip_01_001.mp4"]
    assert _ready(tmp_path, _OTHER_PROFILE) == []
    assert _stored_cursor(tmp_path) is not None
    assert _stored_cursor(tmp_path, _OTHER_PROFILE) is None


def test_queue_copies_the_engine_output_without_touching_the_source(tmp_path: Path) -> None:
    source = _seed_sources(tmp_path, 1)[0]
    original = source.read_bytes()

    UniqueizerQueue().refill(_LANGUAGE, _PROFILE, 1, tmp_path, _FakeEngine(write=b"engine"))

    assert (_buffer(tmp_path) / "clip_01_001.mp4").read_bytes() == b"engine"
    assert source.read_bytes() == original


def test_capsule_does_not_call_overlay_music_or_qa() -> None:
    package = Path(importer.__file__).parent
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert "overlay" not in source, path.name
        assert "music" not in source, path.name
        assert "text" not in source, path.name
