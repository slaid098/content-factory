"""Overlay density QA: threshold classes and rejection reasons."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.schemas.generator.overlay import DensityClass
from content_factory.services.generator import overlay_quality


@pytest.mark.parametrize(
    ("share", "kind"),
    [
        (0.0, DensityClass.BRAK),
        (0.29, DensityClass.BRAK),
        (0.3, DensityClass.WEAK),
        (2.99, DensityClass.WEAK),
        (3.0, DensityClass.NORMAL),
        (9.99, DensityClass.NORMAL),
        (10.0, DensityClass.DENSE),
        (42.0, DensityClass.DENSE),
    ],
)
def test_density_classes_follow_the_channel_thresholds(share: float, kind: DensityClass) -> None:
    assert overlay_quality.classify(share).kind is kind


def test_bright_share_averages_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    frames = {
        1.0: bytes([255]) * 100,
        2.0: bytes([0]) * 100,
        3.0: bytes([255]) * 100,
    }
    monkeypatch.setattr(
        overlay_quality,
        "_gray_frame",
        lambda path, second: frames[second],
    )

    share = overlay_quality.bright_share(Path("clip.mp4"), 10, 10)

    assert share == pytest.approx(100 * 2 / 3)


def test_bright_share_ignores_unreadable_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_quality, "_gray_frame", lambda path, second: None)

    assert overlay_quality.bright_share(Path("clip.mp4"), 10, 10) == 0.0


def test_rejection_reason_accepts_normal_density(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_quality, "bright_share", lambda path, width, height: 5.0)

    assert overlay_quality.rejection_reason(Path("clip.mp4"), 384, 672) is None


def test_rejection_reason_names_weak_density(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_quality, "bright_share", lambda path, width, height: 1.5)

    reason = overlay_quality.rejection_reason(Path("clip.mp4"), 384, 672)

    assert reason is not None
    assert "1.50%" in reason
    assert "слабо" in reason


def test_profile_threshold_never_drops_below_the_channel_norm() -> None:
    assert overlay_quality.minimum_density(1.0) == 3.0
    assert overlay_quality.minimum_density(5.0) == 5.0
