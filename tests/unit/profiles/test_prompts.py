"""Prompt files and appearance card overrides of a generator profile."""

from __future__ import annotations

import random
import stat
from pathlib import Path

import pytest
import yaml
from tests.unit.generator.helpers import make_profile

from content_factory.exceptions.errors import ProfileStorageError, ProfileValidationError
from content_factory.exceptions.generator import ProfileError
from content_factory.utils import prompts


def _profile_dir(tmp_path: Path) -> Path:
    return make_profile(tmp_path / "profiles")


def test_template_falls_back_to_legacy_video_prompt(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    (directory / "prompt_template.txt").unlink()
    (directory / "video_prompt.txt").write_text("legacy {age}", encoding="utf-8")

    filename, text = prompts.read_template_file(directory)

    assert filename == "video_prompt.txt"
    assert text == "legacy {age}"


def test_empty_template_is_rejected(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    (directory / "prompt_template.txt").write_text("  \n", encoding="utf-8")

    with pytest.raises(ProfileError, match="пустой промпт"):
        prompts.read_template_file(directory)


def test_missing_text_prompt_is_rejected(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    (directory / "text_prompt.txt").unlink()

    with pytest.raises(ProfileError, match="нет text_prompt.txt"):
        prompts.read_text_prompt_file(directory)


def test_missing_override_file_reads_as_empty(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)

    assert prompts.read_look_overrides(directory) == {}


def test_look_overrides_are_read_and_written_with_the_original_mode(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    path = directory / prompts.OVERRIDES_FILENAME
    path.write_text("prompts: {}\n", encoding="utf-8")
    path.chmod(0o640)

    prompts.write_look_overrides(directory, {"c02": "second", "c01": "first"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "prompts": {"c01": "first", "c02": "second"}
    }
    assert prompts.read_look_overrides(directory) == {"c01": "first", "c02": "second"}
    assert list(directory.glob(f".{prompts.OVERRIDES_FILENAME}.*.tmp")) == []


def test_broken_override_file_is_reported(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    (directory / prompts.OVERRIDES_FILENAME).write_text("prompts: [", encoding="utf-8")

    with pytest.raises(ProfileError, match="overrides"):
        prompts.read_look_overrides(directory)


def test_override_value_must_be_a_non_empty_string(tmp_path: Path) -> None:
    directory = _profile_dir(tmp_path)
    (directory / prompts.OVERRIDES_FILENAME).write_text("prompts: {c01: 42}\n", encoding="utf-8")

    with pytest.raises(ProfileError, match="c01"):
        prompts.read_look_overrides(directory)


def test_failed_replace_keeps_the_old_file_and_removes_the_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = _profile_dir(tmp_path)
    path = directory / "prompt_template.txt"
    original = path.read_text(encoding="utf-8")

    def fail(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(ProfileStorageError):
        prompts.write_text_file(path, "new prompt")

    assert path.read_text(encoding="utf-8") == original
    assert list(directory.glob(".prompt_template.txt.*.tmp")) == []


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_blank_prompt_text_is_rejected(text: str) -> None:
    with pytest.raises(ProfileValidationError, match="пустым"):
        prompts.validate_prompt_text(text)


def test_prompt_text_over_the_limit_is_rejected() -> None:
    with pytest.raises(ProfileValidationError, match="20000"):
        prompts.validate_prompt_text("x" * 20_001)


def test_prompt_text_keeps_the_original_whitespace() -> None:
    text = "  first line\nsecond line  "

    assert prompts.validate_prompt_text(text) == text


def test_age_phrases_cover_the_profile_window() -> None:
    assert prompts.age_text(27) == "in her late twenties"
    assert prompts.age_text(30) == "around thirty"
    assert prompts.age_text(31) == "around thirty"
    assert prompts.age_text(32) == "in her early thirties"
    assert prompts.age_text(34) == "in her mid-thirties"
    assert prompts.age_text(40) == "in her late thirties"
    assert prompts.age_text("unknown") == ""


def test_render_prompt_fills_placeholders_and_spin_options() -> None:
    text = prompts.render_prompt(
        "A {age}-year-old {age_text}, shot in {day|evening} light.",
        {"age": 31},
        random.Random(0),
    )

    assert "{age}" not in text
    assert "{age_text}" not in text
    assert "around thirty" in text
    assert "{day|evening}" not in text
    assert ("day" in text) != ("evening" in text)
