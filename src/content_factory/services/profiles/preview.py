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

from content_factory.constants import CONTENT_DIR, PREVIEW_DIR, PROFILES_DIR
from content_factory.exceptions.errors import ProfilePreviewError
from content_factory.schemas.profiles.preview import ProfilePreview
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.stock import profile_slugs

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
    directory = data_dir / CONTENT_DIR / profile.niche / profile.language
    videos, photos = _profile_media(data_dir, directory, profile)
    source = _newest(videos)
    if source is not None:
        return _video_preview(data_dir, profile, source)
    photo = _newest(photos)
    if photo is None:
        return None
    return ProfilePreview(photo, _PHOTO_MEDIA_TYPES[photo.suffix.lower()])


def _video_preview(
    data_dir: Path,
    profile: ProfileInfo,
    source: Path,
) -> ProfilePreview:
    """Return the cached JPEG frame of *source*, rebuilding it when stale."""
    target = data_dir / PREVIEW_DIR / profile.category / f"{profile.name}{_JPEG_SUFFIX}"
    if _is_fresh(source, target):
        return ProfilePreview(target, _VIDEO_MEDIA_TYPE)
    staging = target.with_name(f"{target.stem}.tmp{_JPEG_SUFFIX}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
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


def _profile_media(
    data_dir: Path,
    directory: Path,
    profile: ProfileInfo,
) -> tuple[list[Path], list[Path]]:
    """Return video and photo files owned by *profile* in one directory scan.

    The owner of a file is the longest registry slug that prefixes its name,
    so a profile never serves files of a profile whose name extends its own.
    A slug claimed by two profiles is ambiguous and belongs to none; symlinks
    are not stock files.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        directory: Stock directory ``content/<niche>/<language>``.
        profile: Registry fields of the profile.

    Returns:
        Video and photo candidates; both lists are empty when the directory
        cannot be read.
    """
    try:
        entries = list(directory.iterdir())
    except OSError as ex:
        logger.bind(
            path=str(directory),
            error=ex,
            error_type=type(ex).__name__,
        ).debug("Каталог стока профиля {} недоступен", profile.id)
        return [], []
    owners = sorted(
        profile_slugs(data_dir / PROFILES_DIR).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    videos: list[Path] = []
    photos: list[Path] = []
    for path in entries:
        if not _is_regular_file(path):
            continue
        if _owner(path.name, owners) != profile.id:
            continue
        if path.suffix == _MP4_SUFFIX:
            videos.append(path)
        elif path.suffix.lower() in _PHOTO_MEDIA_TYPES:
            photos.append(path)
    return videos, photos


def _is_regular_file(path: Path) -> bool:
    """Return whether *path* is a regular file and not a symlink."""
    try:
        if path.is_symlink():
            return False
        return path.is_file()
    except OSError:
        return False


def _owner(name: str, owners: list[tuple[str, str]]) -> str | None:
    """Return the profile id of the longest slug that prefixes *name*."""
    for slug, profile_id in owners:
        if name.startswith(f"{slug}_"):
            return profile_id
    return None


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
    """Return whether *target* is a regular cache not older than *source*.

    A symlink is never a usable cache: it could point outside the preview
    directory, so it is rebuilt.
    """
    try:
        if target.is_symlink():
            return False
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
