"""Plan and ComfyUI graph of one overlay batch.

The plan is deterministic: the same prompts always give the same seeds and
file names, so a rerun after a crash continues the same batch instead of
producing a second set of clips. The graph is the shared overlay graph
without the audio branch; only size, prompt, seed, frame rate and output
prefix are edited.
"""

from __future__ import annotations

import copy
import hashlib
import random
from collections.abc import Callable
from typing import Any

from content_factory.schemas.generator.overlay import OverlayPlan, OverlayPlanItem
from content_factory.services.generator.overlay_profiles import OverlayProfile
from content_factory.services.generator.plan import attempt_seed
from content_factory.services.generator.workflow import _update

_JITTER_STRIDE = 4
_MAX_SEED = 2**48


def plan_overlay(profile: OverlayProfile, run_id: str) -> OverlayPlan:
    """Build the deterministic plan of an overlay profile.

    Args:
        profile: Loaded overlay profile.
        run_id: Identifier of the run used in reports and the server journal.

    Returns:
        One item per prompt with a unique seed and a stable file name.
    """
    randomizer = random.Random(_seed_from(profile.prompts))
    seeds = _unique_seeds(profile.count, randomizer)
    items = tuple(
        OverlayPlanItem(
            seq=index,
            filename=f"{profile.slug}_{index:03d}.mp4",
            prompt=profile.prompts[index - 1],
            seed=seeds[index - 1],
            frames=_jitter_frames(profile.frames, profile.frames_jitter, randomizer),
        )
        for index in range(1, profile.count + 1)
    )
    return OverlayPlan(
        profile=profile.name,
        run_id=run_id,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        items=items,
        seconds=profile.seconds,
        max_cost=profile.max_cost,
    )


def build_overlay_workflow(
    profile: OverlayProfile,
    item: OverlayPlanItem,
    seed: int,
) -> dict[str, Any]:
    """Build the ComfyUI graph of one overlay clip.

    Args:
        profile: Loaded overlay profile with the shared graph.
        item: Planned clip with prompt, length and file name.
        seed: Seed of this attempt.

    Returns:
        Fresh graph ready to post to ComfyUI.

    Raises:
        ProfileError: The shared graph has no edited node or its inputs are
            not an object.
    """
    graph: dict[str, Any] = copy.deepcopy(dict(profile.workflow))
    _update(
        graph,
        "5",
        {"width": profile.width, "height": profile.height, "length": item.frames},
    )
    _update(graph, "8", {"text": item.prompt})
    if profile.negative_prompt:
        _update(graph, "9", {"text": profile.negative_prompt})
    sampler: dict[str, Any] = {"seed": seed}
    if profile.cfg is not None:
        sampler["cfg"] = profile.cfg
    _update(graph, "11", sampler)
    _update(graph, "10", {"frame_rate": profile.fps})
    _update(
        graph,
        "15",
        {
            "frame_rate": profile.fps,
            "filename_prefix": item.filename.removesuffix(".mp4"),
            "save_metadata": False,
        },
    )
    return graph


def overlay_builder(profile: OverlayProfile) -> Callable[[OverlayPlanItem, int], dict[str, Any]]:
    """Return a builder that gives every attempt a new seed."""

    def build(item: OverlayPlanItem, attempt: int) -> dict[str, Any]:
        return build_overlay_workflow(profile, item, attempt_seed(item.seed, attempt))

    return build


def _seed_from(prompts: tuple[str, ...]) -> int:
    """Derive a stable random seed from the prompt list."""
    digest = hashlib.sha256("\n".join(prompts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _unique_seeds(count: int, rng: random.Random) -> list[int]:
    """Return *count* different seeds in ``1..2**48-1``."""
    seeds: list[int] = []
    seen: set[int] = set()
    while len(seeds) < count:
        seed = rng.randrange(1, _MAX_SEED)
        if seed not in seen:
            seen.add(seed)
            seeds.append(seed)
    return seeds


def _jitter_frames(base: int, jitter: int, rng: random.Random) -> int:
    """Return a length of ``base ± steps*4`` frames, never above zero."""
    steps = max(0, int(jitter)) // _JITTER_STRIDE
    if steps == 0:
        return base
    return max(1, base + _JITTER_STRIDE * rng.randint(-steps, steps))


__all__ = ["build_overlay_workflow", "overlay_builder", "plan_overlay"]
