"""Upscale and pack accepted overlay clips for delivery.

The generator produces small 384x672 clips. The channel needs vertical
1080x1920 files without audio; the archive also carries a short preview cut
from one second of every clip, so the batch can be judged without opening ten
separate files.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from content_factory.constants import GENERATED_DIR
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.overlay import OverlayPackage
from content_factory.services.generator import media
from content_factory.services.generator.overlay_profiles import OverlayProfile

OUT_WIDTH = 1080
OUT_HEIGHT = 1920
PREVIEW_WIDTH = 540
PREVIEW_HEIGHT = 960
CRF = "20"
PREVIEW_CRF = "23"
PREVIEW_SECONDS = 1.0
_WORKERS = 2


def package_overlays(
    profile: OverlayProfile,
    videos: Sequence[Path],
    data_dir: Path,
) -> OverlayPackage:
    """Upscale *videos*, write the zip archive and the preview cut.

    Args:
        profile: Profile that owns the clips.
        videos: Accepted source clips in production order.
        data_dir: Runtime data directory that receives ``generated/overlays``.

    Returns:
        Paths of the archive, the preview and the upscaled clips.

    Raises:
        GeneratorError: There is nothing to pack or ffmpeg failed.
    """
    if not videos:
        raise GeneratorError(f"у профиля {profile.name} нет принятых клипов для упаковки")
    directory = data_dir / GENERATED_DIR / "overlays" / profile.slug
    upscaled_dir = directory / "1080x1920"
    upscaled_dir.mkdir(parents=True, exist_ok=True)
    targets = tuple(upscaled_dir / video.name for video in videos)
    _upscale_all(videos, targets)
    archive = _write_archive(profile, targets, directory)
    preview = _write_preview(targets, directory)
    return OverlayPackage(
        directory=directory,
        archive=archive,
        preview=preview,
        clips=targets,
    )


def _upscale_all(sources: Sequence[Path], targets: Sequence[Path]) -> None:
    """Upscale every source, reusing an existing non-empty target."""
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(_upscale_one, sources, targets))


def _upscale_one(source: Path, target: Path) -> None:
    """Upscale one clip to 1080x1920 without audio."""
    if (
        target.is_file()
        and target.stat().st_size > 0
        and target.stat().st_mtime >= source.stat().st_mtime
    ):
        return
    media.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"scale={OUT_WIDTH}:{OUT_HEIGHT}:flags=lanczos",
            "-c:v",
            "libx264",
            "-crf",
            CRF,
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            str(target),
        ]
    )


def _write_archive(
    profile: OverlayProfile,
    clips: Sequence[Path],
    directory: Path,
) -> Path:
    """Write the stored zip archive of the upscaled clips."""
    archive = directory / f"{profile.slug}_{OUT_WIDTH}x{OUT_HEIGHT}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
        for clip in clips:
            bundle.write(clip, arcname=clip.name)
    return archive


def _write_preview(clips: Sequence[Path], directory: Path) -> Path:
    """Cut one second of every clip and concatenate them into one preview."""
    parts_dir = directory / "preview_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts = tuple(parts_dir / f"part_{index:03d}.mp4" for index in range(1, len(clips) + 1))
    for source, target in zip(clips, parts, strict=True):
        if target.is_file() and target.stat().st_size > 0:
            continue
        media.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                "1",
                "-t",
                f"{PREVIEW_SECONDS:g}",
                "-i",
                str(source),
                "-vf",
                f"scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}:flags=lanczos",
                "-c:v",
                "libx264",
                "-crf",
                PREVIEW_CRF,
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(target),
            ]
        )
    listing = parts_dir / "list.txt"
    listing.write_text(
        "".join(f"file '{part.name}'\n" for part in parts),
        encoding="utf-8",
    )
    preview = directory / "preview.mp4"
    media.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(preview),
        ]
    )
    return preview


__all__ = ["OUT_HEIGHT", "OUT_WIDTH", "package_overlays"]
