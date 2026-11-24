"""External media commands: ffmpeg, ffprobe and the free space check.

The module only wraps processes and parses their output. Domain decisions
(what a bad file or a short disk means) stay in :mod:`qa` and the service.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from content_factory.exceptions.generator import GeneratorError

_DEFAULT_TIMEOUT = 900.0
# Перед арендой сервера на диске должно остаться место под сами ролики и
# временные файлы ffmpeg; порог консервативный, места на Linux 1 хватает.
_MIN_FREE_BYTES = 2 * 1024**3
_STDERR_TAIL = 400


@dataclass(frozen=True, slots=True)
class _MediaInfo:
    """Facts about one mp4 file read by ``ffprobe``."""

    path: Path
    duration: float
    width: int
    height: int
    has_video: bool
    has_audio: bool
    size: int


def run(command: list[str], *, timeout: float = _DEFAULT_TIMEOUT) -> None:
    """Run a media command and raise :class:`GeneratorError` on failure.

    Args:
        command: Full argv, the first item is the executable.
        timeout: Seconds before the process is killed.

    Raises:
        GeneratorError: The command is missing, hung or returned non-zero.
    """
    completed = _capture(command, timeout)
    if completed is None:
        raise GeneratorError(f"{command[0]} не запустился или не ответил за {timeout:g} с")
    if completed.returncode != 0:
        tail = completed.stderr.strip()[-_STDERR_TAIL:]
        raise GeneratorError(f"{command[0]} завершился с кодом {completed.returncode}: {tail}")


def probe(path: Path, *, timeout: float = _DEFAULT_TIMEOUT) -> _MediaInfo | None:
    """Read duration, size and streams of an mp4; ``None`` means unreadable.

    Args:
        path: mp4 file to inspect.
        timeout: Seconds before ffprobe is killed.

    Returns:
        Parsed facts, or ``None`` when ffprobe is missing, the file is gone
        or its JSON has no usable duration.
    """
    if not path.is_file():
        return None
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = _capture(command, timeout)
    if completed is None or completed.returncode != 0:
        return None
    try:
        payload: Any = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return _media_info(path, payload)


def strip_audio(source: Path, target: Path) -> None:
    """Copy the video stream only, dropping the model-generated audio.

    Args:
        source: Downloaded mp4 with the audio track of the LTX model.
        target: Resulting silent mp4; the final music is mixed later.

    Raises:
        GeneratorError: ffmpeg is missing or rejected the file.
    """
    run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            str(target),
        ]
    )


def frame_png(source: Path, seconds: float, *, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
    """Extract one frame of *source* at *seconds* as PNG bytes.

    Args:
        source: Video file to seek in.
        seconds: Timestamp of the frame; negative values clamp to the start.
        timeout: Seconds before ffmpeg is killed.

    Returns:
        Encoded PNG of the frame.

    Raises:
        GeneratorError: ffmpeg is missing, hung, returned non-zero or
            produced no image.
    """
    moment = max(0.0, seconds)
    command = [
        "ffmpeg",
        "-ss",
        f"{moment:.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    completed = _capture_bytes(command, timeout)
    if completed is None:
        raise GeneratorError(f"ffmpeg не запустился или не ответил за {timeout:g} с")
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL:]
        raise GeneratorError(f"ffmpeg завершился с кодом {completed.returncode}: {tail}")
    if not completed.stdout:
        raise GeneratorError(f"ffmpeg не вытащил кадр {moment:.3f} с из {source}")
    return completed.stdout


def _free_bytes(path: Path) -> int:
    """Return free bytes on the filesystem that holds *path*."""
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def ensure_free_space(path: Path, required: int = _MIN_FREE_BYTES) -> None:
    """Raise before renting a server when the disk is too full.

    Args:
        path: Directory that will receive the batch output.
        required: Minimum free bytes that must stay on its filesystem.

    Raises:
        GeneratorError: Fewer than *required* bytes are available.
    """
    available = _free_bytes(path)
    if available < required:
        raise GeneratorError(
            f"мало места на диске для {path}: свободно {available} байт, нужно {required}"
        )


def video_seconds(frames: int, fps: int) -> float:
    """Return the expected duration of a video of *frames* frames at *fps*."""
    if fps <= 0:
        raise GeneratorError(f"fps должен быть положительным, получено {fps}")
    return frames / fps


def _media_info(path: Path, payload: Any) -> _MediaInfo | None:
    """Build :class:`_MediaInfo` from the ffprobe JSON payload."""
    if not isinstance(payload, dict):
        return None
    streams = payload.get("streams")
    if not isinstance(streams, list):
        return None
    video = _first_stream(streams, "video")
    if video is None:
        return None
    duration = _duration(payload, video)
    if duration is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return _MediaInfo(
        path=path,
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_video=True,
        has_audio=_first_stream(streams, "audio") is not None,
        size=size,
    )


def _first_stream(streams: list[Any], kind: str) -> dict[str, Any] | None:
    """Return the first stream of the given codec type, if any."""
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == kind:
            return stream
    return None


def _duration(payload: dict[str, Any], video: dict[str, Any]) -> float | None:
    """Return duration in seconds from the container or the video stream."""
    candidates: list[Any] = []
    container = payload.get("format")
    if isinstance(container, dict):
        candidates.append(container.get("duration"))
    candidates.append(video.get("duration"))
    for value in candidates:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds > 0:
            return seconds
    return None


def _capture(command: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    """Run the command, returning ``None`` when it cannot start or times out."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _capture_bytes(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes] | None:
    """Run a command with binary output, or return ``None`` on start/timeout."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
