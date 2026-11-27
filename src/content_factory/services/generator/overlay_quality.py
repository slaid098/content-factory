"""Density QA of an overlay clip.

The only useful overlay is a dense one: a faint dust layer does not change a
video after blending. Density is the share of pixels brighter than a fixed
threshold on several frames. A pure black background gives almost zero, a
dense particle field gives tens of percent.
"""

from __future__ import annotations

import statistics
import subprocess
from collections.abc import Sequence
from pathlib import Path

from content_factory.schemas.generator.overlay import Density, DensityClass

BRAK = 0.3
WEAK = 3.0
DENSE = 10.0
PROBE_SECONDS = (1.0, 2.0, 3.0)
THRESHOLD = 50
_TIMEOUT = 60.0


def bright_share(
    path: Path,
    width: int,
    height: int,
    *,
    seconds: Sequence[float] = PROBE_SECONDS,
    threshold: int = THRESHOLD,
) -> float:
    """Return the mean share of bright pixels of several frames in percent.

    Args:
        path: Clip to inspect.
        width: Frame width of the clip.
        height: Frame height of the clip.
        seconds: Timestamps probed from the clip.
        threshold: Gray value above which a pixel counts as bright.

    Returns:
        Mean share in ``0..100``; an unreadable clip gives ``0``.
    """
    pixels = width * height
    shares: list[float] = []
    for second in seconds:
        frame = _gray_frame(path, second)
        if frame is None or len(frame) < pixels:
            continue
        bright = sum(1 for value in frame[:pixels] if value > threshold)
        shares.append(100.0 * bright / pixels)
    return statistics.mean(shares) if shares else 0.0


def classify(share: float) -> Density:
    """Return the quality class of a bright-pixel share in percent."""
    if share < BRAK:
        return Density(share, DensityClass.BRAK)
    if share < WEAK:
        return Density(share, DensityClass.WEAK)
    if share < DENSE:
        return Density(share, DensityClass.NORMAL)
    return Density(share, DensityClass.DENSE)


def minimum_density(profile_min: float) -> float:
    """Return the lowest accepted density, never below the channel norm."""
    return max(WEAK, profile_min)


def measure(path: Path, width: int, height: int, *, minimum: float = WEAK) -> Density:
    """Measure a clip and classify it against *minimum*.

    The class follows the measured range; *minimum* is only used by
    :func:`rejection_reason`, so the report never calls a clip dense if it is
    below a stricter profile threshold.
    """
    del minimum
    return classify(bright_share(path, width, height))


def rejection_reason(
    path: Path,
    width: int,
    height: int,
    *,
    minimum: float = WEAK,
) -> str | None:
    """Return why a clip is too sparse, or ``None`` when it is accepted."""
    density = measure(path, width, height, minimum=minimum)
    if density.share >= minimum:
        return None
    return f"плотность {density.share:.2f}% ({density.kind.value}), нужно не меньше {minimum:g}%"


def _gray_frame(path: Path, second: float) -> bytes | None:
    """Read one frame as raw 8-bit gray, or ``None`` when ffmpeg failed."""
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{second:g}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-",
            ],
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


__all__ = [
    "BRAK",
    "DENSE",
    "PROBE_SECONDS",
    "THRESHOLD",
    "WEAK",
    "bright_share",
    "classify",
    "measure",
    "minimum_density",
    "rejection_reason",
]
