"""Flow dispatch: recipe groups, one pipeline run and its reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from tests.unit.generator.helpers import make_profile

from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.flows.generate_batch import flow as generate_flow
from content_factory.flows.generate_batch.flow import generate_batch
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ArtifactKind,
    Layer,
    PipelineContext,
)
from content_factory.services.generator import load_profile
from content_factory.services.production import registry
from content_factory.services.production.card import CardProducer

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
    """Layer producer that records calls without any network activity."""

    def __init__(
        self,
        kind: ArtifactKind = ArtifactKind.VIDEO,
        *,
        prefix: str = "item",
        failed_seq: int | None = None,
    ) -> None:
        self.supported_outputs = frozenset({kind})
        self.kind = kind
        self.prefix = prefix
        self.failed_seq = failed_seq
        self.calls: list[tuple[tuple[str, ...], int]] = []

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Plan one artifact per profile and item."""
        del layer
        profiles = tuple(profile.name for profile in context.profiles)
        return tuple(
            self._artifact(profile, seq, context, planned=True)
            for profile in profiles
            for seq in range(1, context.count + 1)
        )

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Produce one artifact per profile and item."""
        del layer, inputs
        profiles = tuple(profile.name for profile in context.profiles)
        self.calls.append((profiles, context.count))
        artifacts: list[Artifact] = []
        for profile in profiles:
            for seq in range(1, context.count + 1):
                failed = self.failed_seq == seq
                artifact = self._artifact(profile, seq, context, planned=False)
                if failed:
                    artifact = Artifact(
                        artifact.item,
                        artifact.profile,
                        artifact.kind,
                        artifact.path,
                        artifact.media_type,
                        {**artifact.meta, "status": "failed", "error": "сбой"},
                    )
                artifacts.append(artifact)
        return tuple(artifacts)

    def _artifact(
        self,
        profile: str,
        seq: int,
        context: PipelineContext,
        *,
        planned: bool,
    ) -> Artifact:
        suffix = "jpg" if self.kind is ArtifactKind.PHOTO else "mp4"
        name = f"{profile.replace('/', '_')}_{self.prefix}_{seq:03d}.{suffix}"
        path = context.settings.output_dir / profile / context.run_id / name
        meta: dict[str, object] = {
            "status": "planned" if planned else "done",
            "look_id": None if planned else f"c0{seq}",
            "seed": None if planned else seq,
            "frames": None if planned else 119,
            "duration": None if planned else 4.96,
        }
        if self.kind is ArtifactKind.PHOTO:
            loaded = load_profile(profile, context.data_dir / PROFILES_DIR)
            meta["width"], meta["height"] = loaded.width, loaded.height
            if not planned:
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (loaded.width, loaded.height), "white").save(path, format="JPEG")
        return Artifact(
            item=name,
            profile=profile,
            kind=self.kind,
            path=path,
            media_type="image/jpeg" if suffix == "jpg" else "video/mp4",
            meta=meta,
        )


class _BrokenProducer(_FakeProducer):
    """Producer that must never be called."""

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        raise AssertionError("producer must not be called")


class _CardWriter:
    """Text model port with one fixed, parseable card answer."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def write(self, system_prompt: str) -> str:
        self.prompts.append(system_prompt)
        return "<result><block>Первая строка</block><block>Пиши мне</block></result>"


def _card_renderer(profile: Any, overlay: Any) -> Image.Image:
    """Draw no text over a transparent card of the profile size."""
    del overlay
    return Image.new("RGBA", (profile.width, profile.height), (0, 0, 0, 0))


def _recipe(*producers: str, kind: str = "video", qa: str = "clef") -> dict[str, object]:
    return {
        "production": [{"producer": producer, "type": kind, "qa": [qa]} for producer in producers]
    }


def _reports(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "generated").rglob("run.json"))
    ]


