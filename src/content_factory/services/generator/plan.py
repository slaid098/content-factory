"""Batch plan: which card, seed and length every video of the run gets."""

from __future__ import annotations

import random
from dataclasses import dataclass

from loguru import logger

from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.services.generator.profiles import _Look, _Profile

_JITTER_STRIDE = 4
_MAX_SEED = 2**48
_SEED_MASK = 2**64 - 1
_SEED_STEP = 0x9E3779B97F4A7C15
_SEED_MIX_A = 0xBF58476D1CE4E5B9
_SEED_MIX_B = 0x94D049BB133111EB


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
    start_after: str | None = None,
) -> tuple[_PlannedItem, ...]:
    """Build the plan of one profile: cards, seeds and frame lengths.

    Cards go in the order of ``looks.yaml`` from the card that follows
    *start_after*, wrapping to the first one after the end of the list. When
    *start_after* is ``None`` or names a card that is not in the profile, the
    batch starts from the first card. Seeds are unique inside the batch. Every
    length differs from ``profile.frames`` by at most ``frames_jitter`` and
    keeps the ``_JITTER_STRIDE`` step relative to the base.

    Args:
        profile: Loaded profile.
        count: Number of videos to plan, at least one.
        run_id: Identifier of the run used in file names.
        rng: Random source; tests pass a seeded instance.
        start_after: Id of the last successfully generated card, if any.

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
    _warn_missing_cursor(profile, start_after)
    looks = _pick_looks(profile.looks, count, start_after)
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


def attempt_seed(seed: int, attempt: int) -> int:
    """Return the deterministic seed of one generation attempt.

    The first attempt keeps the planned seed; every later attempt gets a
    well-mixed value derived from that seed and the attempt number, so a
    rejected source is regenerated with a different scene.

    Args:
        seed: Seed of the planned video.
        attempt: One-based generation attempt.

    Returns:
        Seed in ``1..2**48 - 1``; the same input always gives the same value.
    """
    if attempt <= 1:
        return seed
    value = (seed + attempt * _SEED_STEP) & _SEED_MASK
    value = ((value ^ (value >> 30)) * _SEED_MIX_A) & _SEED_MASK
    value = ((value ^ (value >> 27)) * _SEED_MIX_B) & _SEED_MASK
    value ^= value >> 31
    return value % _MAX_SEED or 1


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


def _pick_looks(looks: tuple[_Look, ...], count: int, start_after: str | None) -> list[_Look]:
    """Pick cards in file order after *start_after*, wrapping around the end."""
    start = 0
    for index, look in enumerate(looks):
        if look.id == start_after:
            start = index + 1
            break
    return [looks[(start + offset) % len(looks)] for offset in range(count)]


def _warn_missing_cursor(profile: _Profile, start_after: str | None) -> None:
    """Report a cursor that names a card absent from ``looks.yaml``."""
    if start_after is None or any(look.id == start_after for look in profile.looks):
        return
    logger.bind(profile=profile.name, look_id=start_after).warning(
        "Карточки из курсора нет в looks.yaml — план начнётся с первой"
    )


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
