"""ComfyUI graph assembly from the shared LTX template."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator.plan import plan_batch
from content_factory.services.generator.profiles import load_profile
from content_factory.services.generator.workflow import build_workflow


def _item(tmp_path: Path):
    profile = fixture_profile(tmp_path / "profiles", looks=20)
    return profile, plan_batch(profile, 1, "run", rng=random.Random(1))[0]


def test_nodes_receive_size_prompt_seed_and_output(tmp_path: Path) -> None:
    profile, item = _item(tmp_path)

    graph = build_workflow(profile, item, "a woman at home")

    assert graph["5"]["inputs"]["width"] == profile.width
    assert graph["5"]["inputs"]["height"] == profile.height
    assert graph["5"]["inputs"]["length"] == item.frames
    assert graph["6"]["inputs"]["frames_number"] == item.frames
    assert graph["6"]["inputs"]["frame_rate"] == profile.fps
    assert graph["8"]["inputs"]["text"] == "a woman at home"
    assert graph["9"]["inputs"]["text"] == profile.negative_prompt
    assert graph["10"]["inputs"]["frame_rate"] == profile.fps
    assert graph["11"]["inputs"]["seed"] == item.seed
    assert graph["15"]["inputs"]["frame_rate"] == profile.fps
    assert graph["15"]["inputs"]["filename_prefix"] == item.filename.removesuffix(".mp4")


def test_template_is_not_mutated(tmp_path: Path) -> None:
    profile, item = _item(tmp_path)
    before = profile.workflow["8"]["inputs"]["text"]

    build_workflow(profile, item, "prompt")

    assert profile.workflow["8"]["inputs"]["text"] == before


def test_missing_node_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    profile = fixture_profile(root, looks=20)
    broken = dict(profile.workflow)
    broken.pop("11")

    broken_profile = replace(profile, workflow=broken)
    item = plan_batch(profile, 1, "run", rng=random.Random(1))[0]

    with pytest.raises(ProfileError, match="11"):
        build_workflow(broken_profile, item, "prompt")


def test_shared_workflow_is_loaded_from_shared_dir(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    fixture_profile(root, looks=20)

    loaded = load_profile("dating/profile_2", root)

    assert "15" in loaded.workflow
