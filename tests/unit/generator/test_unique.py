"""Unique producer: source cursor, publication and fail-without-burn semantics."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import SOURCES_DIR
from content_factory.db.connection import connect
from content_factory.db.sources import upsert_import
from content_factory.exceptions.generator import GeneratorError
from content_factory.services.production import unique as unique_module
from content_factory.services.production.unique import UniqueProducer

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"
_SLUG = "dating_profile_2"
_PROFILE = "dating/profile_2"
_ANSWER = "<result><block>Первая строка\nВторая строка</block><block>Пиши мне</block></result>"


class _Writer:
    """Text model port that returns one fixed card answer."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def write(self, system_prompt: str) -> str:
        self.prompts.append(system_prompt)
        return _ANSWER


class _Checker:
    """Clef port with a fixed verdict."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.calls: list[tuple[str, Path]] = []

    def preflight(self) -> None:
        raise AssertionError("unique producer must not run the Clef preflight")

    def verify(self, item: Any, source: Path, attempt: int) -> str | None:
        self.calls.append((item.filename, source))
        return self.reason


def _recipe(qa: list[str] | None = None) -> dict[str, object]:
    return {
        "production": [
            {"producer": "unique", "type": "video", "qa": qa or ["media"]},
        ]
    }


def _profile(tmp_path: Path, qa: list[str] | None = None) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe(qa))


def _settings(tmp_path: Path, *, safety: bool = False) -> GeneratorSettings:
    return GeneratorSettings(output_dir=tmp_path / "generated", safety_enabled=safety)


def _source(
    tmp_path: Path, name: str, *, content: bytes | None = None, duration: float = 4.0
) -> int:
    path = tmp_path / SOURCES_DIR / "ru" / _SLUG / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content or name.encode())
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        upsert_import(
            connection,
            language="ru",
            profile_id=_PROFILE,
            path=path.relative_to(tmp_path).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=path.stat().st_size,
            duration=duration,
            now="2026-01-01T00:00:00+00:00",
        )
        connection.commit()
        row = connection.execute(
            "SELECT id FROM source_items WHERE path = ?",
            (path.relative_to(tmp_path).as_posix(),),
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def _producer(
    *,
    writer: _Writer | None = None,
    checker: _Checker | None = None,
) -> UniqueProducer:
    return UniqueProducer(
        writer=writer or _Writer(),
        checker=checker,  # type: ignore[arg-type]
        rng=random.Random(1),
        now=_NOW,
    )


def _fake_mix(profile: Any, card: Any, source: Path, output: Path, audio: Path | None) -> None:
    del profile, card, source, audio
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"final-video")


def _fake_qa(names: tuple[str, ...], path: Path, expected: float, expect_audio: bool = False):
    del names, path, expected, expect_audio
    return SimpleNamespace(duration=4.0)


@pytest.fixture(autouse=True)
def _quiet_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ffmpeg and ffprobe with deterministic local steps."""
    monkeypatch.setattr(unique_module, "mix", _fake_mix)
    monkeypatch.setattr(unique_module, "check_declared", _fake_qa)


