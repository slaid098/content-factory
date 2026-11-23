"""MP4 checks of a finished video."""

from __future__ import annotations

from pathlib import Path

from content_factory.exceptions.generator import QaError
from content_factory.services.generator.media import _MediaInfo, probe

_MIN_BYTES = 10 * 1024
# Допуск длительности: 15% или 0,75 с — длиннее для коротких роликов.
_DURATION_RATIO = 0.15
_MIN_DURATION_TOLERANCE = 0.75


def check_video(path: Path, expected_seconds: float) -> _MediaInfo:
    """Probe a finished mp4 and verify it is a playable video.

    Args:
        path: Final mp4 in the ``final/`` directory.
        expected_seconds: Nominal duration of the planned video.

    Returns:
        Parsed facts of the file.

    Raises:
        QaError: The file is missing, empty, unreadable, has no video stream
            or its duration is outside the tolerance.
    """
    info = probe(path)
    if info is None:
        raise QaError(f"не удалось прочитать mp4: {path}")
    _check_media(info, expected_seconds)
    return info


def _check_media(info: _MediaInfo, expected_seconds: float) -> None:
    """Check already parsed facts of one mp4.

    Args:
        info: Facts returned by ``ffprobe``.
        expected_seconds: Nominal duration of the planned video.

    Raises:
        QaError: The file is too small, has no video or is too short/long.
    """
    if info.size < _MIN_BYTES:
        raise QaError(f"файл слишком мал: {info.path} ({info.size} байт)")
    if not info.has_video:
        raise QaError(f"в файле нет видеопотока: {info.path}")
    tolerance = max(_MIN_DURATION_TOLERANCE, expected_seconds * _DURATION_RATIO)
    if abs(info.duration - expected_seconds) > tolerance:
        raise QaError(
            f"длительность {info.duration:.2f} с вне допуска "
            f"{expected_seconds:.2f} ± {tolerance:.2f} с: {info.path}"
        )
