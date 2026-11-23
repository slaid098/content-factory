from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.supplier.supplier_helpers import make_config

from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import VideoMetadata
from content_factory.services.supplier import rules
from content_factory.services.supplier.rules import evaluate

NOW = 1_800_000_000
MIN_AGE_SECONDS = 5 * 86400


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rules.time, "time", lambda: NOW)


@pytest.fixture
def cfg(tmp_path: Path) -> SupplierConfig:
    return make_config(
        tmp_path,
        max_views=2000,
        max_followers=500,
        min_duration=5,
        max_duration=60,
    )


@pytest.fixture
def meta() -> VideoMetadata:
    return VideoMetadata(
        video_id="1",
        url="https://tiktok.test/video/1",
        author_id="author",
        author_name="Author",
        description="description",
        views=100,
        followers=50,
        duration=12,
        is_aigc=False,
        aigc_description="",
        aigc_label="",
        moderation_aigc_label="",
        width=1080,
        height=1920,
        create_time=NOW - 30 * 86400,
    )


def test_approved(meta: VideoMetadata, cfg: SupplierConfig) -> None:
    decision = evaluate(meta, cfg)
    assert decision.approved is True
    assert decision.reason is None


def test_boundaries_are_approved(meta: VideoMetadata, cfg: SupplierConfig) -> None:
    assert evaluate(replace(meta, views=2000, followers=500, duration=5), cfg).approved
    assert evaluate(replace(meta, views=0, followers=0, duration=60), cfg).approved


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"is_aigc": True}, "aigc"),
        ({"aigc_description": "AI generated"}, "aigc"),
        ({"aigc_label": "1"}, "aigc"),
        ({"moderation_aigc_label": "1"}, "aigc"),
        ({"aigc_label": "1", "moderation_aigc_label": "1"}, "aigc"),
        ({"duration": None}, "invalid_duration"),
        ({"duration": 0}, "invalid_duration"),
        ({"duration": 4}, "duration_out_of_range"),
        ({"duration": 61}, "duration_out_of_range"),
        ({"width": None}, "unknown_dimensions"),
        ({"height": None}, "unknown_dimensions"),
        ({"width": 0}, "unknown_dimensions"),
        ({"width": 1080, "height": 1080}, "not_vertical"),
        ({"width": 1920, "height": 1080}, "not_vertical"),
        ({"views": None}, "unknown_views"),
        ({"views": 2001}, "too_many_views"),
        ({"followers": None}, "unknown_followers"),
        ({"followers": 501}, "too_many_followers"),
        ({"create_time": None}, "unknown_age"),
        ({"create_time": 0}, "unknown_age"),
        ({"create_time": NOW + 1}, "unknown_age"),
        ({"create_time": NOW - MIN_AGE_SECONDS + 1}, "too_young"),
    ],
)
def test_rejections(
    meta: VideoMetadata,
    cfg: SupplierConfig,
    changes: dict[str, object],
    reason: str,
) -> None:
    decision = evaluate(replace(meta, **changes), cfg)
    assert decision.approved is False
    assert decision.reason == reason


@pytest.mark.parametrize("field", ["aigc_label", "moderation_aigc_label"])
def test_aigc_label_takes_priority_over_other_filters(
    meta: VideoMetadata,
    cfg: SupplierConfig,
    field: str,
) -> None:
    labelled = replace(meta, **{field: "1", "duration": None})

    decision = evaluate(labelled, cfg)

    assert decision.approved is False
    assert decision.reason == "aigc"


def test_missing_aigc_labels_are_approved(meta: VideoMetadata, cfg: SupplierConfig) -> None:
    decision = evaluate(replace(meta, aigc_label="", moderation_aigc_label=""), cfg)

    assert decision.approved is True
    assert decision.reason is None


def test_minimum_age_boundary_is_approved(meta: VideoMetadata, cfg: SupplierConfig) -> None:
    decision = evaluate(replace(meta, create_time=NOW - MIN_AGE_SECONDS), cfg)
    assert decision.approved is True
    assert decision.reason is None


def test_disabled_age_filter_ignores_create_time(meta: VideoMetadata, cfg: SupplierConfig) -> None:
    disabled = replace(cfg, min_age_days=0)
    assert evaluate(replace(meta, create_time=None), disabled).approved
    assert evaluate(replace(meta, create_time=NOW - 60), disabled).approved
