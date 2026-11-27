"""Overlay profile loading and creation."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.generator.helpers import OVERLAY_GRAPH, make_overlay_profile, overlay_prompts

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator.overlay_profiles import (
    create_overlay_profile,
    load_overlay_profile,
    next_profile_name,
)

_TAIL = " on pure black background, slow motion, no camera movement, no people, no text"


def _prompts(count: int = 10) -> list[str]:
    return [
        f"Unique particle scene number {index} fills the frame" + _TAIL
        for index in range(1, count + 1)
    ]


def test_create_and_load_profile(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_overlay_profile(root, "overlay/profile_1")
    prompts = _prompts()

    directory = create_overlay_profile(root, "overlay/profile_2", prompts)
    profile = load_overlay_profile("overlay/profile_2", root)

    assert directory == root / "overlay" / "profile_2"
    assert profile.name == "overlay/profile_2"
    assert profile.count == 10
    assert profile.prompts == tuple(prompts)
    assert profile.seconds == pytest.approx(89 / 18)
    assert profile.workflow == OVERLAY_GRAPH
    assert (directory / "video_prompt.txt").read_text(encoding="utf-8") == "{background}\n"


def test_create_rejects_existing_directory(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_overlay_profile(root, "overlay/profile_1")

    with pytest.raises(ProfileError, match="уже существует"):
        create_overlay_profile(root, "overlay/profile_1", _prompts())


def test_create_rejects_foreign_category(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="overlay/profile_N"):
        create_overlay_profile(tmp_path / "profiles", "dating/profile_2", _prompts())


def test_next_profile_name_counts_existing_profiles(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_overlay_profile(root, "overlay/profile_1")
    make_overlay_profile(root, "overlay/profile_7")

    assert next_profile_name(root) == "overlay/profile_8"


def test_load_rejects_prompt_count_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    directory = make_overlay_profile(root, "overlay/profile_1", count=2)
    (directory / "backgrounds.txt").write_text(
        "\n".join(overlay_prompts(1)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match="промптов 1"):
        load_overlay_profile("overlay/profile_1", root)


def test_load_rejects_missing_shared_workflow(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    directory = make_overlay_profile(root, "overlay/profile_1")
    (root / "_shared" / "workflows" / "workflow-overlay.json").unlink()

    with pytest.raises(ProfileError, match="overlay-workflow"):
        load_overlay_profile("overlay/profile_1", root)

    assert directory.is_dir()
