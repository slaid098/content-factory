"""Profile loading: required files, shared workflow and pre-rent checks."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.unit.generator.helpers import fixture_profile, make_profile

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator.profiles import (
    _age_text,
    _alpha,
    _float_or,
    _optional_int,
    _optional_number,
    _optional_str,
    _profile_name,
    build_prompt,
    load_profile,
    profile_dir,
    prompt_template,
    text_prompt,
)

_DELETE = object()


def _rewrite(root: Path, **changes: Any) -> None:
    path = root / "dating" / "profile_2" / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is _DELETE:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_profile_uses_shared_workflow_and_shared_font(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")

    assert profile.name == "dating/profile_2"
    assert profile.language == "ru"
    assert profile.niche == "dating"
    assert len(profile.looks) == 3
    assert profile.workflow["5"]["inputs"]["width"] == 1
    assert profile.font.name == "arialbd.ttf"
    assert profile.audio_tracks[0].name == "neutral_1.mp3"
    assert profile.negative_prompt == "blurry, low quality"


def test_profile_workflow_overrides_shared_one(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root, workflow=True)
    local = root / "dating" / "profile_2" / "workflow.json"
    payload = local.read_text(encoding="utf-8").replace('"width": 1', '"width": 777')
    local.write_text(payload, encoding="utf-8")

    profile = load_profile("dating/profile_2", root)

    assert profile.workflow["5"]["inputs"]["width"] == 777


def test_short_profile_name_is_rejected_when_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root, "dating/profile_2")
    make_profile(root, "other/profile_2")

    with pytest.raises(ProfileError, match="неоднозначно"):
        profile_dir("profile_2", root)


def test_missing_shared_workflow_fails_before_rent(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    (root / "_shared" / "workflows" / "video_gen_workflow.json").unlink()

    with pytest.raises(ProfileError, match="workflow"):
        load_profile("dating/profile_2", root)


def test_missing_music_fails_before_rent(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    (root / "dating" / "profile_2" / "audio" / "neutral_1.mp3").unlink()

    with pytest.raises(ProfileError, match="mp3"):
        load_profile("dating/profile_2", root)


def test_unsupported_renderer_fails_before_rent(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root, renderer="dossier")

    with pytest.raises(ProfileError, match="renderer"):
        load_profile("dating/profile_2", root)


def test_prompt_placeholders_and_spin_are_filled(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = fixture_profile(root)
    look = profile.looks[0]

    prompt = build_prompt(profile, look, random.Random(1))

    assert "{age}" not in prompt
    assert "{age_text}" not in prompt
    assert "dark brown" in prompt
    assert ("day" in prompt) != ("evening" in prompt)
    assert prompt_template(profile).startswith("A {age}")
    assert text_prompt(profile) == "Сгенерируй текст карточки."


def test_profile_dir_resolves_short_unique_names(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)

    assert profile_dir("dating/profile_2", root) == root / "dating" / "profile_2"
    assert profile_dir("profile_2", root) == root / "dating" / "profile_2"

    with pytest.raises(ProfileError, match="пустое имя"):
        profile_dir("  ", root)

    with pytest.raises(ProfileError, match="не найден"):
        profile_dir("dating/absent", root)

    with pytest.raises(ProfileError, match="не найден"):
        profile_dir("absent", root)


def test_missing_and_broken_profile_yaml_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    config = root / "dating" / "profile_2" / "profile.yaml"

    config.unlink()
    with pytest.raises(ProfileError, match="нет profile.yaml"):
        load_profile("dating/profile_2", root)

    config.write_text("a: [", encoding="utf-8")
    with pytest.raises(ProfileError, match="не удалось прочитать"):
        load_profile("dating/profile_2", root)

    config.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="должен быть словарём"):
        load_profile("dating/profile_2", root)


def test_missing_required_keys_are_reported(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    _rewrite(root, gpu=_DELETE)

    with pytest.raises(ProfileError, match="обязательных ключей"):
        load_profile("dating/profile_2", root)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("image", "  ", "пустой ключ"),
        ("count", "many", "должен быть числом"),
        ("count", 0, "больше нуля"),
        ("frames_jitter", _DELETE, "нет ключа"),
        ("frames_jitter", -1, "отрицательным"),
        ("max_cost", "free", "должен быть числом"),
        ("max_cost", 0, "больше нуля"),
        ("inet_up_cost", -0.1, "отрицательным"),
        ("cfg", "high", "должен быть числом"),
        ("block2_y", "top", "ожидалось число"),
        ("text_bg_alpha", "opaque", "непрозрачность"),
    ],
)
def test_invalid_profile_values_fail_before_rent(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    _rewrite(root, **{key: value})

    with pytest.raises(ProfileError, match=message):
        load_profile("dating/profile_2", root)


def test_prompt_files_are_required_and_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    profile = load_profile("dating/profile_2", root)
    directory = root / "dating" / "profile_2"

    (directory / "prompt_template.txt").unlink()
    legacy = directory / "video_prompt.txt"
    legacy.write_text("legacy prompt", encoding="utf-8")
    assert prompt_template(load_profile("dating/profile_2", root)) == "legacy prompt"

    legacy.write_text("  ", encoding="utf-8")
    with pytest.raises(ProfileError, match="пустой промпт"):
        prompt_template(profile)

    legacy.unlink()
    with pytest.raises(ProfileError, match="prompt_template"):
        prompt_template(profile)

    (directory / "text_prompt.txt").unlink()
    with pytest.raises(ProfileError, match="нет text_prompt.txt"):
        text_prompt(profile)

    (directory / "text_prompt.txt").write_text("  ", encoding="utf-8")
    with pytest.raises(ProfileError, match="пустой text_prompt.txt"):
        text_prompt(profile)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "нет looks.yaml"),
        ("looks: [", "не удалось прочитать"),
        ("looks: []", "не содержит карточек"),
        ("looks:\n  - just-a-string\n", "должна быть словарём"),
        ("looks:\n  - age: 30\n", "нет id"),
        ("looks:\n  - id: same\n  - id: same\n", "повтор id"),
    ],
)
def test_broken_looks_are_reported(tmp_path: Path, content: str | None, message: str) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    path = root / "dating" / "profile_2" / "looks.yaml"
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8")

    with pytest.raises(ProfileError, match=message):
        load_profile("dating/profile_2", root)


def test_audio_can_be_disabled_and_profile_font_wins(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root, audio=False)
    directory = root / "dating" / "profile_2"
    local_font = directory / "fonts" / "custom.ttf"
    local_font.parent.mkdir()
    local_font.write_bytes(b"font")
    _rewrite(root, audio=False, font="custom.ttf")

    profile = load_profile("dating/profile_2", root)

    assert profile.audio_tracks == ()
    assert profile.font == local_font.resolve()


def test_broken_workflow_json_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    path = root / "dating" / "profile_2" / "workflow.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(ProfileError, match="не удалось прочитать workflow"):
        load_profile("dating/profile_2", root)

    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ProfileError, match="JSON-объектом"):
        load_profile("dating/profile_2", root)


def test_age_phrases_cover_the_profile_window() -> None:
    assert _age_text(27) == "in her late twenties"
    assert _age_text(30) == "around thirty"
    assert _age_text(31) == "around thirty"
    assert _age_text(32) == "in her early thirties"
    assert _age_text(34) == "in her mid-thirties"
    assert _age_text(40) == "in her late thirties"
    assert _age_text("unknown") == ""


def test_optional_profile_values_are_normalized(tmp_path: Path) -> None:
    assert _optional_str(None) is None
    assert _optional_str("  ") is None
    assert _optional_str(" x ") == "x"
    assert _optional_int(None) is None
    assert _optional_int("5") == 5
    assert _optional_number(None) is None
    assert _optional_number("1.5") == 1.5
    assert _alpha(None) == 255
    assert _alpha("300") == 255
    assert _alpha("-1") == 0
    assert _float_or(None, 0.5) == 0.5
    assert _float_or("0.25", 0.5) == 0.25
    assert _profile_name(tmp_path / "outside", tmp_path / "root") == "outside"

    with pytest.raises(ProfileError, match="ожидалось число"):
        _optional_number("x")

    with pytest.raises(ProfileError, match="непрозрачность"):
        _alpha("x")


def test_overlay_hug_options_are_read(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", hug_text=True, max_text_width=0.7)

    assert profile.style.hug_text is True
    assert profile.style.max_text_width == 0.7


def test_bad_text_width_fails_before_rent(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", max_text_width=1.5)

    with pytest.raises(ProfileError, match="max_text_width"):
        load_profile("dating/profile_2", tmp_path / "profiles")
