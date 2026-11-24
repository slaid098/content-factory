"""Batch plan: unique cards and seeds, frame lengths inside the window."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.services.generator.plan import plan_batch, validate_count

_RUN_ID = "20260101T000000Z"


def test_seven_items_have_unique_cards_and_seeds(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    items = plan_batch(profile, 7, _RUN_ID, rng=random.Random(7))

    assert len(items) == 7
    assert len({item.look.id for item in items}) == 7
    assert len({item.seed for item in items}) == 7
    assert [item.seq for item in items] == list(range(1, 8))


def test_frame_lengths_stay_in_window_with_step_four(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20, frames_jitter=11)

    items = plan_batch(profile, 20, _RUN_ID, rng=random.Random(11))

    for item in items:
        assert abs(item.frames - profile.frames) <= profile.frames_jitter
        assert (item.frames - profile.frames) % 4 == 0


def test_file_name_contains_slug_run_and_sequence(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    items = plan_batch(profile, 2, _RUN_ID, rng=random.Random(3))

    assert items[0].filename == f"dating_profile_2_{_RUN_ID}_001.mp4"
    assert items[1].filename == f"dating_profile_2_{_RUN_ID}_002.mp4"


def test_more_items_than_cards_go_in_circle_without_adjacent_repeats(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=2)

    items = plan_batch(profile, 7, _RUN_ID, rng=random.Random(5))

    ids = [item.look.id for item in items]
    assert all(left != right for left, right in zip(ids, ids[1:], strict=False))


def test_batch_starts_with_the_card_after_the_cursor(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    items = plan_batch(profile, 3, _RUN_ID, rng=random.Random(7), start_after="c01")

    assert [item.look.id for item in items] == ["c02", "c03", "c04"]


def test_cursor_on_the_last_card_wraps_to_the_first(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    items = plan_batch(profile, 2, _RUN_ID, rng=random.Random(7), start_after="c20")

    assert [item.look.id for item in items] == ["c01", "c02"]


@pytest.mark.parametrize("start_after", [None, "gone"])
def test_absent_or_unknown_cursor_starts_from_the_first_card(
    tmp_path: Path, start_after: str | None
) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    items = plan_batch(profile, 2, _RUN_ID, rng=random.Random(7), start_after=start_after)

    assert [item.look.id for item in items] == ["c01", "c02"]


def test_card_order_does_not_depend_on_the_random_source(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    first = plan_batch(profile, 4, _RUN_ID, rng=random.Random(1), start_after="c04")
    second = plan_batch(profile, 4, _RUN_ID, rng=random.Random(99), start_after="c04")

    assert [item.look.id for item in first] == ["c05", "c06", "c07", "c08"]
    assert [item.look.id for item in second] == [item.look.id for item in first]


def test_count_must_be_positive(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=20)

    for value in (0, -1, True):
        with pytest.raises(GeneratorError):
            plan_batch(profile, value, _RUN_ID)  # type: ignore[arg-type]
        with pytest.raises(GeneratorError):
            validate_count(value)  # type: ignore[arg-type]


def test_single_card_cannot_fill_a_multi_item_batch(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", looks=1)

    single = plan_batch(profile, 1, _RUN_ID, rng=random.Random(1))
    assert len(single) == 1

    with pytest.raises(ProfileError, match="одна карточка"):
        plan_batch(profile, 2, _RUN_ID, rng=random.Random(1))
