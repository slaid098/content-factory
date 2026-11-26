"""Producer and check registries, layer adapters and the Vast layer producer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from tests.unit.generator.helpers import make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ArtifactKind,
    CheckResult,
    ItemFailure,
    Layer,
    LayerOutcome,
    PipelineContext,
    PipelineResult,
)
from content_factory.schemas.generator.run import RunItem, RunReport, RunServer, RunStatus
from content_factory.services.production import checks as checks_module
from content_factory.services.production import qa as production_qa
from content_factory.services.production import registry
from content_factory.services.production import reports as reports_module
from content_factory.services.production import vast as vast_module
from content_factory.services.production.card import CardProducer, check_image
from content_factory.services.production.registry import ProducerRegistry
from content_factory.services.production.vast import VastProducer

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"


def _settings(tmp_path: Path) -> GeneratorSettings:
    return GeneratorSettings(output_dir=tmp_path / "generated", safety_enabled=False)


def _context(tmp_path: Path, count: int = 1) -> PipelineContext:
    return PipelineContext(
        profiles=(SimpleNamespace(name="dating/profile_2"),),
        count=count,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        run_id=_RUN_ID,
        now=_NOW,
    )


def _layer(kind: ArtifactKind = ArtifactKind.VIDEO) -> Layer:
    return Layer("layer", "producer", (), kind)


def _artifact(
    tmp_path: Path,
    *,
    kind: ArtifactKind = ArtifactKind.VIDEO,
    status: str = "done",
    error: str | None = None,
    meta: dict[str, object] | None = None,
) -> Artifact:
    path = tmp_path / f"item.{kind.value}"
    payload = {"status": status, "error": error, **(meta or {})}
    return Artifact("item", "dating/profile_2", kind, path, None, payload)


class _FakeProducer:
    """Layer producer that only records its resolution."""

    supported_outputs = frozenset({ArtifactKind.VIDEO})

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Return no plan."""
        del layer, context
        return ()

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Return no artifacts."""
        del layer, inputs, context
        return ()


def test_vast_is_the_builtin_video_producer() -> None:
    assert "vast" in registry.available()
    producer = registry.resolve("vast")
    assert isinstance(producer, VastProducer)
    assert producer.supported_outputs == frozenset({ArtifactKind.VIDEO})


def test_card_is_the_builtin_photo_producer() -> None:
    assert "card" in registry.available()
    producer = registry.resolve("card")
    assert isinstance(producer, CardProducer)
    assert producer.supported_outputs == frozenset({ArtifactKind.PHOTO})


def test_layer_registry_can_be_injected_and_reset() -> None:
    local = ProducerRegistry()
    assert local.available() == ()

    producer = _FakeProducer()
    local.register("fake", lambda: producer)

    assert local.available() == ("fake",)
    assert local.resolve("fake") is producer

    local.reset()
    assert local.available() == ()


def test_layer_registry_rejects_blank_and_unknown_names() -> None:
    local = ProducerRegistry()

    with pytest.raises(ProfileError, match="пустое имя"):
        local.register("  ", _FakeProducer)

    with pytest.raises(ProfileError, match="неизвестный producer 'unique': допустимы нет"):
        local.resolve("unique")


def test_process_registry_restores_builtins_after_injection() -> None:
    registry.register("fake", _FakeProducer)
    assert "fake" in registry.available()

    registry.reset()
    assert registry.available() == ("card", "vast")


def test_qa_builtins_have_their_pipeline_stages() -> None:
    assert production_qa.available() == ("clef", "image", "mp4")
    assert production_qa.resolve("clef").stage == production_qa.QA_SOURCE
    assert production_qa.resolve("image").stage == production_qa.QA_FINAL
    assert production_qa.resolve("mp4").stage == production_qa.QA_FINAL
    assert production_qa.has(("mp4", "clef"), production_qa.QA_FINAL)
    assert not production_qa.has(("mp4",), production_qa.QA_SOURCE)


def test_layer_checks_resolve_the_builtin_adapters() -> None:
    checks = production_qa.default_checks()

    assert checks.available() == ("clef", "image", "mp4")
    assert isinstance(checks.resolve("mp4"), checks_module.ArtifactCheck)
    assert isinstance(checks.resolve("clef"), checks_module.ArtifactCheck)
    assert isinstance(checks.resolve("image"), checks_module.ArtifactCheck)


def test_qa_checks_run_in_the_declared_order() -> None:
    local = production_qa.Registry({"second": production_qa.QA_SOURCE, "first": "source"})
    events: list[str] = []

    def handler(name: str):
        def run() -> str | None:
            events.append(name)
            return None

        return run

    reason = local.run(
        ("second", "first"),
        production_qa.QA_SOURCE,
        {"second": handler("second"), "first": handler("first")},
    )

    assert reason is None
    assert events == ["second", "first"]


def test_qa_runs_only_the_checks_of_the_requested_stage() -> None:
    local = production_qa.Registry(
        {"source": production_qa.QA_SOURCE, "final": production_qa.QA_FINAL}
    )
    events: list[str] = []

    reason = local.run(
        ("final", "source"),
        production_qa.QA_SOURCE,
        {
            "source": lambda: events.append("source") or None,
            "final": lambda: events.append("final") or None,
        },
    )

    assert reason is None
    assert events == ["source"]


def test_qa_stops_at_the_first_reason() -> None:
    local = production_qa.Registry({"bad": production_qa.QA_SOURCE, "later": "source"})
    events: list[str] = []

    reason = local.run(
        ("bad", "later"),
        production_qa.QA_SOURCE,
        {
            "bad": lambda: events.append("bad") or "брак",
            "later": lambda: events.append("later") or None,
        },
    )

    assert reason == "брак"
    assert events == ["bad"]


def test_qa_rejects_an_unknown_check_or_a_missing_handler() -> None:
    local = production_qa.Registry({"known": production_qa.QA_SOURCE})

    with pytest.raises(ProfileError, match="неизвестная QA-проверка"):
        local.resolve("absent")

    with pytest.raises(ProfileError, match="пустое имя"):
        local.register(" ", production_qa.QA_SOURCE)

    with pytest.raises(GeneratorError, match="неизвестный этап"):
        local.register("other", "middle")

    with pytest.raises(GeneratorError, match="нет обработчика"):
        local.run(("known",), production_qa.QA_SOURCE, {})


def test_mp4_and_clef_checks_report_a_producer_failure(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, status="failed", error="сервер не ответил")
    layer = Layer("vast", "vast", (), ArtifactKind.VIDEO, ("mp4", "clef"))
    context = _context(tmp_path)

    for check in (
        checks_module.ArtifactCheck(checks_module.mp4_reason),
        checks_module.ArtifactCheck(checks_module.source_reason),
    ):
        assert check.check(layer, artifact, context) == CheckResult(False, "сервер не ответил")


def test_image_check_reports_a_producer_failure(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        kind=ArtifactKind.PHOTO,
        status="failed",
        error="модель не ответила",
    )

    result = checks_module.ArtifactCheck(checks_module.image_reason).check(
        _layer(ArtifactKind.PHOTO), artifact, _context(tmp_path)
    )

    assert result == CheckResult(False, "модель не ответила")


def test_image_check_accepts_a_finished_photo(tmp_path: Path) -> None:
    path = tmp_path / "post.jpg"
    Image.new("RGB", (544, 960), "white").save(path, format="JPEG")
    artifact = Artifact(
        "post.jpg",
        "dating/profile_2",
        ArtifactKind.PHOTO,
        path,
        "image/jpeg",
        {"status": "done", "width": 544, "height": 960},
    )

    result = checks_module.ArtifactCheck(checks_module.image_reason).check(
        _layer(ArtifactKind.PHOTO), artifact, _context(tmp_path)
    )

    assert result == CheckResult(True)
    assert check_image(path, 544, 960) is None


def test_image_check_rejects_a_wrong_size(tmp_path: Path) -> None:
    path = tmp_path / "post.jpg"
    Image.new("RGB", (10, 10), "white").save(path, format="JPEG")
    artifact = Artifact(
        "post.jpg",
        "dating/profile_2",
        ArtifactKind.PHOTO,
        path,
        "image/jpeg",
        {"status": "done", "width": 544, "height": 960},
    )

    result = checks_module.ArtifactCheck(checks_module.image_reason).check(
        _layer(ArtifactKind.PHOTO), artifact, _context(tmp_path)
    )

    assert result.passed is False
    assert "размер" in str(result.reason)


def test_vast_plans_every_profile_item(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")
    context = _context(tmp_path, count=2)

    artifacts = VastProducer().plan_layer(_layer(), context)

    assert [artifact.item for artifact in artifacts] == [
        f"dating_profile_2_{_RUN_ID}_{seq:03d}.mp4" for seq in (1, 2)
    ]
    assert [artifact.meta["look_id"] for artifact in artifacts] == ["c01", "c02"]
    assert all(artifact.meta["frames"] for artifact in artifacts)
    assert not list((tmp_path / "generated").rglob("rotation.json"))


def test_vast_produces_the_group_and_reads_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    calls: list[dict[str, Any]] = []
    report = RunReport(
        run_id=_RUN_ID,
        started_at=_NOW,
        status=RunStatus.DONE,
        profiles=["dating/profile_2"],
        count=1,
        server=RunServer(contract_id="42", ip="127.0.0.1"),
        items=[
            RunItem(
                file=f"dating_profile_2_{_RUN_ID}_001.mp4",
                profile="dating/profile_2",
                look_id="c01",
                seed=7,
                frames=119,
                duration=4.96,
                status=RunStatus.DONE,
            )
        ],
    )

    def fake_generate_batch(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        run_dir = tmp_path / "generated" / "ru" / "dating_profile_2" / _RUN_ID
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(report.model_dump_json(), encoding="utf-8")
        return SimpleNamespace(run_id=_RUN_ID, generated=1, failed=0, run_dirs=(run_dir,))

    monkeypatch.setattr(vast_module, "generate_batch", fake_generate_batch)
    context = _context(tmp_path)

    artifacts = VastProducer().produce_layer(_layer(), (), context)

    assert calls == [
        {
            "profiles": "dating/profile_2",
            "count": 1,
            "data_dir": tmp_path,
            "settings": context.settings,
            "run_id": _RUN_ID,
            "rng": context.rng,
            "now": _NOW,
        }
    ]
    artifact = artifacts[0]
    assert artifact.item == f"dating_profile_2_{_RUN_ID}_001.mp4"
    assert artifact.meta["status"] == "done"
    assert artifact.meta["look_id"] == "c01"
    assert artifact.meta["server"] == {
        "contract_id": "42",
        "ip": "127.0.0.1",
        "gpu": None,
        "image": None,
        "cost_per_hour": None,
    }


def test_vast_calls_generate_batch_once_for_the_whole_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_2")
    make_profile(tmp_path / "profiles", name="dating/profile_1")
    calls: list[dict[str, Any]] = []

    def fake_generate_batch(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        run_dirs: list[Path] = []
        for name in str(kwargs["profiles"]).split(","):
            slug = name.replace("/", "_")
            run_dir = tmp_path / "generated" / "ru" / slug / _RUN_ID
            run_dir.mkdir(parents=True)
            report = RunReport(
                run_id=_RUN_ID,
                started_at=_NOW,
                status=RunStatus.DONE,
                profiles=[name],
                count=1,
                items=[
                    RunItem(
                        file=f"{slug}_{_RUN_ID}_001.mp4",
                        profile=name,
                        look_id="c01",
                        seed=7,
                        frames=119,
                        duration=4.96,
                        status=RunStatus.DONE,
                    )
                ],
            )
            (run_dir / "run.json").write_text(report.model_dump_json(), encoding="utf-8")
            run_dirs.append(run_dir)
        return SimpleNamespace(run_id=_RUN_ID, generated=2, failed=0, run_dirs=tuple(run_dirs))

    monkeypatch.setattr(vast_module, "generate_batch", fake_generate_batch)
    context = PipelineContext(
        profiles=(
            SimpleNamespace(name="dating/profile_2"),
            SimpleNamespace(name="dating/profile_1"),
        ),
        count=1,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        run_id=_RUN_ID,
        now=_NOW,
    )

    artifacts = VastProducer().produce_layer(_layer(), (), context)

    assert len(calls) == 1
    assert calls[0]["profiles"] == "dating/profile_2,dating/profile_1"
    assert [(artifact.profile, artifact.item) for artifact in artifacts] == [
        ("dating/profile_2", f"dating_profile_2_{_RUN_ID}_001.mp4"),
        ("dating/profile_1", f"dating_profile_1_{_RUN_ID}_001.mp4"),
    ]


def _fake_vast_run(tmp_path: Path, report: RunReport) -> Any:
    """Return a ``generate_batch`` fake that writes *report* for profile_2."""

    def fake_generate_batch(**kwargs: Any) -> SimpleNamespace:
        del kwargs
        run_dir = tmp_path / "generated" / "ru" / "dating_profile_2" / _RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(report.model_dump_json(), encoding="utf-8")
        return SimpleNamespace(run_id=_RUN_ID, generated=0, failed=1, run_dirs=(run_dir,))

    return fake_generate_batch


def test_vast_rejects_report_items_of_another_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    report = RunReport(
        run_id=_RUN_ID,
        started_at=_NOW,
        status=RunStatus.FAILED,
        profiles=["dating/profile_1"],
        count=1,
        items=[
            RunItem(
                file=f"dating_profile_1_{_RUN_ID}_001.mp4",
                profile="dating/profile_1",
                status=RunStatus.DONE,
            )
        ],
    )
    monkeypatch.setattr(vast_module, "generate_batch", _fake_vast_run(tmp_path, report))

    artifacts = VastProducer().produce_layer(_layer(), (), _context(tmp_path))

    artifact = artifacts[0]
    assert artifact.profile == "dating/profile_2"
    assert artifact.item == f"dating_profile_2_{_RUN_ID}_001.mp4"
    assert artifact.path == (
        tmp_path / "generated" / "ru" / "dating_profile_2" / _RUN_ID / "final" / artifact.item
    )
    assert artifact.meta["status"] == "failed"
    assert artifact.meta["error"] == "нет отчёта генератора"


def test_vast_never_takes_a_file_path_from_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    report = RunReport(
        run_id=_RUN_ID,
        started_at=_NOW,
        status=RunStatus.DONE,
        profiles=["dating/profile_2"],
        count=1,
        items=[
            RunItem(
                file="../../outside.mp4",
                profile="dating/profile_2",
                status=RunStatus.DONE,
            )
        ],
    )
    monkeypatch.setattr(vast_module, "generate_batch", _fake_vast_run(tmp_path, report))

    artifacts = VastProducer().produce_layer(_layer(), (), _context(tmp_path))

    artifact = artifacts[0]
    assert artifact.item == f"dating_profile_2_{_RUN_ID}_001.mp4"
    assert artifact.path.parent == (
        tmp_path / "generated" / "ru" / "dating_profile_2" / _RUN_ID / "final"
    )
    assert artifact.meta["status"] == "failed"
    assert artifact.meta["error"] == "нет отчёта генератора"


def test_vast_rejects_a_report_of_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    report = RunReport(
        run_id="20260101T000000Z-2",
        started_at=_NOW,
        status=RunStatus.DONE,
        profiles=["dating/profile_2"],
        count=1,
        items=[
            RunItem(
                file=f"dating_profile_2_{_RUN_ID}_001.mp4",
                profile="dating/profile_2",
                status=RunStatus.DONE,
            )
        ],
    )
    monkeypatch.setattr(vast_module, "generate_batch", _fake_vast_run(tmp_path, report))

    artifacts = VastProducer().produce_layer(_layer(), (), _context(tmp_path))

    assert artifacts[0].meta["status"] == "failed"
    assert artifacts[0].meta["error"] == "нет отчёта генератора"


def test_vast_rejects_a_missing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_profile(tmp_path / "profiles")
    monkeypatch.setattr(
        vast_module,
        "generate_batch",
        lambda **kwargs: SimpleNamespace(run_id=_RUN_ID, generated=1, failed=0, run_dirs=()),
    )

    with pytest.raises(GeneratorError, match="не удалось прочитать отчёт"):
        VastProducer().produce_layer(_layer(), (), _context(tmp_path))


def test_reports_keep_the_old_run_json_fields(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        meta={
            "look_id": "c01",
            "seed": 7,
            "frames": 119,
            "duration": 4.96,
            "expected_seconds": 4.96,
            "expect_audio": True,
            "server": {"contract_id": "42"},
        },
    )
    artifact.path.write_bytes(b"video")
    result = PipelineResult(
        layers=(
            LayerOutcome(
                layer="layer",
                artifacts=(artifact,),
                failures=(),
                produced=(artifact,),
            ),
        ),
        artifacts=(artifact,),
        failures=(),
    )
    layer = Layer("layer", "vast", (), ArtifactKind.VIDEO, ("mp4", "clef"))
    context = _context(tmp_path)

    report = reports_module.build_report(
        SimpleNamespace(name="dating/profile_2"),
        (layer,),
        context,
        result,
    )

    assert report.run_id == _RUN_ID
    assert report.status is RunStatus.DONE
    assert report.production[0].producer == "vast"
    assert report.production[0].type.value == "video"
    assert report.production[0].qa == ["mp4", "clef"]
    assert report.server is not None
    assert report.server.contract_id == "42"
    assert report.items[0].look_id == "c01"
    assert report.items[0].seed == 7
    assert report.items[0].frames == 119
    assert report.items[0].duration == pytest.approx(4.96)
    assert report.totals.generated == 1
    assert report.totals.bytes == 5
    assert json.loads(report.model_dump_json())["status"] == "done"


def test_reports_keep_the_rented_server_when_every_item_failed(tmp_path: Path) -> None:
    artifact = _artifact(
        tmp_path,
        status="failed",
        error="сервер умер",
        meta={"server": {"contract_id": "42", "ip": "127.0.0.1"}},
    )
    failure = ItemFailure(artifact.item, artifact.profile, "layer", "сервер умер")
    result = PipelineResult(
        layers=(
            LayerOutcome(
                layer="layer",
                artifacts=(),
                failures=(failure,),
                produced=(artifact,),
            ),
        ),
        artifacts=(),
        failures=(failure,),
    )
    layer = Layer("layer", "vast", (), ArtifactKind.VIDEO, ("mp4", "clef"))

    report = reports_module.build_report(
        SimpleNamespace(name="dating/profile_2"),
        (layer,),
        _context(tmp_path),
        result,
    )

    assert report.status is RunStatus.FAILED
    assert report.server is not None
    assert report.server.contract_id == "42"
    assert report.totals.generated == 0
    assert report.totals.failed == 1


def test_pipeline_context_rejects_an_unsafe_run_id(tmp_path: Path) -> None:
    with pytest.raises(GeneratorError, match="run_id"):
        PipelineContext(
            profiles=(SimpleNamespace(name="dating/profile_2"),),
            count=1,
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            run_id="../../escape",
        )
