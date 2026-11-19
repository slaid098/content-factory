from __future__ import annotations

import pytest

from content_factory.services.supplier.planning import plan_candidates


def test_zero_need_or_cap_plans_nothing() -> None:
    assert plan_candidates(0, 100) == 0
    assert plan_candidates(-5, 100) == 0
    assert plan_candidates(10, 0) == 0
    assert plan_candidates(10, -1) == 0


def test_batch_uses_headroom_and_never_drops_below_one() -> None:
    assert plan_candidates(1, 100) == 12
    assert plan_candidates(2, 100) == 24
    assert plan_candidates(5, 100) == 60


def test_batch_is_capped_by_filter_budget() -> None:
    assert plan_candidates(10, 100) == 100
    assert plan_candidates(10, 50) == 50
    assert plan_candidates(1000, 7) == 7


@pytest.mark.parametrize(("need", "cap"), [(1, 1), (3, 2), (7, 3)])
def test_batch_never_exceeds_cap(need: int, cap: int) -> None:
    assert plan_candidates(need, cap) <= cap
