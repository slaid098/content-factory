"""Pure refill decision for generator profiles.

The decision never touches the database or the network: the flow reads the
settings and stock counters and hands them here, so the choice of the first
profile to refill stays a small function with its own tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from loguru import logger

from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.refill import ProfileRefill, RefillDecision
from content_factory.schemas.profiles.registry import ProfileInfo


def decide_refill(
    profiles: Iterable[ProfileInfo],
    settings: Mapping[str, ProfileRefill],
    stock: Mapping[str, int],
    global_config: GeneratorConfig,
) -> RefillDecision | None:
    """Choose the first profile by id that needs a refill.

    Effective values merge the profile overrides with the global ones:
    ``target = override or global.target`` and
    ``refill_at = override if override is not None else global.refill_at``.
    A profile is chosen when its refill is enabled, its stock is at or below
    the refill threshold and the target is still above the stock. Invalid
    per-profile settings are logged and only that profile is skipped.

    Args:
        profiles: Profiles of the registry, in any order.
        settings: Refill settings keyed by profile id. A missing id means the
            profile is disabled.
        stock: Available video count keyed by profile id; a missing id counts
            as zero.
        global_config: Global target and refill threshold.

    Returns:
        The decision for the first eligible profile in id order, or ``None``
        when no profile needs a refill.
    """
    for profile in sorted(profiles, key=lambda item: item.id):
        override = settings.get(profile.id)
        if override is None or not override.enabled:
            continue
        target = override.target or global_config.target
        refill_at = (
            override.refill_at if override.refill_at is not None else global_config.refill_at
        )
        if target < 1 or refill_at < 0 or refill_at >= target:
            logger.bind().warning(
                "Настройки добора профиля {} некорректны: target={}, refill_at={}; "
                "профиль пропущен",
                profile.id,
                target,
                refill_at,
            )
            continue
        available = stock.get(profile.id, 0)
        if available <= refill_at and target > available:
            return RefillDecision(
                profile_id=profile.id,
                count=target - available,
                target=target,
                refill_at=refill_at,
            )
    return None


__all__ = ["RefillDecision", "decide_refill"]
