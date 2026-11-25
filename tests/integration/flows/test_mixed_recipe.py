"""Flow: one profile with a video and a photo chain publishes both."""

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
from content_factory.constants import CONTENT_DIR
from content_factory.flows.generate_batch.flow import generate_batch
from content_factory.schemas.generator.run import (
    RunItem,
    RunReport,
    RunStatus,
    RunStep,
    RunTotals,
)
from content_factory.services import generator as generator_service
from content_factory.services.production import registry
from content_factory.services.production import vast as vast_module
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


class _Writer:
    """Text model port returning one fixed card answer."""

    def write(self, system_prompt: str) -> str:
        del system_prompt
        return "<result><block>Первая строка</block><block>Пиши мне</block></result>"


def _renderer(profile: Any, overlay: Any) -> Image.Image:
    del overlay
    return Image.new("RGBA", (profile.width, profile.height), (0, 0, 0, 0))


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))


def test_mixed_recipe_publishes_video_and_photo_in_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(
        tmp_path / "profiles",
        width=544,
        height=960,
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["media", "clef"]},
                {"producer": "card", "type": "photo"},
            ]
        },
    )
    published: list[str] = []

    def fake_generate_batch(**kwargs: Any) -> SimpleNamespace:
        settings: GeneratorSettings = kwargs["settings"]
        run_id: str = kwargs["run_id"]
        name = f"dating_profile_2_{run_id}_001.mp4"
        run_dir = settings.output_dir / "ru" / "dating_profile_2" / run_id
        (run_dir / "final").mkdir(parents=True)
        (run_dir / "final" / name).write_bytes(b"mp4")
        stock = tmp_path / CONTENT_DIR / "dating" / "ru" / name
        stock.parent.mkdir(parents=True)
        stock.write_bytes(b"mp4")
        published.append(str(stock))
        report = RunReport(
            run_id=run_id,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            status=RunStatus.DONE,
            profiles=["dating/profile_2"],
            count=1,
            production=[RunStep(producer="vast", type="video", qa=["media", "clef"])],
            items=[
                RunItem(
                    file=name,
                    profile="dating/profile_2",
                    look_id="c01",
                    seed=7,
                    frames=119,
                    duration=4.96,
                    status=RunStatus.DONE,
                )
            ],
            totals=RunTotals(generated=1, bytes=3),
        )
        (run_dir / "run.json").write_text(report.model_dump_json(), encoding="utf-8")
        return SimpleNamespace(run_id=run_id, generated=1, failed=0, run_dirs=(run_dir,))

    monkeypatch.setattr(vast_module, "generate_batch", fake_generate_batch)
    monkeypatch.setattr(generator_service, "check_video", lambda *args, **kwargs: None)
    registry.register(
        "card",
        lambda: CardProducer(writer=_Writer(), renderer=_renderer),
    )
    try:
        outcome = generate_batch.fn(profiles="dating/profile_2", count=1)
    finally:
        registry.reset()

    assert outcome.count("run=") == 1
    assert "status=done generated=2 failed=0" in outcome
    report_path = next((tmp_path / "generated").rglob("run.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["production"] == [
        {"producer": "vast", "type": "video", "qa": ["media", "clef"]},
        {"producer": "card", "type": "photo", "qa": ["image"]},
    ]
    assert [item["type"] for item in report["items"]] == ["video", "photo"]
    assert [item["status"] for item in report["items"]] == ["done", "done"]
    photo = (
        tmp_path / CONTENT_DIR / "dating" / "ru" / f"dating_profile_2_{report['run_id']}_001.jpg"
    )
    video = (
        tmp_path / CONTENT_DIR / "dating" / "ru" / f"dating_profile_2_{report['run_id']}_001.mp4"
    )
    assert published == [str(video)]
    assert photo.is_file()
    assert video.is_file()
