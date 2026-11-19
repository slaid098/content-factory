from __future__ import annotations

from content_factory.schemas.config.runtime import LanguageConfig
from content_factory.services.planner.service import plan_production


def _language(stock_target: int, *, enabled: bool = True) -> LanguageConfig:
    return LanguageConfig(enabled=enabled, stock_target=stock_target)


def test_splits_batch_proportionally_to_deficit() -> None:
    counts = {"ru": 50, "en": 50}
    languages = {"ru": _language(150), "en": _language(100)}

    assert plan_production(counts, languages, 20) == {"ru": 13, "en": 7}


def test_returns_empty_when_every_target_is_reached() -> None:
    counts = {"ru": 150, "en": 100}
    languages = {"ru": _language(150), "en": _language(100)}

    assert plan_production(counts, languages, 20) == {}


def test_ignores_stock_above_target() -> None:
    counts = {"ru": 200, "en": 0}
    languages = {"ru": _language(150), "en": _language(100)}

    assert plan_production(counts, languages, 20) == {"en": 20}


def test_batch_below_language_count_gives_one_to_largest_deficits() -> None:
    languages = {
        "a": _language(10),
        "b": _language(10),
        "c": _language(10),
    }

    assert plan_production({}, languages, 1) == {"a": 1}
    assert plan_production({}, languages, 2) == {"a": 1, "b": 1}


def test_batch_below_language_count_prefers_larger_deficit() -> None:
    languages = {"ru": _language(100), "en": _language(1000)}

    assert plan_production({}, languages, 1) == {"en": 1}


def test_language_deficit_limits_its_share() -> None:
    counts = {"ru": 4, "en": 0}
    languages = {"ru": _language(5), "en": _language(100)}

    plan = plan_production(counts, languages, 20)

    assert plan == {"ru": 1, "en": 19}
    assert sum(plan.values()) == 20


def test_batch_above_total_deficit_equals_deficits() -> None:
    languages = {"ru": _language(3), "en": _language(2)}

    assert plan_production({}, languages, 100) == {"ru": 3, "en": 2}


def test_disabled_language_is_ignored() -> None:
    languages = {"ru": _language(10), "en": _language(10, enabled=False)}

    assert plan_production({}, languages, 5) == {"ru": 5}


def test_zero_target_language_is_ignored() -> None:
    languages = {"ru": _language(0), "en": _language(10)}

    assert plan_production({}, languages, 5) == {"en": 5}


def test_missing_count_is_treated_as_zero() -> None:
    assert plan_production({}, {"ru": _language(5)}, 20) == {"ru": 5}


def test_zero_batch_returns_empty_plan() -> None:
    assert plan_production({}, {"ru": _language(5)}, 0) == {}


def test_every_deficient_language_gets_at_least_one() -> None:
    counts = {"ru": 0, "en": 0}
    languages = {"ru": _language(1), "en": _language(100)}

    plan = plan_production(counts, languages, 10)

    assert plan == {"en": 9, "ru": 1}
    assert sum(plan.values()) == 10


def test_equal_fractions_break_ties_alphabetically() -> None:
    languages = {"ru": _language(10), "en": _language(10)}

    assert plan_production({}, languages, 1) == {"en": 1}


def test_leftover_goes_to_equal_fractions_alphabetically() -> None:
    languages = {"a": _language(10), "b": _language(10), "c": _language(10)}

    assert plan_production({}, languages, 5) == {"a": 2, "b": 2, "c": 1}


def test_plan_never_exceeds_batch_or_deficit() -> None:
    counts = {"ru": 7, "en": 13}
    languages = {"ru": _language(10), "en": _language(20)}

    plan = plan_production(counts, languages, 6)

    assert sum(plan.values()) == 6
    assert plan["ru"] <= 3
    assert plan["en"] <= 7
    assert plan == plan_production(counts, languages, 6)
