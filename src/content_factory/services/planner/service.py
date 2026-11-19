"""Production planning by language."""

from __future__ import annotations

from collections.abc import Mapping

from content_factory.schemas.config.runtime import LanguageConfig


def plan_production(
    counts: Mapping[str, int],
    languages: Mapping[str, LanguageConfig],
    batch: int,
) -> dict[str, int]:
    """Распределить batch производства по языкам пропорционально дефициту."""
    deficits = {
        code: config.stock_target - counts.get(code, 0)
        for code, config in languages.items()
        if config.enabled and config.stock_target > 0
    }
    deficits = {code: value for code, value in deficits.items() if value > 0}
    total = sum(deficits.values())
    if total == 0 or batch <= 0:
        return {}

    planned = min(batch, total)
    if planned < len(deficits):
        chosen = sorted(deficits, key=lambda code: (-deficits[code], code))[:planned]
        return dict.fromkeys(sorted(chosen), 1)

    allocations = _largest_remainder(deficits, planned)
    _enforce_minimum(allocations)
    return {code: allocations[code] for code in sorted(allocations)}


def _largest_remainder(deficits: Mapping[str, int], planned: int) -> dict[str, int]:
    """Split *planned* proportionally, giving leftovers to larger fractions."""
    total = sum(deficits.values())
    allocations = {code: planned * value // total for code, value in deficits.items()}
    leftover = planned - sum(allocations.values())
    ranked = sorted(
        deficits,
        key=lambda code: (-(planned * deficits[code] % total), code),
    )
    for code in ranked[:leftover]:
        allocations[code] += 1
    return allocations


def _enforce_minimum(allocations: dict[str, int]) -> None:
    """Give one video to every zero allocation, taking it from the largest."""
    for code in sorted(allocations):
        if allocations[code] > 0:
            continue
        donor = max(
            (candidate for candidate in allocations if allocations[candidate] > 1),
            key=lambda candidate: (allocations[candidate], candidate),
        )
        allocations[donor] -= 1
        allocations[code] = 1
