"""Producer and QA registries, and the Vast production adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.content.delivery import ContentType
from content_factory.services.production import ProducedBatch, Producer, ProfileRef, registry
from content_factory.services.production import protocol as production_protocol
from content_factory.services.production import qa as production_qa
from content_factory.services.production import vast as vast_module
from content_factory.services.production.vast import VastProducer


def test_production_door_exports_the_producer_port() -> None:
    assert Producer is production_protocol.Producer
    assert ProfileRef is production_protocol.ProfileRef
    assert ProducedBatch is production_protocol.ProducedBatch


def test_vast_is_the_builtin_producer() -> None:
    assert registry.available() == ("vast",)
    assert isinstance(registry.resolve("vast"), VastProducer)
    assert registry.resolve("vast").supported_types == frozenset({ContentType.VIDEO})


def test_registry_can_be_injected_and_reset() -> None:
    local = registry.Registry()
    assert local.available() == ()

    producer = SimpleNamespace(supported_types=frozenset({ContentType.VIDEO}))
    local.register("fake", lambda: producer)

    assert local.available() == ("fake",)
    assert local.resolve("fake") is producer
    assert local.resolve("fake") is producer

    local.reset()
    assert local.available() == ("vast",)


def test_registry_rejects_blank_and_unknown_names() -> None:
    local = registry.Registry()

    with pytest.raises(ProfileError, match="пустое имя"):
        local.register("  ", lambda: VastProducer())

    with pytest.raises(ProfileError, match="неизвестный producer 'unique': допустимы нет"):
        local.resolve("unique")


def test_process_registry_restores_builtins_after_injection() -> None:
    registry.register("fake", lambda: VastProducer())
    assert "fake" in registry.available()

    registry.reset()
    assert registry.available() == ("vast",)


def test_qa_builtins_have_their_pipeline_stages() -> None:
    assert production_qa.available() == ("clef", "media")
    assert production_qa.resolve("clef").stage == production_qa.QA_SOURCE
    assert production_qa.resolve("media").stage == production_qa.QA_FINAL
    assert production_qa.has(("media", "clef"), production_qa.QA_FINAL)
    assert not production_qa.has(("media",), production_qa.QA_SOURCE)


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


def test_vast_producer_maps_the_generator_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    result = SimpleNamespace(
        run_id="run-1",
        generated=2,
        failed=1,
        run_dirs=(tmp_path / "run",),
    )

    def fake_generate_batch(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return result

    monkeypatch.setattr(vast_module, "generate_batch", fake_generate_batch)
    settings = GeneratorSettings(output_dir=tmp_path, safety_enabled=False)

    produced = vast_module.VastProducer().produce(
        SimpleNamespace(name="dating/profile_2"), 3, tmp_path, settings
    )

    assert produced == ProducedBatch(
        run_id="run-1",
        generated=2,
        failed=1,
        run_dirs=(tmp_path / "run",),
    )
    assert calls == [
        {
            "profiles": "dating/profile_2",
            "count": 3,
            "data_dir": tmp_path,
            "settings": settings,
        }
    ]
