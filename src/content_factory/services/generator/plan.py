"""Batch plan: which card, seed and length every video of the run gets."""

from __future__ import annotations

import random
from dataclasses import dataclass

from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.services.generator.profiles import _Look, _Profile

_JITTER_STRIDE = 4
_MAX_SEED = 2**48


@dataclass(frozen=True, slots=True)
class _PlannedItem:
    """One video of the batch before generation."""

    profile: str
    seq: int
    look: _Look
    seed: int
    frames: int
    filename: str


def plan_batch(
    profile: _Profile,
    count: int,
    run_id: str,
    *,
    rng: random.Random | None = None,
) -> tuple[_PlannedItem, ...]:
    """Build the plan of one profile: cards, seeds and frame lengths.

    Cards do not repeat inside the batch while there are enough of them and
    go in a circle after that. Seeds are unique inside the batch. Every
    length differs from ``profile.frames`` by at most ``frames_jitter`` and
    keeps the ``_JITTER_STRIDE`` step relative to the base.

    Args:
        profile: Loaded profile.
        count: Number of videos to plan, at least one.
        run_id: Identifier of the run used in file names.
        rng: Random source; tests pass a seeded instance.

    Returns:
        Planned items in production order.

    Raises:
        GeneratorError: ``count`` is not a positive integer.
        ProfileError: The batch asks for more than one video while the
            profile has only one appearance card.
    """
    validate_count(count)
    if count > 1 and len(profile.looks) == 1:
        raise ProfileError(
            f"в профиле {profile.name} одна карточка: нельзя собрать {count} роликов без повтора"
        )
    randomizer = rng or random.Random()  # noqa: S311 — seeds guard variety, not secrets
    looks = _pick_looks(profile.looks, count, randomizer)
    seeds = _unique_seeds(count, randomizer)
    slug = profile.name.replace("/", "_")
    return tuple(
        _PlannedItem(
            profile=profile.name,
            seq=index,
            look=looks[index - 1],
            seed=seeds[index - 1],
            frames=_jitter_frames(profile.frames, profile.frames_jitter, randomizer),
            filename=f"{slug}_{run_id}_{index:03d}.mp4",
        )
        for index in range(1, count + 1)
    )


def validate_count(count: int) -> None:
    """Reject a batch size that is not a positive integer.

    Raises:
        GeneratorError: ``count`` is a bool, a non-integer or not positive.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise GeneratorError(f"count должен быть целым числом больше нуля, получено {count!r}")


def _jitter_frames(base: int, jitter: int, rng: random.Random) -> int:
    """Return a length of ``base ± steps*4`` frames.

    Args:
        base: Base length of the profile in frames.
        jitter: Declared jitter in frames; the real step is 4 frames.
        rng: Random source.

    Returns:
        Video length that stays inside the profile window.
    """
    steps = max(0, int(jitter)) // _JITTER_STRIDE
    if steps == 0:
        return base
    return base + _JITTER_STRIDE * rng.randint(-steps, steps)


def _pick_looks(looks: tuple[_Look, ...], count: int, rng: random.Random) -> list[_Look]:
    """Pick cards without repeats inside one batch."""
    if count <= len(looks):
        return rng.sample(list(looks), count)
    order = list(looks)
    rng.shuffle(order)
    return [order[index % len(order)] for index in range(count)]


def _unique_seeds(count: int, rng: random.Random) -> list[int]:
    """Return *count* different seeds."""
    seeds: list[int] = []
    seen: set[int] = set()
    while len(seeds) < count:
        seed = rng.randrange(1, _MAX_SEED)
        if seed not in seen:
            seen.add(seed)
            seeds.append(seed)
    return seeds