def _rows(tmp_path: Path) -> list[tuple[object, ...]]:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        rows = connection.execute(
            "SELECT id, used_at, use_count, missing_at FROM source_items ORDER BY id"
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        connection.close()


def _cursor(tmp_path: Path) -> tuple[object, ...] | None:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        row = connection.execute(
            "SELECT last_source_id FROM source_cursors WHERE language = ? AND profile_id = ?",
            ("ru", _PROFILE),
        ).fetchone()
        return None if row is None else tuple(row)
    finally:
        connection.close()


def _stock(tmp_path: Path) -> Path:
    return tmp_path / "content" / "dating" / "ru"


def test_successful_items_move_the_cursor_and_publish(tmp_path: Path) -> None:
    _profile(tmp_path)
    _source(tmp_path, "a.mp4")
    _source(tmp_path, "b.mp4")
    writer = _Writer()

    produced = _producer(writer=writer).produce(
        SimpleNamespace(name=_PROFILE), 2, tmp_path, _settings(tmp_path)
    )

    assert produced.generated == 2
    assert produced.failed == 0
    assert produced.run_id == _RUN_ID
    assert len(writer.prompts) == 2
    report = json.loads(
        (tmp_path / "generated" / "ru" / _SLUG / _RUN_ID / "run.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "done"
    assert report["production"] == [{"producer": "unique", "type": "video", "qa": ["media"]}]
    assert [item["source_path"] for item in report["items"]] == [
        f"sources/ru/{_SLUG}/a.mp4",
        f"sources/ru/{_SLUG}/b.mp4",
    ]
    assert all(item["source_sha256"] for item in report["items"])
    assert [item["file"] for item in report["items"]] == [
        f"{_SLUG}_{_RUN_ID}_{seq:03d}.mp4" for seq in (1, 2)
    ]
    assert all(item["status"] == "done" for item in report["items"])
    assert _cursor(tmp_path) == (2,)
    assert [row[1] is not None and row[2] == 1 for row in _rows(tmp_path)] == [True, True]
    for item in report["items"]:
        assert (_stock(tmp_path) / item["file"]).is_file()
        assert not (tmp_path / "content" / "general" / "ru" / item["file"]).exists()


def test_failed_item_does_not_move_the_cursor_or_burn_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(tmp_path)
    _source(tmp_path, "a.mp4")

    def fail(profile: Any, card: Any, source: Path, output: Path, audio: Path | None) -> None:
        del profile, card, source, output, audio
        raise OSError("ffmpeg упал")

    monkeypatch.setattr(unique_module, "mix", fail)
    failed = _producer().produce(SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path))

    assert (failed.generated, failed.failed) == (0, 1)
    assert _cursor(tmp_path) is None
    assert _rows(tmp_path)[0][1:] == (None, 0, None)
    report = json.loads(
        (tmp_path / "generated" / "ru" / _SLUG / _RUN_ID / "run.json").read_text(encoding="utf-8")
    )
    assert report["items"][0]["status"] == "failed"
    assert "ffmpeg" in report["items"][0]["error"]
    assert not _stock(tmp_path).exists()

    monkeypatch.setattr(unique_module, "mix", _fake_mix)
    retried = _producer().produce(SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path))

    assert (retried.generated, retried.failed) == (1, 0)
    assert _cursor(tmp_path) == (1,)
    assert _rows(tmp_path)[0][2] == 1


def test_next_run_takes_the_next_source_and_wraps(tmp_path: Path) -> None:
    _profile(tmp_path)
    _source(tmp_path, "a.mp4")
    _source(tmp_path, "b.mp4")
    _source(tmp_path, "c.mp4")
    producer = _producer()

    taken = []
    for _ in range(4):
        produced = producer.produce(
            SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path)
        )
        report = json.loads(
            (tmp_path / "generated" / "ru" / _SLUG / produced.run_id / "run.json").read_text(
                encoding="utf-8"
            )
        )
        taken.append(Path(report["items"][0]["source_path"]).name)

    assert taken == ["a.mp4", "b.mp4", "c.mp4", "a.mp4"]
    assert _cursor(tmp_path) == (1,)


def test_pool_shorter_than_count_fails_before_any_work(tmp_path: Path) -> None:
    _profile(tmp_path)
    _source(tmp_path, "a.mp4")
    _source(tmp_path, "b.mp4")
    writer = _Writer()

    with pytest.raises(GeneratorError, match="доступно 2 исходников, нужно 3"):
        _producer(writer=writer).produce(
            SimpleNamespace(name=_PROFILE), 3, tmp_path, _settings(tmp_path)
        )

    assert writer.prompts == []
    assert not (tmp_path / "generated").exists()
    assert not _stock(tmp_path).exists()
    assert _cursor(tmp_path) is None
    assert [row[1:] for row in _rows(tmp_path)] == [(None, 0, None), (None, 0, None)]


def test_empty_pool_fails_before_any_work(tmp_path: Path) -> None:
    _profile(tmp_path)

    with pytest.raises(GeneratorError, match="пуст"):
        _producer().produce(SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path))

    assert not (tmp_path / "generated").exists()


def test_missing_source_file_fails_before_any_work(tmp_path: Path) -> None:
    _profile(tmp_path)
    source_id = _source(tmp_path, "a.mp4")
    (tmp_path / SOURCES_DIR / "ru" / _SLUG / "a.mp4").unlink()

    with pytest.raises(GeneratorError, match="не найден на диске"):
        _producer().produce(SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path))

    assert _cursor(tmp_path) is None
    assert _rows(tmp_path)[0] == (source_id, None, 0, None)


def test_clef_rejection_stops_before_the_text_model(tmp_path: Path) -> None:
    _profile(tmp_path, qa=["media", "clef"])
    _source(tmp_path, "a.mp4")
    writer = _Writer()
    checker = _Checker("кадр 1/1: nude 0.90 >= 0.3")

    produced = _producer(writer=writer, checker=checker).produce(
        SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path, safety=True)
    )

    assert (produced.generated, produced.failed) == (0, 1)
    assert writer.prompts == []
    assert len(checker.calls) == 1
    assert _cursor(tmp_path) is None
    assert not _stock(tmp_path).exists()


def test_clef_pass_keeps_the_source_check_in_the_recipe(tmp_path: Path) -> None:
    _profile(tmp_path, qa=["media", "clef"])
    _source(tmp_path, "a.mp4")
    checker = _Checker()

    produced = _producer(checker=checker).produce(
        SimpleNamespace(name=_PROFILE), 1, tmp_path, _settings(tmp_path, safety=True)
    )

    assert produced.generated == 1
    report = json.loads(
        (tmp_path / "generated" / "ru" / _SLUG / _RUN_ID / "run.json").read_text(encoding="utf-8")
    )
    assert report["production"][0]["qa"] == ["media", "clef"]
    assert len(checker.calls) == 1