def test_flow_resolves_the_producer_from_the_recipe(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    producer = _FakeProducer()
    registry.register("fake", lambda: producer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=2 failed=0", outcome)
    assert producer.calls == [(("dating/profile_2",), 2)]
    report = _reports(tmp_path)[0]
    assert report["status"] == "done"
    assert report["production"] == [{"producer": "fake", "type": "video", "qa": ["clef"]}]
    assert [item["file"] for item in report["items"]] == [
        f"dating_profile_2_item_{seq:03d}.mp4" for seq in (1, 2)
    ]


def test_same_recipe_profiles_share_one_run_and_one_call(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2", extra=_recipe("vast"))
    make_profile(tmp_path / "profiles", name="dating/profile_1", extra=_recipe("vast"))
    producer = _FakeProducer()
    registry.register("vast", lambda: producer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2,dating/profile_1", count=1)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=2 failed=0", outcome)
    assert len(producer.calls) == 1
    assert producer.calls[0][0] == ("dating/profile_2", "dating/profile_1")
    reports = _reports(tmp_path)
    assert len(reports) == 2
    assert len({report["run_id"] for report in reports}) == 1
    assert {item["profile"] for report in reports for item in report["items"]} == {
        "dating/profile_2",
        "dating/profile_1",
    }


def test_card_group_gives_every_profile_its_own_report_and_stock(tmp_path: Path) -> None:
    names = ("dating/profile_2", "dating/profile_1")
    for name in names:
        make_profile(
            tmp_path / "profiles",
            name=name,
            width=544,
            height=960,
            extra={"production": [{"producer": "card", "type": "photo"}]},
        )
    writer = _CardWriter()
    registry.register("card", lambda: CardProducer(writer=writer, renderer=_card_renderer))
    try:
        outcome = generate_batch.fn(profiles=",".join(names), count=2)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=4 failed=0", outcome)
    reports = _reports(tmp_path)
    assert len(reports) == 2
    run_ids = {report["run_id"] for report in reports}
    assert len(run_ids) == 1
    run_id = run_ids.pop()
    by_profile = {report["profiles"][0]: report for report in reports}
    assert set(by_profile) == set(names)
    for name, report in by_profile.items():
        slug = name.replace("/", "_")
        run_dir = tmp_path / "generated" / "ru" / slug / run_id
        assert (run_dir / "run.json").is_file()
        assert report["profiles"] == [name]
        assert [item["profile"] for item in report["items"]] == [name, name]
        assert [item["file"] for item in report["items"]] == [
            f"{slug}_{run_id}_{seq:03d}.jpg" for seq in (1, 2)
        ]
        for item in report["items"]:
            assert (run_dir / "final" / item["file"]).is_file()
    stock = tmp_path / "content" / "dating" / "ru"
    assert sorted(path.name for path in stock.glob("*.jpg")) == sorted(
        f"{name.replace('/', '_')}_{run_id}_{seq:03d}.jpg" for name in names for seq in (1, 2)
    )


def test_slug_collision_is_rejected_before_any_producer(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/p1", extra=_recipe("fake"))
    make_profile(tmp_path / "profiles", name="dating_p1", extra=_recipe("fake"))
    registry.register("fake", _BrokenProducer)
    try:
        with pytest.raises(ProfileError, match="одинаковый каталог"):
            generate_batch.fn(profiles="dating/p1,dating_p1", count=1)
    finally:
        registry.reset()

    assert _reports(tmp_path) == []


def test_different_recipes_get_their_own_run_and_group(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2", extra=_recipe("fake-a"))
    make_profile(tmp_path / "profiles", name="dating/profile_1", extra=_recipe("fake-b"))
    first = _FakeProducer()
    second = _FakeProducer(prefix="other")
    registry.register("fake-a", lambda: first)
    registry.register("fake-b", lambda: second)
    try:
        outcome = generate_batch.fn(
            profiles="dating/profile_2,dating/profile_1",
            count=1,
        )
    finally:
        registry.reset()

    assert re.fullmatch(
        r"run=\S+ run=\S+ status=done generated=2 failed=0",
        outcome,
    )
    assert first.calls == [(("dating/profile_2",), 1)]
    assert second.calls == [(("dating/profile_1",), 1)]
    reports = _reports(tmp_path)
    assert len({report["run_id"] for report in reports}) == 2


@pytest.mark.parametrize("count", [0, True])
def test_flow_rejects_an_invalid_count_before_any_producer(tmp_path: Path, count: int) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    registry.register("fake", _BrokenProducer)
    try:
        with pytest.raises(GeneratorError, match="count"):
            generate_batch.fn(profiles="dating/profile_2", count=count)
    finally:
        registry.reset()


def test_dry_run_plans_without_calling_the_producer(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("fake"))
    registry.register("fake", _BrokenProducer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=dry_run generated=0 failed=0", outcome)
    report = _reports(tmp_path)[0]
    assert report["production"] == [{"producer": "fake", "type": "video", "qa": ["clef"]}]
    assert [item["status"] for item in report["items"]] == ["dry_run", "dry_run"]
    assert [item["producer"] for item in report["items"]] == ["fake", "fake"]


def test_dry_run_plans_photo_items_without_calling_the_producer(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        width=544,
        height=960,
        extra={"production": [{"producer": "card", "type": "photo"}]},
    )
    outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)

    assert "status=dry_run" in outcome
    report = _reports(tmp_path)[0]
    assert report["production"] == [{"producer": "card", "type": "photo", "qa": ["image"]}]
    assert [item["file"] for item in report["items"]] == [
        f"dating_profile_2_{report['run_id']}_{seq:03d}.jpg" for seq in (1, 2)
    ]
    assert [item["status"] for item in report["items"]] == ["dry_run", "dry_run"]
    assert [item["type"] for item in report["items"]] == ["photo", "photo"]
    for item in report["items"]:
        assert item["look_id"] is None
        assert item["seed"] is None
        assert item["frames"] is None


def test_dry_run_plans_every_independent_chain_of_one_profile(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["media", "clef"]},
                {"producer": "card", "type": "photo"},
            ]
        },
    )
    outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)

    assert re.fullmatch(r"run=\S+ status=dry_run generated=0 failed=0", outcome)
    reports = _reports(tmp_path)
    assert len(reports) == 1
    report = reports[0]
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
    make_profile(tmp_path / "profiles")
    cursor = tmp_path / "generated" / "ru" / "dating_profile_2" / "rotation.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"last_look_id": "c01"}), encoding="utf-8")

    generate_batch.fn(profiles="dating/profile_2", count=1, dry_run=True)

    report = _reports(tmp_path)[0]
    assert [item["look_id"] for item in report["items"]] == ["c02"]


def test_mixed_recipe_runs_both_chains_in_one_pipeline_run(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo"},
            ]
        },
    )
    video = _FakeProducer()
    photo = _FakeProducer(ArtifactKind.PHOTO, prefix="photo")
    registry.register("vast", lambda: video)
    registry.register("card", lambda: photo)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=1)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=2 failed=0", outcome)
    assert video.calls == [(("dating/profile_2",), 1)]
    assert photo.calls == [(("dating/profile_2",), 1)]
    report = _reports(tmp_path)[0]
    assert [item["type"] for item in report["items"]] == ["video", "photo"]


