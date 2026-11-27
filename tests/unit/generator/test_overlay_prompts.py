"""Overlay prompt validation and near-duplicate detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator import overlay_prompts
from content_factory.services.generator.overlay_prompts import (
    BLOCK_RATIO,
    blocking,
    check_prompts,
    load_existing,
    prompt_body,
    token_set_ratio,
    validate_prompts,
)

_TAIL = " on pure black background, slow motion, no camera movement, no people, no text"


def test_prompt_body_cuts_the_shared_tail_and_punctuation() -> None:
    prompt = "Dense golden glitter, thousands of sparkles" + _TAIL

    assert prompt_body(prompt) == "dense golden glitter thousands of sparkles"


def test_reordered_words_have_the_same_body() -> None:
    left = "dense golden glitter filling the frame" + _TAIL
    right = "golden dense glitter filling the frame" + _TAIL

    assert token_set_ratio(prompt_body(left), prompt_body(right)) == pytest.approx(1.0)


def test_near_duplicate_candidates_are_blocking() -> None:
    left = "Dense golden glitter filling the entire frame, thousands of tiny sparkles" + _TAIL
    right = "Dense golden glitter filling the whole frame, thousands of tiny sparkles" + _TAIL

    conflicts = check_prompts([left, right], {})

    assert conflicts
    assert conflicts[0].ratio >= BLOCK_RATIO
    assert blocking(conflicts)
    assert conflicts[0].other_profile == "<новый>"


def test_different_candidates_do_not_conflict() -> None:
    left = "Magnetic ferrofluid beads spiking into needles" + _TAIL
    right = "Pale green lichen fragments tumbling in a cloud" + _TAIL

    assert check_prompts([left, right], {}) == ()


def test_existing_prompts_are_checked_with_their_line() -> None:
    prompt = "Dense golden glitter filling the entire frame, thousands of tiny sparkles" + _TAIL
    existing = {"profile_1": (prompt,)}

    conflicts = check_prompts([prompt], existing)

    assert conflicts[0].other_profile == "profile_1"
    assert conflicts[0].other_line == 1
    assert conflicts[0].blocking


def test_validate_accepts_distinct_prompts() -> None:
    prompts = [f"Dense unique particle effect {index}" + _TAIL for index in range(1, 11)]

    validate_prompts(prompts, 10)


def test_validate_rejects_wrong_count() -> None:
    with pytest.raises(ProfileError, match="нужно ровно 10"):
        validate_prompts(["one" + _TAIL], 10)


def test_validate_rejects_missing_tail() -> None:
    with pytest.raises(ProfileError, match="хвоста"):
        validate_prompts(["Dense particles everywhere"], 1)


def test_validate_rejects_non_latin_text() -> None:
    with pytest.raises(ProfileError, match="латиница"):
        validate_prompts(["Плотные частицы" + _TAIL], 1)


def test_validate_rejects_exact_duplicate() -> None:
    prompt = "Dense golden glitter" + _TAIL

    with pytest.raises(ProfileError, match="повтор"):
        validate_prompts([prompt, prompt], 2)


def test_load_existing_skips_comments_and_excluded_profile(tmp_path: Path) -> None:
    root = tmp_path / "profiles" / "overlay"
    for name, prompt in (("profile_1", "One" + _TAIL), ("profile_2", "Two" + _TAIL)):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "backgrounds.txt").write_text(
            f"# comment\n\n{prompt}\n",
            encoding="utf-8",
        )

    existing = load_existing(root, exclude="profile_2")

    assert existing == {"profile_1": ("One" + _TAIL,)}


def test_warning_threshold_is_reported_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_prompts, "WARN_RATIO", 0.5)
    monkeypatch.setattr(overlay_prompts, "BLOCK_RATIO", 0.99)
    prompt = "Dense golden glitter filling the entire frame, thousands of tiny sparkles" + _TAIL
    existing = {
        "profile_1": ("Golden glitter falling through the frame, thousands of sparkles" + _TAIL,)
    }

    conflicts = check_prompts([prompt], existing)

    assert conflicts
    assert not conflicts[0].blocking
    assert not blocking(conflicts)
