"""Pure refill decision for generator profiles.

The decision never touches the database or the network: the flow reads the
settings and stock counters and hands them here, so the choice of the first
profile and content type to refill stays a small function with its own tests.
Video has priority over photo: a tick that finds both only starts the video
batch, and the photo batch is left for the next tick.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from loguru import logger

from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.profiles.refill import ProfileRefill, RefillDecision
from content_factory.schemas.profiles.registry import ProfileInfo


def decide_refill(
    profiles: Iterable[ProfileInfo],
    settings: Mapping[str, ProfileRefill],
    stock: Mapping[str, int],
    photo_stock: Mapping[str, int],
    global_config: GeneratorConfig,
) -> RefillDecision | None:
    """Choose the first profile and content type that need a refill.

    Video is checked first over every profile in id order, then photo. A type
    takes part only when the profile declares it in ``outputs``, the refill is
    enabled, the available count is at or below its threshold and the goal is
    still above that count. Effective video values merge the profile overrides
    with the global ones; the photo pair has no global fallback. Invalid
    per-profile settings are logged and only that profile is skipped.

    Args:
        profiles: Profiles of the registry, in any order.
        settings: Refill settings keyed by profile id. A missing id means the
            profile is disabled.
        stock: Available video count keyed by profile id; a missing id counts
            as zero.
        photo_stock: Available photo count keyed by profile id; a missing id
            counts as zero.
        global_config: Global video target and refill threshold.

    Returns:
        The decision for the first eligible profile and type, or ``None``
        when no profile needs a refill.
    """
    ordered = sorted(profiles, key=lambda item: item.id)
    for content_type, available in (
        (ContentType.VIDEO, stock),
        (ContentType.PHOTO, photo_stock),
    ):
        decision = _first_profile(ordered, settings, available, content_type, global_config)
        if decision is not None:
            return decision
    return None


def _first_profile(
    profiles: Iterable[ProfileInfo],
    settings: Mapping[str, ProfileRefill],
    available: Mapping[str, int],
    content_type: ContentType,
    global_config: GeneratorConfig,
) -> RefillDecision | None:
    """Return the first profile that needs a refill of one content type."""
    for profile in profiles:
        override = settings.get(profile.id)
        if override is None or not override.enabled:
            continue
        if content_type not in profile.outputs:
            continue
        limits = _limits(profile.id, override, content_type, global_config)
        if limits is None:
            continue
        target, refill_at = limits
        count = available.get(profile.id, 0)
        if count <= refill_at and target > count:
            return RefillDecision(
                profile_id=profile.id,
                count=target - count,
                target=target,
                refill_at=refill_at,
                content_type=content_type,
            )
    return None


def _limits(
    profile_id: str,
    override: ProfileRefill,
    content_type: ContentType,
    global_config: GeneratorConfig,
) -> tuple[int, int] | None:
    """Return the effective goal and threshold of one type, or ``None`` if off.

    A zero photo goal is the normal off state and stays silent; a zero goal
    with a non-zero threshold and any other invalid pair are logged.
    """
    if content_type is ContentType.VIDEO:
        target = override.target or global_config.target
        refill_at = (
            override.refill_at if override.refill_at is not None else global_config.refill_at
        )
    else:
        target = override.photo_target
        refill_at = override.photo_refill_at
        if target == 0:
            if refill_at != 0:
                _invalid(profile_id, content_type, target, refill_at)
            return None
    if target < 1 or refill_at < 0 or refill_at >= target:
        _invalid(profile_id, content_type, target, refill_at)
        return None
    return target, refill_at


def _invalid(
    profile_id: str,
    content_type: ContentType,
    target: int,
    refill_at: int,
) -> None:
    """Log invalid refill settings of one profile and type."""
    logger.bind().warning(
        "Настройки добора {} профиля {} некорректны: target={}, refill_at={}; тип пропущен",
        content_type.value,
        profile_id,
        target,
        refill_at,
    )


__all__ = ["RefillDecision", "decide_refill"]
