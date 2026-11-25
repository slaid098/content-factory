"""Card producer: photo posts from the profile text card."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from tests.unit.generator.helpers import fixture_profile, make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, OverlayError, TextError
from content_factory.services.generator import publish
from content_factory.services.production import card as card_module
from content_factory.services.production import qa as production_qa
from content_factory.services.production.card import CardProducer, check_image

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"
_SLUG = "dating_profile_2"
_ANSWER = "<result><block>Первая строка\nВторая строка</block><block>Пиши мне</block></result>"
_CAPTION = "Первая строка\nВторая строка\nПиши мне"


class _Writer:
    """Text model port that returns one fixed card answer."""

    def __init__(self, answer: str = _ANSWER) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def write(self, system_prompt: str) -> str:
        self.prompts.append(system_prompt)
        return self.answer


def _renderer(profile: Any, overlay: Any) -> Image.Image:
    del overlay
    return Image.new("RGBA", (profile.width, profile.height), (0, 0, 0, 0))


def _settings(tmp_path: Path) -> GeneratorSettings:
    return GeneratorSettings(output_dir=tmp_path / "generated", safety_enabled=False)


def _producer(
    *,
    writer: _Writer | None = None,
    renderer: Any = _renderer,
) -> CardProducer:
    return CardProducer(writer=writer or _Writer(), renderer=renderer, now=_NOW)


def _photo_profile(tmp_path: Path) -> None:
    make_profile(
        tmp_path / "profiles",
        width=544,
        height=960,
        extra={"production": [{"producer": "card", "type": "photo"}]},
    )


def _run_dir(tmp_path: Path) -> Path:
    return tmp_path / "generated" / "ru" / _SLUG / _RUN_ID


def _stock_dir(tmp_path: Path) -> Path:
    return tmp_path / "content" / "dating" / "ru"


def _post_name(seq: int) -> str:
    return f"{_SLUG}_{_RUN_ID}_{seq:03d}"


def test_card_produces_two_photo_posts_with_their_text(tmp_path: Path) -> None:
    _photo_profile(tmp_path)
    writer = _Writer()

    produced = _producer(writer=writer).produce(
        SimpleNamespace(name="dating/profile_2"), 2, tmp_path, _settings(tmp_path)
    )

    assert produced.run_id == _RUN_ID
    assert produced.generated == 2
    assert produced.failed == 0
    assert produced.run_dirs == (_run_dir(tmp_path),)
    assert len(writer.prompts) == 2
    for seq in (1, 2):
        name = _post_name(seq)
        final = _run_dir(tmp_path) / "final" / f"{name}.jpg"
        stock = _stock_dir(tmp_path) / f"{name}.jpg"
        assert final.is_file()
        assert stock.is_file()
        assert stock.stat().st_ino == final.stat().st_ino
        with Image.open(stock) as image:
            assert image.format == "JPEG"
            assert image.size == (544, 960)
            assert image.getpixel((0, 0)) == pytest.approx((255, 241, 220), abs=3)
        sidecar = json.loads(stock.with_suffix(".json").read_text(encoding="utf-8"))
        assert sidecar == {"text": _CAPTION}

    report = json.loads((_run_dir(tmp_path) / "run.json").read_text(encoding="utf-8"))
    assert report["status"] == "done"
    assert report["production"] == [{"producer": "card", "type": "photo", "qa": ["image"]}]
    assert report["count"] == 2
    assert [item["file"] for item in report["items"]] == [
        f"{_post_name(1)}.jpg",
        f"{_post_name(2)}.jpg",
    ]
    for item in report["items"]:
        assert item["profile"] == "dating/profile_2"
        assert item["status"] == "done"
        assert item["error"] is None
        assert item["producer"] == "card"
        assert item["type"] == "photo"
        assert item["qa"] == ["image"]
        assert item["look_id"] is None
        assert item["seed"] is None
        assert item["frames"] is None
    assert report["totals"]["generated"] == 2
    assert report["totals"]["failed"] == 0
    assert report["totals"]["bytes"] == sum(
        (_run_dir(tmp_path) / "final" / f"{_post_name(seq)}.jpg").stat().st_size for seq in (1, 2)
    )


@pytest.mark.parametrize("count", [0, -1])
def test_invalid_count_fails_before_the_text_model_and_render(tmp_path: Path, count: int) -> None:
    _photo_profile(tmp_path)
    writer = _Writer()
    renders: list[str] = []

    def render(profile: Any, overlay: Any) -> Image.Image:
        del profile, overlay
        renders.append("render")
        return Image.new("RGB", (1, 1))

    with pytest.raises(GeneratorError, match="count"):
        _producer(writer=writer, renderer=render).produce(
            SimpleNamespace(name="dating/profile_2"), count, tmp_path, _settings(tmp_path)
        )

    assert writer.prompts == []
    assert renders == []


def test_text_failure_marks_the_item_failed_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _photo_profile(tmp_path)

    def fail(profile: Any, writer: Any) -> None:
        del profile, writer
        raise TextError("модель не ответила")

    monkeypatch.setattr(card_module, "generate_overlay", fail)

    produced = _producer().produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert produced.generated == 0
    assert produced.failed == 1
    assert not _stock_dir(tmp_path).exists()
    report = json.loads((_run_dir(tmp_path) / "run.json").read_text(encoding="utf-8"))
    item = report["items"][0]
    assert item["status"] == "failed"
    assert "модель не ответила" in item["error"]
    assert report["totals"] == {"generated": 0, "failed": 1, "bytes": 0}
    assert not (_run_dir(tmp_path) / "final" / f"{_post_name(1)}.jpg").exists()


def test_broken_render_marks_the_item_failed_without_publication(tmp_path: Path) -> None:
    _photo_profile(tmp_path)

    def render(profile: Any, overlay: Any) -> Image.Image:
        del profile, overlay
        raise OverlayError("карточка не нарисовалась")

    produced = _producer(renderer=render).produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert produced.generated == 0
    assert produced.failed == 1
    assert not _stock_dir(tmp_path).exists()
    report = json.loads((_run_dir(tmp_path) / "run.json").read_text(encoding="utf-8"))
    assert "карточка не нарисовалась" in report["items"][0]["error"]


def test_image_check_failure_marks_the_item_failed_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _photo_profile(tmp_path)
    monkeypatch.setattr(card_module, "check_image", lambda path, width, height: "битый jpeg")

    produced = _producer().produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert produced.generated == 0
    assert produced.failed == 1
    assert not _stock_dir(tmp_path).exists()
    report = json.loads((_run_dir(tmp_path) / "run.json").read_text(encoding="utf-8"))
    assert report["items"][0]["error"] == "битый jpeg"


def test_image_check_runs_even_when_the_recipe_names_only_another_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production_qa.register("custom", production_qa.QA_FINAL)
    make_profile(
        tmp_path / "profiles",
        width=544,
        height=960,
        extra={"production": [{"producer": "card", "type": "photo", "qa": ["custom"]}]},
    )
    calls: list[str] = []

    def failing(path: Path, width: int, height: int) -> str:
        del width, height
        calls.append(str(path))
        return "битый jpeg"

    monkeypatch.setattr(card_module, "check_image", failing)

    produced = _producer().produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert len(calls) == 1
    assert produced.failed == 1
    assert not _stock_dir(tmp_path).exists()
    report = json.loads((_run_dir(tmp_path) / "run.json").read_text(encoding="utf-8"))
    assert report["items"][0]["qa"] == ["custom"]
    assert report["items"][0]["error"] == "битый jpeg"


@pytest.mark.parametrize("existing", ["jpg", "json"])
def test_existing_stock_file_leaves_no_half_pair(tmp_path: Path, existing: str) -> None:
    _photo_profile(tmp_path)
    stock_dir = _stock_dir(tmp_path)
    stock_dir.mkdir(parents=True)
    name = _post_name(1)
    (stock_dir / f"{name}.{existing}").write_text("old", encoding="utf-8")

    produced = _producer().produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert produced.failed == 1
    if existing == "jpg":
        assert not (stock_dir / f"{name}.json").exists()
        assert (stock_dir / f"{name}.jpg").read_text(encoding="utf-8") == "old"
    else:
        assert not (stock_dir / f"{name}.jpg").exists()
        assert (stock_dir / f"{name}.json").read_text(encoding="utf-8") == "old"


def test_sidecar_publication_failure_removes_the_published_jpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _photo_profile(tmp_path)
    real_link = publish.os.link

    def link(source: Path, destination: Path, *args: object, **kwargs: object) -> None:
        if str(destination).endswith(".json"):
            raise OSError("sidecar link failed")
        real_link(source, destination, *args, **kwargs)

    def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> None:
        del source, destination, args, kwargs
        raise OSError("copy failed")

    monkeypatch.setattr(publish.os, "link", link)
    monkeypatch.setattr(publish.shutil, "copy2", copy)

    produced = _producer().produce(
        SimpleNamespace(name="dating/profile_2"), 1, tmp_path, _settings(tmp_path)
    )

    assert produced.failed == 1
    stock_dir = _stock_dir(tmp_path)
    assert list(stock_dir.glob(f"{_post_name(1)}.*")) == []


def test_publish_photo_places_the_jpeg_and_its_sidecar(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "post.jpg"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"jpeg")
    sidecar = final.with_suffix(".json")
    sidecar.write_text('{"text": "Текст"}', encoding="utf-8")

    destination = publish.publish_photo(profile, final, tmp_path)

    assert destination == tmp_path / "content" / "dating" / "ru" / "post.jpg"
    assert destination.read_bytes() == b"jpeg"
    assert destination.with_suffix(".json").read_text(encoding="utf-8") == '{"text": "Текст"}'
    assert final.is_file()
    assert sidecar.is_file()


def test_check_image_accepts_a_matching_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "post.jpg"
    Image.new("RGB", (544, 960), "white").save(path, format="JPEG", quality=90)

    assert check_image(path, 544, 960) is None


def test_check_image_rejects_another_size_and_broken_files(tmp_path: Path) -> None:
    small = tmp_path / "small.jpg"
    Image.new("RGB", (10, 10), "white").save(small, format="JPEG")

    assert "размер" in str(check_image(small, 544, 960))

    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    assert check_image(broken, 544, 960) is not None

    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert "пуст" in str(check_image(empty, 544, 960))
