"""Flow dispatch: producer from the recipe, dry run without a call."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.flows.generate_batch.flow import generate_batch
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import ProducedBatch
from content_factory.services.production import registry

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


class _BrokenProducer(_FakeProducer):
    """Producer that must never be called in a dry run."""

    def produce(self, profile, count: int, data_dir: Path, settings: object) -> ProducedBatch:
        raise AssertionError("dry_run must not call a producer")


def _recipe(producer: str) -> dict[str, object]:
    return {
        "production": [
            {"producer": producer, "type": "video", "qa": ["media", "clef"]},
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
