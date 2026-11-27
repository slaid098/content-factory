"""Overlay plan: deterministic seeds, frames and ComfyUI graph."""

from __future__ import annotations

from pathlib import Path

from tests.unit.generator.helpers import make_overlay_profile

from content_factory.services.generator.overlay_plan import (
    build_overlay_workflow,
    overlay_builder,
    plan_overlay,
)
from content_factory.services.generator.overlay_profiles import load_overlay_profile


def _profile(tmp_path: Path, name: str = "overlay/profile_1"):
    root = tmp_path / "profiles"
    make_overlay_profile(root, name)
    return load_overlay_profile(name, root)


def test_plan_is_deterministic_and_has_unique_seeds(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    first = plan_overlay(profile, "20260101T000000Z")
    second = plan_overlay(profile, "20260101T000000Z")

    assert first == second
    assert len(first.items) == profile.count
    assert len({item.seed for item in first.items}) == profile.count
    assert [item.filename for item in first.items] == [
        f"overlay_profile_1_{index:03d}.mp4" for index in range(1, 11)
    ]


def test_plan_keeps_prompt_order_and_length(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    plan = plan_overlay(profile, "20260101T000000Z")

    assert [item.prompt for item in plan.items] == list(profile.prompts)
    assert all(item.frames == profile.frames for item in plan.items)
    assert plan.seconds == profile.seconds
    assert plan.max_cost == profile.max_cost


def test_different_prompts_give_a_different_seed(tmp_path: Path) -> None:
    left = _profile(tmp_path, "overlay/profile_1")
    right_root = tmp_path / "profiles"
    right_dir = make_overlay_profile(right_root, "overlay/profile_2")
    (right_dir / "backgrounds.txt").write_text(
        "\n".join(
            f"Alternative particle scene number {index} fills the frame, slow motion, "
            "no camera movement, no people, no text on pure black background"
            for index in range(1, 11)
        )
        + "\n",
        encoding="utf-8",
    )
    right = load_overlay_profile("overlay/profile_2", right_root)

    assert plan_overlay(left, "run").items[0].seed != plan_overlay(right, "run").items[0].seed


def test_workflow_edits_size_prompt_seed_and_output_without_audio(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    item = plan_overlay(profile, "run").items[0]

    graph = build_overlay_workflow(profile, item, seed=123)

    assert graph["5"]["inputs"]["width"] == profile.width
    assert graph["5"]["inputs"]["length"] == item.frames
    assert graph["8"]["inputs"]["text"] == item.prompt
    assert graph["11"]["inputs"]["seed"] == 123
    assert graph["10"]["inputs"]["frame_rate"] == profile.fps
    assert graph["15"]["inputs"]["filename_prefix"] == item.filename.removesuffix(".mp4")
    assert "6" not in graph


def test_builder_changes_seed_on_every_attempt(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    item = plan_overlay(profile, "run").items[0]
    build = overlay_builder(profile)

    first = build(item, 1)
    second = build(item, 2)

    assert first["11"]["inputs"]["seed"] == item.seed
    assert second["11"]["inputs"]["seed"] != item.seed
