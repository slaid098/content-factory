"""Flow dispatch: producer from the recipe, dry run without a call."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.constants import SOURCES_DIR
from content_factory.db.connection import connect
from content_factory.db.sources import upsert_import
from content_factory.exceptions.generator import GeneratorError
from content_factory.flows.generate_batch import flow as flow_module
from content_factory.flows.generate_batch.flow import generate_batch
from content_factory.schemas.content.delivery import ContentType
from content_factory.services.production import ProducedBatch, registry

_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "VAST_API_TOKEN",
    "GENERATOR_MODEL",
    "GENERATOR_MAX_COST_PER_HOUR",
    "GENERATOR_VIDEOS_PER_SERVER",
    "GENERATOR_MAX_SERVERS",
    "GENERATOR_OUTPUT_DIR",
)


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))


class _FakeProducer:
    """Producer that records its calls without any network activity."""

    supported_types = frozenset({ContentType.VIDEO})

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def produce(self, profile, count: int, data_dir: Path, settings: object) -> ProducedBatch:
        del data_dir, settings
        self.calls.append((profile.name, count))
        return ProducedBatch(run_id="fake-run", generated=count, failed=0, run_dirs=())


class _SequenceProducer(_FakeProducer):
    """Producer that gives every call its own run id."""

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix

    def produce(self, profile, count: int, data_dir: Path, settings: object) -> ProducedBatch:
        del data_dir, settings
        self.calls.append((profile.name, count))
        return ProducedBatch(
            run_id=f"{self.prefix}-{len(self.calls)}",
            generated=count,
            failed=0,
            run_dirs=(),
        )


class _BrokenProducer(_FakeProducer):
    """Producer that must never be called."""

    def produce(self, profile, count: int, data_dir: Path, settings: object) -> ProducedBatch:
        raise AssertionError("producer must not be called")


class _BrokenPhotoProducer(_BrokenProducer):
    """Photo producer that must never be called in a dry run."""

    supported_types = frozenset({ContentType.PHOTO})


class _PhotoSequenceProducer(_SequenceProducer):
    """Photo producer that gives every call its own run id."""

    supported_types = frozenset({ContentType.PHOTO})


def _recipe(*producers: str) -> dict[str, object]:
    return {
        "production": [
            {"producer": producer, "type": "video", "qa": ["media", "clef"]}
            for producer in producers
        ]
    }


def test_flow_resolves_the_producer_from_the_recipe(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    producer = _FakeProducer()
    registry.register("fake", lambda: producer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2)
    finally:
        registry.reset()

    assert outcome == "run=fake-run status=done generated=2 failed=0"
    assert producer.calls == [("dating/profile_2", 2)]


def test_flow_calls_one_batch_for_two_default_vast_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2")
    make_profile(tmp_path / "profiles", name="dating/profile_1")
    calls: list[dict[str, object]] = []

    def fake_run_batch(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            run_id="run-shared", generated=2, failed=0, status="done", run_dirs=()
        )

    monkeypatch.setattr(flow_module, "run_batch", fake_run_batch)

    outcome = generate_batch.fn(profiles="dating/profile_2,dating/profile_1", count=1)

    assert outcome == "run=run-shared status=done generated=2 failed=0"
    assert len(calls) == 1
    assert calls[0]["profiles"] == "dating/profile_2,dating/profile_1"
    assert calls[0]["count"] == 1


def test_dry_run_keeps_two_default_profiles_in_one_run(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2")
    make_profile(tmp_path / "profiles", name="dating/profile_1")

    outcome = generate_batch.fn(profiles="dating/profile_2,dating/profile_1", dry_run=True)

    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "generated").rglob("run.json")
    ]
    assert len(reports) == 2
    assert len({report["run_id"] for report in reports}) == 1
    assert outcome.startswith(f"run={reports[0]['run_id']} ")
    assert {item["profile"] for report in reports for item in report["items"]} == {
        "dating/profile_2",
        "dating/profile_1",
    }


def test_recipe_batches_keep_every_run_id_in_the_outcome(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        name="dating/profile_2",
        extra=_recipe("fake-a", "fake-b"),
    )
    make_profile(tmp_path / "profiles", name="dating/profile_1", extra=_recipe("fake-a"))
    first = _SequenceProducer("run-a")
    second = _SequenceProducer("run-b")
    registry.register("fake-a", lambda: first)
    registry.register("fake-b", lambda: second)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2,dating/profile_1", count=2)
    finally:
        registry.reset()

    assert outcome == ("run=run-a-1 run=run-b-1 run=run-a-2 status=done generated=6 failed=0")
    assert first.calls == [("dating/profile_2", 2), ("dating/profile_1", 2)]
    assert second.calls == [("dating/profile_2", 2)]


@pytest.mark.parametrize("count", [0, True])
def test_flow_rejects_an_invalid_count_before_any_producer(tmp_path: Path, count: int) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    registry.register("fake", _BrokenProducer)
    try:
        with pytest.raises(GeneratorError, match="count"):
            generate_batch.fn(profiles="dating/profile_2", count=count)
    finally:
        registry.reset()


def test_dry_run_validates_the_recipe_without_calling_the_producer(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    registry.register("fake", _BrokenProducer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)
    finally:
        registry.reset()

    assert "status=dry_run" in outcome
    reports = list((tmp_path / "generated").rglob("run.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["production"] == [{"producer": "fake", "type": "video", "qa": ["media", "clef"]}]
    assert report["items"][0]["producer"] == "fake"


def _photo_recipe() -> dict[str, object]:
    return {"production": [{"producer": "card", "type": "photo"}]}


def _mixed_recipe() -> dict[str, object]:
    return {
        "production": [
            {"producer": "vast", "type": "video", "qa": ["media", "clef"]},
            {"producer": "card", "type": "photo"},
        ]
    }


def test_dry_run_plans_photo_items_without_calling_the_producer(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", width=544, height=960, extra=_photo_recipe())
    registry.register("card", _BrokenPhotoProducer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)
    finally:
        registry.reset()

    assert "status=dry_run" in outcome
    report = json.loads(
        next((tmp_path / "generated").rglob("run.json")).read_text(encoding="utf-8")
    )
    assert report["production"] == [{"producer": "card", "type": "photo", "qa": ["image"]}]
    assert [item["file"] for item in report["items"]] == [
        f"dating_profile_2_{report['run_id']}_{seq:03d}.jpg" for seq in (1, 2)
    ]
    assert [item["status"] for item in report["items"]] == ["dry_run", "dry_run"]
    assert [item["type"] for item in report["items"]] == ["photo", "photo"]
    assert [item["producer"] for item in report["items"]] == ["card", "card"]
    for item in report["items"]:
        assert item["look_id"] is None
        assert item["seed"] is None
        assert item["frames"] is None


def test_dry_run_plans_every_recipe_step_by_type(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_mixed_recipe())
    registry.register("vast", _BrokenProducer)
    registry.register("card", _BrokenPhotoProducer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)
    finally:
        registry.reset()

    assert "status=dry_run" in outcome
    reports = list((tmp_path / "generated").rglob("run.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["production"] == [
        {"producer": "vast", "type": "video", "qa": ["media", "clef"]},
        {"producer": "card", "type": "photo", "qa": ["image"]},
    ]
    assert [item["type"] for item in report["items"]] == ["video", "video", "photo", "photo"]
    assert [Path(item["file"]).suffix for item in report["items"]] == [
        ".mp4",
        ".mp4",
        ".jpg",
        ".jpg",
    ]
    assert [item["status"] for item in report["items"]] == ["dry_run"] * 4
    assert all(item["look_id"] is not None for item in report["items"][:2])
    assert all(item["look_id"] is None for item in report["items"][2:])


def test_dry_run_starts_videos_after_the_rotation_cursor(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_mixed_recipe())
    cursor = tmp_path / "generated" / "ru" / "dating_profile_2" / "rotation.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"last_look_id": "c01"}), encoding="utf-8")
    registry.register("vast", _BrokenProducer)
    registry.register("card", _BrokenPhotoProducer)
    try:
        generate_batch.fn(profiles="dating/profile_2", count=1, dry_run=True)
    finally:
        registry.reset()

    report = json.loads(
        next((tmp_path / "generated").rglob("run.json")).read_text(encoding="utf-8")
    )
    video = [item for item in report["items"] if item["type"] == "video"]
    assert [item["look_id"] for item in video] == ["c02"]


def test_mixed_recipe_returns_every_step_run_id(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_mixed_recipe())
    video = _SequenceProducer("run-video")
    photo = _PhotoSequenceProducer("run-photo")
    registry.register("vast", lambda: video)
    registry.register("card", lambda: photo)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=1)
    finally:
        registry.reset()

    assert outcome == "run=run-video-1 run=run-photo-1 status=done generated=2 failed=0"
    assert video.calls == [("dating/profile_2", 1)]
    assert photo.calls == [("dating/profile_2", 1)]


def _unique_recipe() -> dict[str, object]:
    return {
        "production": [
            {"producer": "unique", "type": "video", "qa": ["media", "clef"]},
        ]
    }


def _add_unique_source(tmp_path: Path, name: str = "a.mp4", duration: float = 5.0) -> str:
    relative = f"{SOURCES_DIR}/ru/dating_profile_2/{name}"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(name.encode())
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        upsert_import(
            connection,
            language="ru",
            profile_id="dating/profile_2",
            path=relative,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=path.stat().st_size,
            duration=duration,
            now="2026-01-01T00:00:00+00:00",
        )
        connection.commit()
    finally:
        connection.close()
    return relative


def test_dry_run_plans_unique_sources_without_cursor_or_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles", extra=_unique_recipe())
    first = _add_unique_source(tmp_path, "a.mp4", duration=5.0)
    second = _add_unique_source(tmp_path, "b.mp4", duration=7.0)

    def forbidden(**kwargs: object) -> None:
        raise AssertionError(f"dry run must not call the vast batch: {kwargs}")

    monkeypatch.setattr(flow_module, "run_batch", forbidden)

    outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)

    report = json.loads(
        next((tmp_path / "generated").rglob("run.json")).read_text(encoding="utf-8")
    )
    assert outcome.startswith(f"run={report['run_id']} ")
    assert "status=dry_run" in outcome
    assert [item["source_path"] for item in report["items"]] == [first, second]
    assert [item["duration"] for item in report["items"]] == [5.0, 7.0]
    assert all(item["source_sha256"] for item in report["items"])
    assert all(item["status"] == "dry_run" for item in report["items"])
    assert all(item["producer"] == "unique" for item in report["items"])
    assert [item["look_id"] for item in report["items"]] == [None, None]
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0] == 0
        rows = connection.execute("SELECT used_at, use_count FROM source_items").fetchall()
        assert [tuple(row) for row in rows] == [(None, 0), (None, 0)]
    finally:
        connection.close()


def test_dry_run_unique_pool_short_fails_before_any_report(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_unique_recipe())
    _add_unique_source(tmp_path, "a.mp4")

    with pytest.raises(GeneratorError, match="доступно 1 исходников, нужно 2"):
        generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)

    assert list((tmp_path / "generated").rglob("run.json")) == []
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0] == 0
    finally:
        connection.close()