def test_failed_item_does_not_stop_the_next_item_or_profile(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2", extra=_recipe("vast"))
    make_profile(tmp_path / "profiles", name="dating/profile_1", extra=_recipe("vast"))
    producer = _FakeProducer(failed_seq=1)
    registry.register("vast", lambda: producer)
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2,dating/profile_1", count=2)
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=failed generated=2 failed=2", outcome)
    reports = _reports(tmp_path)
    assert {item["profile"] for report in reports for item in report["items"]} == {
        "dating/profile_2",
        "dating/profile_1",
    }
    assert [
        item["status"]
        for report in reports
        for item in report["items"]
        if item["file"].endswith("002.mp4")
    ] == ["done", "done"]


def test_content_type_photo_runs_only_the_photo_chain(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo"},
            ]
        },
    )
    photo = _FakeProducer(ArtifactKind.PHOTO, prefix="photo")
    registry.register("vast", _BrokenProducer)
    registry.register("card", lambda: photo)
    try:
        outcome = generate_batch.fn(
            profiles="dating/profile_2",
            count=1,
            content_type="photo",
        )
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=1 failed=0", outcome)
    assert photo.calls == [(("dating/profile_2",), 1)]
    report = _reports(tmp_path)[0]
    assert report["production"] == [{"producer": "card", "type": "photo", "qa": ["image"]}]
    assert [item["type"] for item in report["items"]] == ["photo"]
    assert [item["status"] for item in report["items"]] == ["done"]
    assert not list((tmp_path / "generated").rglob("*.mp4"))


def test_content_type_video_runs_only_the_video_chain(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo"},
            ]
        },
    )
    video = _FakeProducer()
    registry.register("vast", lambda: video)
    try:
        outcome = generate_batch.fn(
            profiles="dating/profile_2",
            count=1,
            content_type="video",
        )
    finally:
        registry.reset()

    assert re.fullmatch(r"run=\S+ status=done generated=1 failed=0", outcome)
    assert video.calls == [(("dating/profile_2",), 1)]
    report = _reports(tmp_path)[0]
    assert report["production"] == [{"producer": "vast", "type": "video", "qa": ["clef"]}]
    assert [item["type"] for item in report["items"]] == ["video"]
    assert not list((tmp_path / "generated").rglob("*.jpg"))


def test_content_type_without_the_layer_is_rejected(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        extra={"production": [{"producer": "card", "type": "photo"}]},
    )

    with pytest.raises(ProfileError, match="нет слоя"):
        generate_batch.fn(profiles="dating/profile_2", count=1, content_type="video")

    assert _reports(tmp_path) == []


def test_unknown_content_type_is_rejected(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra=_recipe("vast"))

    with pytest.raises(ProfileError, match="content_type"):
        generate_batch.fn(profiles="dating/profile_2", count=1, content_type="audio")

    assert _reports(tmp_path) == []


def test_filtered_layer_with_an_input_of_another_type_is_rejected() -> None:
    layers = (
        Layer(name="vast", producer="vast", inputs=(), output=ArtifactKind.VIDEO),
        Layer(name="card", producer="card", inputs=("vast",), output=ArtifactKind.PHOTO),
    )

    with pytest.raises(ProfileError, match="другого типа"):
        generate_flow._filter_recipe("dating/profile_2", layers, ContentType.PHOTO)
