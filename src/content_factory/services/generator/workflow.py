"""ComfyUI graph assembly: dimensions, prompt, seed and output node."""

from __future__ import annotations

import copy
import random
import threading
from collections.abc import Callable
from typing import Any

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator import profiles
from content_factory.services.generator.plan import _PlannedItem, attempt_seed
from content_factory.services.generator.profiles import _Profile

_VIDEO_LATENT_NODE = "5"
_AUDIO_LATENT_NODE = "6"
_PROMPT_NODE = "8"
_NEGATIVE_NODE = "9"
_CONDITIONING_NODE = "10"
_SAMPLER_NODE = "11"
_OUTPUT_NODE = "15"


def build_workflow(
    profile: _Profile,
    item: _PlannedItem,
    prompt: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build the graph of one video from the profile template.

    Both latent nodes receive the same length: a desynchronized video and
    audio latent gives a broken file. The sampler receives *seed* or, when it
    is absent, the planned seed; the output node receives the file prefix and
    frame rate.

    Args:
        profile: Loaded profile with the graph template.
        item: Planned video with seed, length and file name.
        prompt: Final scene prompt.
        seed: Seed of this generation attempt; defaults to ``item.seed``.

    Returns:
        Fresh graph ready to be posted to ComfyUI.

    Raises:
        ProfileError: The template lacks one of the required nodes.
    """
    graph: dict[str, Any] = copy.deepcopy(dict(profile.workflow))
    prefix = item.filename.removesuffix(".mp4")
    _update(
        graph,
        _VIDEO_LATENT_NODE,
        {"width": profile.width, "height": profile.height, "length": item.frames},
    )
    _update(
        graph,
        _AUDIO_LATENT_NODE,
        {"frames_number": item.frames, "frame_rate": profile.fps},
    )
    _update(graph, _PROMPT_NODE, {"text": prompt})
    negative = profile.negative_prompt
    if negative:
        _update(graph, _NEGATIVE_NODE, {"text": negative})
    sampler: dict[str, Any] = {"seed": item.seed if seed is None else seed}
    if profile.cfg is not None:
        sampler["cfg"] = profile.cfg
    _update(graph, _SAMPLER_NODE, sampler)
    _update(graph, _CONDITIONING_NODE, {"frame_rate": profile.fps})
    _update(
        graph,
        _OUTPUT_NODE,
        {"frame_rate": profile.fps, "filename_prefix": prefix, "save_metadata": False},
    )
    return graph


def workflow_builder(
    profile: _Profile,
    rng: random.Random,
    lock: threading.Lock | None = None,
) -> Callable[[_PlannedItem, int], dict[str, Any]]:
    """Return a thread-safe workflow builder for one profile.

    The shared random source is guarded while a scene prompt is generated:
    several ComfyUI jobs call the builder from different threads.
    """
    guard = lock or threading.Lock()

    def build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
        with guard:
            prompt = profiles.build_prompt(profile, item.look, rng)
        return build_workflow(profile, item, prompt, seed=attempt_seed(item.seed, attempt))

    return build


def _update(graph: dict[str, Any], node_id: str, values: dict[str, Any]) -> None:
    """Merge *values* into the inputs of one node."""
    try:
        inputs = graph[node_id]["inputs"]
    except (KeyError, TypeError) as ex:
        raise ProfileError(f"в workflow нет узла {node_id}: {ex}") from ex
    if not isinstance(inputs, dict):
        raise ProfileError(f"у узла {node_id} в workflow нет словаря inputs")
    inputs.update(values)
