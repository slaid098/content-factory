"""Preview of the newest generated media of one profile.

A video preview is a JPEG frame cached under ``app_data/preview``. It is
rebuilt only when the newest source video is newer than the cache, so a partner
page can poll the endpoint without running ffmpeg on every request. A profile
without videos falls back to its newest photo post: the photo is served as is,
without a cache and without ffmpeg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, PREVIEW_DIR
from content_factory.exceptions.errors import ProfilePreviewError
from content_factory.schemas.profiles.preview import ProfilePreview
from content_factory.schemas.profiles.registry import ProfileInfo

_TIMEOUT_SECONDS = 30
_MP4_SUFFIX = ".mp4"
_JPEG_SUFFIX = ".jpg"
_VIDEO_MEDIA_TYPE = "image/jpeg"
_PHOTO_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def extract_preview(source: Path, target: Path) -> None:
    """Write one JPEG frame from the middle of *source* to *target*.

    ``ffprobe`` reads the duration and ``ffmpeg`` seeks to its half. Neither
    command uses a shell. A frame keeps the source size: a video is never
    upscaled.

    Args:
        source: Existing mp4 file of the profile.
        target: Destination JPEG path; its parent is created when missing.

    Raises:
        ProfilePreviewError: ``ffprobe`` returned no usable duration.
        OSError: The target directory cannot be created or ffmpeg is missing.
        subprocess.SubprocessError: A command failed or timed out; the
            original exception carries ``stderr``.
    """
    duration = _duration(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{duration / 2:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


def profile_preview(data_dir: Path, profile: ProfileInfo) -> ProfilePreview | None:
    """Return the preview of *profile*, rebuilding the video frame when stale.

    A generated video has priority: its preview is the cached JPEG frame. A
    profile without videos falls back to its newest photo post, served from the
    stock directory as is.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile: Registry fields of the profile.

    Returns:
        The preview file with its media type, or ``None`` when the profile has
        neither a generated video nor a photo.

    Raises:
        ProfilePreviewError: The newest source video exists but ffprobe or
            ffmpeg cannot produce a frame.
    """
    source = _newest_source(data_dir, profile)
    if source is None:
        photo = _newest_photo(data_dir, profile)
        if photo is None:
            return None
        return ProfilePreview(photo, _PHOTO_MEDIA_TYPES[photo.suffix.lower()])
    target = data_dir / PREVIEW_DIR / f"{profile.category}_{profile.name}{_JPEG_SUFFIX}"
    if _is_fresh(source, target):
        return ProfilePreview(target, _VIDEO_MEDIA_TYPE)
    staging = target.with_name(f"{target.stem}.tmp{_JPEG_SUFFIX}")
    try:
        extract_preview(source, staging)
        staging.replace(target)
    except (OSError, subprocess.SubprocessError, ProfilePreviewError) as ex:
        try:
            staging.unlink(missing_ok=True)
        except OSError as unlink_ex:
            logger.bind(
                path=str(staging),
                error=unlink_ex,
                error_type=type(unlink_ex).__name__,
            ).warning("Не удалось удалить временное превью {}", staging)
        logger.bind(
            path=str(source),
            stderr=_stderr(ex),
            error_type=type(ex).__name__,
        ).warning("Не удалось собрать превью профиля {}", profile.id)
        raise ProfilePreviewError("preview unavailable") from ex
    return ProfilePreview(target, _VIDEO_MEDIA_TYPE)


def _duration(source: Path) -> float:
    """Read the duration of *source* with ``ffprobe``."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    try:
        duration = float(completed.stdout.strip())
    except ValueError as ex:
        raise ProfilePreviewError(f"ffprobe не вернул длительность {source}") from ex
    if duration <= 0:
        raise ProfilePreviewError(f"ffprobe вернул пустую длительность {source}")
    return duration


def _newest_source(data_dir: Path, profile: ProfileInfo) -> Path | None:
    """Return the newest generated mp4 of *profile*, or ``None``."""
    directory = data_dir / CONTENT_DIR / profile.niche / profile.language
    prefix = f"{profile.category}_{profile.name}_"
    try:
        candidates = _media_candidates(directory, prefix, frozenset({_MP4_SUFFIX}))
    except OSError as ex:
        logger.bind(
            path=str(directory),
            error=ex,
            error_type=type(ex).__name__,
        ).debug("Каталог стока профиля {} недоступен", profile.id)
        return None
    return _newest(candidates)


def _newest_photo(data_dir: Path, profile: ProfileInfo) -> Path | None:
    """Return the newest photo post of *profile*, or ``None``."""
    directory = data_dir / CONTENT_DIR / profile.niche / profile.language
    prefix = f"{profile.category}_{profile.name}_"
    try:
        candidates = _media_candidates(
            directory,
            prefix,
            frozenset(_PHOTO_MEDIA_TYPES),
            ignore_case=True,
        )
    except OSError as ex:
        logger.bind(
            path=str(directory),
            error=ex,
            error_type=type(ex).__name__,
        ).debug("Каталог стока профиля {} недоступен", profile.id)
        return None
    return _newest(candidates)


def _media_candidates(
    directory: Path,
    prefix: str,
    suffixes: frozenset[str],
    *,
    ignore_case: bool = False,
) -> list[Path]:
    """Return regular media files with *prefix*.

    Raises:
        OSError: The directory cannot be read.
    """
    return [
        path
        for path in directory.iterdir()
        if (path.suffix.lower() if ignore_case else path.suffix) in suffixes
        and path.name.startswith(prefix)
        and path.is_file()
    ]


def _newest(candidates: list[Path]) -> Path | None:
    """Return the newest file by modification time, breaking ties by name."""
    newest: Path | None = None
    newest_mtime = float("-inf")
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError as ex:
            logger.bind(
                path=str(path),
                error=ex,
                error_type=type(ex).__name__,
            ).debug("Не удалось прочитать время файла стока {}", path.name)
            continue
        if (
            newest is None
            or mtime > newest_mtime
            or (mtime == newest_mtime and path.name > newest.name)
        ):
            newest = path
            newest_mtime = mtime
    return newest


def _is_fresh(source: Path, target: Path) -> bool:
    """Return whether *target* exists and is not older than *source*."""
    try:
        return target.is_file() and target.stat().st_mtime >= source.stat().st_mtime
    except OSError as ex:
        logger.bind(
            path=str(target),
            error=ex,
            error_type=type(ex).__name__,
        ).debug("Кэш превью {} недоступен и будет пересобран", target)
        return False


def _stderr(ex: BaseException) -> str:
    """Return the captured stderr of a failed command as text."""
    stderr = getattr(ex, "stderr", None)
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace").strip()
    if stderr is None:
        return ""
    return str(stderr).strip()


__all__ = ["ProfilePreview", "extract_preview", "profile_preview"]
