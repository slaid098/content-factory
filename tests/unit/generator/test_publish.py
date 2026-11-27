"""Idempotent publication of finished videos and photo pairs."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import GeneratorError
from content_factory.services.generator import publish

_NAME = "dating_profile_2_20260101T000000Z_001"


def _stock(tmp_path: Path, name: str) -> Path:
    return tmp_path / "content" / "dating" / "ru" / name


def _video(tmp_path: Path) -> tuple[Path, Path]:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final" / f"{_NAME}.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final-video")
    return profile, final


def test_publishing_the_same_video_twice_is_accepted(tmp_path: Path) -> None:
    profile, final = _video(tmp_path)

    first = publish.publish_stock(profile, final, tmp_path)
    second = publish.publish_stock(profile, final, tmp_path)

    assert first == second == _stock(tmp_path, f"{_NAME}.mp4")
    assert first.read_bytes() == b"final-video"
    assert list(first.parent.iterdir()) == [first]
    assert final.is_file()


def test_existing_video_with_other_content_is_an_error(tmp_path: Path) -> None:
    profile, final = _video(tmp_path)
    destination = _stock(tmp_path, f"{_NAME}.mp4")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"someone else")

    with pytest.raises(GeneratorError, match="положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert destination.read_bytes() == b"someone else"


def test_publishing_a_photo_pair_twice_creates_no_duplicates(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final" / f"{_NAME}.jpg"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"jpeg")
    sidecar = final.with_suffix(".json")
    sidecar.write_text('{"text": "Текст"}', encoding="utf-8")

    first = publish.publish_photo(profile, final, tmp_path)
    second = publish.publish_photo(profile, final, tmp_path)

    assert first == second == _stock(tmp_path, f"{_NAME}.jpg")
    assert first.read_bytes() == b"jpeg"
    assert first.with_suffix(".json").read_text(encoding="utf-8") == '{"text": "Текст"}'
    assert sorted(path.name for path in first.parent.iterdir()) == [
        f"{_NAME}.jpg",
        f"{_NAME}.json",
    ]


def test_restart_between_the_text_and_the_photo_finishes_the_pair(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final" / f"{_NAME}.jpg"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"jpeg")
    sidecar = final.with_suffix(".json")
    sidecar.write_text('{"text": "Текст"}', encoding="utf-8")
    published_text = _stock(tmp_path, f"{_NAME}.json")
    published_text.parent.mkdir(parents=True)
    published_text.write_text('{"text": "Текст"}', encoding="utf-8")
    photo = _stock(tmp_path, f"{_NAME}.jpg")
    assert not photo.exists()

    destination = publish.publish_photo(profile, final, tmp_path)

    assert destination == photo
    assert photo.read_bytes() == b"jpeg"
    assert published_text.read_text(encoding="utf-8") == '{"text": "Текст"}'


def test_photo_with_other_content_at_the_stock_keeps_the_published_text(
    tmp_path: Path,
) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final" / f"{_NAME}.jpg"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"jpeg")
    sidecar = final.with_suffix(".json")
    sidecar.write_text('{"text": "Текст"}', encoding="utf-8")
    photo = _stock(tmp_path, f"{_NAME}.jpg")
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"someone else")
    text = _stock(tmp_path, f"{_NAME}.json")
    text.write_text('{"text": "Текст"}', encoding="utf-8")

    with pytest.raises(GeneratorError, match="положить в сток"):
        publish.publish_photo(profile, final, tmp_path)

    assert photo.read_bytes() == b"someone else"
    assert text.read_text(encoding="utf-8") == '{"text": "Текст"}'
