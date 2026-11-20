"""Frame extraction for title generation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg

from content_factory.services.titles.client import TitleError

_FRAME_TIMEOUT_SECONDS = 30
_SCALE_FILTER = "scale=768:768:force_original_aspect_ratio=decrease"
_FALLBACK_TIMES = (1.0, 3.0, 6.0)
_FRAME_PERCENTAGES = (0.1, 0.5, 0.9)


def frame_times(duration: int | None) -> tuple[float, float, float]:
    """Return the 10 %, 50 % and 90 % moments of a video.

    A video without a usable duration falls back to one, three and six
    seconds, which are reachable for every downloaded TikTok clip.

    Args:
        duration: Video duration in seconds, if known.

    Returns:
        Three seek moments in seconds.
    """
    if duration is None or duration <= 0:
        return _FALLBACK_TIMES
    return (
        round(duration * _FRAME_PERCENTAGES[0], 3),
        round(duration * _FRAME_PERCENTAGES[1], 3),
        round(duration * _FRAME_PERCENTAGES[2], 3),
    )


def extract_frames(path: Path, duration: int | None) -> tuple[bytes, ...]:
    """Extract up to three JPEG frames from a video.

    An unreadable frame is skipped, so a clip shorter than its nominal
    duration still yields the frames that ffmpeg could decode. Empty and
    duplicate frames are dropped.

    Args:
        path: Path to the local ``.mp4`` file.
        duration: Video duration in seconds, if known.

    Returns:
        Distinct non-empty JPEG frames, possibly fewer than three.

    Raises:
        TitleError: ffmpeg cannot be started, times out or fails unexpectedly.
    """
    frames: list[bytes] = []
    for moment in frame_times(duration):
        data = _extract_frame(path, moment)
        if data and data not in frames:
            frames.append(data)
    return tuple(frames)


def _extract_frame(path: Path, moment: float) -> bytes:
    """Run ffmpeg for one moment and return the JPEG bytes, if any."""
    try:
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-ss",
            f"{moment:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            _SCALE_FILTER,
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=_FRAME_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as ex:
        raise TitleError("не удалось извлечь кадры видео") from ex
    if completed.returncode != 0:
        return b""
    return completed.stdout
