"""Pure profile refill decision: order, overrides and edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.refill import ProfileRefill, RefillDecision
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles.refill import decide_refill

_GLOBAL = GeneratorConfig(target=20, refill_at=2)


def _profile(profile_id: str) -> ProfileInfo:
    category, name = profile_id.split("/", 1)
    return ProfileInfo(
        id=profile_id,
        category=category,
        name=name,
        language="ru",
        niche=category,
        width=512,
        height=896,
        audio=True,
        dir=Path("/profiles") / category / name,
    )


def test_disabled_profile_is_not_chosen() -> None:
    profile = _profile("dating/profile_2")

    assert decide_refill([profile], {}, {"dating/profile_2": 0}, _GLOBAL) is None
    assert (
        decide_refill(
            [profile],
            {"dating/profile_2": ProfileRefill(False, None, None)},
            {"dating/profile_2": 0},
            _GLOBAL,
        )
        is None
    )


def test_enough_stock_is_idle() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, None, None)},
        {"dating/profile_2": 3},
        _GLOBAL,
    )

    assert decision is None


def test_stock_at_target_is_idle() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, None, None)},
        {"dating/profile_2": 20},
        _GLOBAL,
    )

    assert decision is None


def test_target_below_stock_is_idle() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, 3, 1)},
        {"dating/profile_2": 5},
        _GLOBAL,
    )

    assert decision is None


def test_zero_stock_refills_to_global_target() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, None, None)},
        {},
        _GLOBAL,
    )

    assert decision == RefillDecision(
        profile_id="dating/profile_2",
        count=20,
        target=20,
        refill_at=2,
    )


def test_stock_at_threshold_refills_the_deficit() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, None, None)},
        {"dating/profile_2": 2},
        _GLOBAL,
    )

    assert decision is not None
    assert decision.count == 18


def test_profile_overrides_replace_global_values() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, 5, 0)},
        {"dating/profile_2": 0},
        _GLOBAL,
    )

    assert decision == RefillDecision(
        profile_id="dating/profile_2",
        count=5,
        target=5,
        refill_at=0,
    )


def test_null_override_keeps_global_value() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, None, 0)},
        {"dating/profile_2": 0},
        _GLOBAL,
    )

    assert decision is not None
    assert decision.target == 20
    assert decision.refill_at == 0


def test_first_profile_by_id_is_chosen() -> None:
    profiles = [_profile("z/profile_1"), _profile("b/profile_1"), _profile("a/profile_1")]
    settings = {
        "z/profile_1": ProfileRefill(True, 5, 0),
        "b/profile_1": ProfileRefill(True, 4, 0),
        "a/profile_1": ProfileRefill(True, 3, 0),
    }

    decision = decide_refill(profiles, settings, {}, _GLOBAL)

    assert decision is not None
    assert decision.profile_id == "a/profile_1"
    assert decision.count == 3


def test_profile_with_invalid_settings_is_skipped(
    log_records: list[dict[str, Any]],
) -> None:
    profiles = [_profile("a/profile_1"), _profile("b/profile_1")]
    settings = {
        "a/profile_1": ProfileRefill(True, 5, 5),
        "b/profile_1": ProfileRefill(True, 4, 1),
    }

    decision = decide_refill(profiles, settings, {}, _GLOBAL)

    assert decision is not None
    assert decision.profile_id == "b/profile_1"
    assert any(
        record["level"].name == "WARNING" and "a/profile_1" in record["message"]
        for record in log_records
    )


def test_enabled_profile_without_stock_entry_counts_as_zero() -> None:
    profile = _profile("dating/profile_2")

    decision = decide_refill(
        [profile],
        {"dating/profile_2": ProfileRefill(True, 2, 1)},
        {"other/profile_1": 100},
        _GLOBAL,
    )

    assert decision is not None
    assert decision.count == 2
