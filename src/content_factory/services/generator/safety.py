"""Clef frame safety check of a downloaded source before the overlay."""

from __future__ import annotations

import io
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from loguru import logger
from PIL import Image

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, SafetyError
from content_factory.services.generator import media
from content_factory.services.generator.clef import ClefClient, _ClefTransport
from content_factory.services.generator.plan import _PlannedItem

_FIRST_FRAME_RATIO = 0.02
_LAST_FRAME_RATIO = 0.98
_LAST_FRAME_GAP = 0.05
_SHORT_DURATION = 0.5
# Проверка идёт на арендованном сервере: зависший кадр нельзя ждать 15 минут,
# как обычную локальную нарезку.
_SAFETY_TIMEOUT = 20.0
_SYNTHETIC_SIZE = 64
_SYNTHETIC_COLOR = (127, 127, 127)


class _Checker(Protocol):
    """Safety port of the generation queue."""

    def preflight(self) -> None:
        """Fail before rent when Clef cannot answer one synthetic frame."""
        ...

    def verify(self, item: _PlannedItem, source: Path, attempt: int) -> str | None:
        """Return the defect reason of *source*, or ``None`` when it is safe."""
        ...


class FrameChecker:
    """Clef check of every downloaded source before the text card is drawn."""

    def __init__(
        self,
        settings: GeneratorSettings,
        *,
        client: _ClefTransport | None = None,
    ) -> None:
        """Store settings and the Clef client used for all videos of the batch."""
        self._settings = settings
        self._client = client or ClefClient(settings)

    def preflight(self) -> None:
        """Ask Clef about one synthetic frame before any server is rented.

        Raises:
            SafetyError: The endpoint rejects the request or returns an
                answer that cannot be parsed.
        """
        self._client.scores(_synthetic_frame())
        logger.bind(stage="preflight").info("Preflight Clef пройден")

    def verify(self, item: _PlannedItem, source: Path, attempt: int) -> str | None:
        """Return the defect reason of *source*, or ``None`` when it is safe.

        Raises:
            SafetyError: The source or one of its frames is unreadable, or
                Clef fails. The caller fails the video closed.
        """
        return verify_source(self._client, self._settings, item, source, attempt)


def _synthetic_frame() -> bytes:
    """Return a small neutral PNG used only by the preflight request."""
    image = Image.new("RGB", (_SYNTHETIC_SIZE, _SYNTHETIC_SIZE), _SYNTHETIC_COLOR)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def frame_points(duration: float, count: int) -> tuple[float, ...]:
    """Return the timestamps of the checked frames of one source.

    The points are spread uniformly from 2% to 98% of the duration; the last
    one is moved to ``duration - 0.05`` so the final frame of the video is
    always checked. A video shorter than half a second gets one frame.

    Args:
        duration: Source duration in seconds.
        count: Requested number of frames.

    Returns:
        Strictly increasing timestamps inside the video.

    Raises:
        SafetyError: The duration is not positive.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise SafetyError(f"длительность исходника должна быть положительной: {duration}")
    last = max(0.0, duration - _LAST_FRAME_GAP)
    if duration < _SHORT_DURATION or count <= 1:
        return (last,)
    step = (_LAST_FRAME_RATIO - _FIRST_FRAME_RATIO) / (count - 1)
    points = [duration * (_FIRST_FRAME_RATIO + step * index) for index in range(count)]
    points[-1] = last
    return tuple(points)


def frame_reason(scores: Mapping[str, float], settings: GeneratorSettings) -> str | None:
    """Return the first unsafe question of one frame, or ``None``.

    Args:
        scores: Clef scores keyed by the safety question names.
        settings: Thresholds of the batch.

    Returns:
        Human-readable questions and scores that crossed their limits.
    """
    reasons = [
        f"{key} {scores[key]:.2f} >= {limit:g}"
        for key, limit in _limits(settings)
        if scores[key] >= limit
    ]
    return "; ".join(reasons) or None


def verify_source(
    client: _ClefTransport,
    settings: GeneratorSettings,
    item: _PlannedItem,
    source: Path,
    attempt: int,
) -> str | None:
    """Check the frames of one downloaded source with Clef.

    Args:
        client: Clef transport.
        settings: Frame count and thresholds of the batch.
        item: Planned video, used for log context only.
        source: Downloaded silent mp4, before the text card is drawn.
        attempt: One-based generation attempt.

    Returns:
        Reason of the first unsafe frame, or ``None`` when every frame passed.

    Raises:
        SafetyError: The source cannot be probed, ffmpeg cannot extract a
            frame, or Clef fails. The caller must fail the video closed.
    """
    if not settings.safety_enabled:
        return None
    info = media.probe(source, timeout=_SAFETY_TIMEOUT)
    if info is None:
        raise SafetyError(f"не удалось прочитать исходник: {source}")
    points = frame_points(info.duration, settings.safety_frames)
    for index, seconds in enumerate(points, start=1):
        try:
            png = media.frame_png(source, seconds, timeout=_SAFETY_TIMEOUT)
        except GeneratorError as ex:
            raise SafetyError(
                f"кадр {index}/{len(points)} не извлечён из {source.name}: {ex}"
            ) from ex
        scores = client.scores(png)
        reason = frame_reason(scores, settings)
        if reason is not None:
            logger.bind(
                file=item.filename,
                attempt=attempt,
                frame=index,
                seconds=round(seconds, 2),
                scores=scores,
            ).info("Кадр не прошёл проверку Clef")
            return f"кадр {index}/{len(points)} ({seconds:.2f} с): {reason}"
    return None


def _limits(settings: GeneratorSettings) -> tuple[tuple[str, float], ...]:
    """Return every safety question with its threshold."""
    return (
        ("back", settings.safety_threshold),
        ("short_skirt", settings.safety_threshold),
        ("cleavage", settings.safety_threshold),
        ("all_skin", settings.safety_threshold),
        ("nude", settings.safety_nude_threshold),
    )


__all__ = ["FrameChecker", "frame_points", "frame_reason", "verify_source"]
