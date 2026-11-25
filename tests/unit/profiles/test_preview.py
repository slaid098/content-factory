"""Preview extraction, cache freshness and error mapping."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.unit.common.profile_helpers import add_profile_photo, add_profile_video

from content_factory.constants import CONTENT_DIR, PREVIEW_DIR
from content_factory.exceptions.errors import ProfilePreviewError
from content_factory.schemas.profiles.preview import ProfilePreview
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles import preview


def _profile(tmp_path: Path, *, language: str = "ru", niche: str = "dating") -> ProfileInfo:
    return ProfileInfo(
        id="dating/profile_1",
        category="dating",
        name="profile_1",
        language=language,
        niche=niche,
        width=544,
        height=960,
        audio=True,
        dir=tmp_path / "profiles" / "dating" / "profile_1",
    )


def _cache(tmp_path: Path) -> Path:
    return tmp_path / PREVIEW_DIR / "dating_profile_1.jpg"


def _fake_run(
    calls: list[tuple[list[str], dict[str, Any]]],
    *,
    duration: str = "10.0",
) -> Any:
    def run(args: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((args, kwargs))
        if args[0] == "ffprobe":
            return SimpleNamespace(stdout=duration)
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8frame")
        return SimpleNamespace(stdout="")

    return run


def test_extract_preview_probes_duration_and_takes_middle_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    target = tmp_path / "preview" / "frame.jpg"
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(preview.subprocess, "run", _fake_run(calls))

    preview.extract_preview(source, target)

    assert target.read_bytes() == b"\xff\xd8frame"
    probe_args, probe_kwargs = calls[0]
    assert probe_args[0] == "ffprobe"
    assert str(source) in probe_args
    ffmpeg_args, ffmpeg_kwargs = calls[1]
    assert ffmpeg_args[0] == "ffmpeg"
    assert ffmpeg_args[ffmpeg_args.index("-ss") + 1] == "5.000"
    assert ffmpeg_args[ffmpeg_args.index("-frames:v") + 1] == "1"
    assert ffmpeg_args[-1] == str(target)
    for kwargs in (probe_kwargs, ffmpeg_kwargs):
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 30
        assert "shell" not in kwargs


def test_extract_preview_rejects_unparsable_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(preview.subprocess, "run", _fake_run(calls, duration=""))

    with pytest.raises(ProfilePreviewError, match="длительность"):
        preview.extract_preview(tmp_path / "source.mp4", tmp_path / "frame.jpg")

    assert [args[0] for args, _ in calls] == ["ffprobe"]


def test_extract_preview_rejects_empty_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(preview.subprocess, "run", _fake_run(calls, duration="0\n"))

    with pytest.raises(ProfilePreviewError, match="пустую длительность"):
        preview.extract_preview(tmp_path / "source.mp4", tmp_path / "frame.jpg")


def test_profile_preview_without_video_returns_none(tmp_path: Path) -> None:
    assert preview.profile_preview(tmp_path, _profile(tmp_path)) is None


def test_profile_preview_builds_cache_once_and_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = add_profile_video(tmp_path, data=b"video", mtime=1_000)
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8cached")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    first = preview.profile_preview(tmp_path, _profile(tmp_path))
    second = preview.profile_preview(tmp_path, _profile(tmp_path))

    assert first == ProfilePreview(_cache(tmp_path), "image/jpeg")
    assert second == first
    assert first is not None
    assert first.path.read_bytes() == b"\xff\xd8cached"
    assert calls == [source]
    assert list((tmp_path / PREVIEW_DIR).glob("*.tmp.jpg")) == []


def test_profile_preview_rebuilds_when_source_is_newer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = add_profile_video(tmp_path, mtime=2_000)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"old")
    os.utime(cache, (900, 900))
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.write_bytes(b"\xff\xd8new")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    result = preview.profile_preview(tmp_path, _profile(tmp_path))

    assert result == ProfilePreview(cache, "image/jpeg")
    assert cache.read_bytes() == b"\xff\xd8new"
    assert calls == [source]


def test_profile_preview_prefers_the_newest_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_video(tmp_path, filename="dating_profile_1_old.mp4", mtime=1_000)
    newest = add_profile_video(tmp_path, filename="dating_profile_1_new.mp4", mtime=3_000)
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8new")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    preview.profile_preview(tmp_path, _profile(tmp_path))

    assert calls == [newest]


def test_profile_preview_ignores_other_profiles_and_foreign_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_video(tmp_path, name="profile_2", mtime=3_000)
    (tmp_path / CONTENT_DIR / "dating" / "ru").mkdir(parents=True, exist_ok=True)
    (tmp_path / CONTENT_DIR / "dating" / "ru" / "supplier.mp4").write_bytes(b"x")
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.write_bytes(b"\xff\xd8new")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) is None
    assert calls == []


def test_profile_preview_logs_source_and_stderr_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    source = add_profile_video(tmp_path, mtime=1_000)

    def _fail(_source: Path, _target: Path) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"boom")

    monkeypatch.setattr(preview, "extract_preview", _fail)

    with pytest.raises(ProfilePreviewError, match="preview unavailable"):
        preview.profile_preview(tmp_path, _profile(tmp_path))

    assert not _cache(tmp_path).exists()
    assert list((tmp_path / PREVIEW_DIR).glob("*.tmp.jpg")) == []
    records = [record for record in log_records if record["function"] == "profile_preview"]
    assert records[-1]["extra"] == {
        "path": str(source),
        "stderr": "boom",
        "error_type": "CalledProcessError",
    }


def test_profile_preview_logs_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    add_profile_video(tmp_path, mtime=1_000)
    original_unlink = Path.unlink

    def _deny_staging(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith(".tmp.jpg"):
            raise OSError("permission denied")
        original_unlink(self, *args, **kwargs)

    def _fail(_source: Path, _target: Path) -> None:
        raise OSError("ffmpeg is missing")

    monkeypatch.setattr(Path, "unlink", _deny_staging)
    monkeypatch.setattr(preview, "extract_preview", _fail)

    with pytest.raises(ProfilePreviewError, match="preview unavailable"):
        preview.profile_preview(tmp_path, _profile(tmp_path))

    messages = [
        record["message"] for record in log_records if record["function"] == "profile_preview"
    ]
    assert any("Не удалось удалить временное превью" in message for message in messages)


def test_profile_preview_skips_unreadable_source_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = add_profile_video(tmp_path, filename="dating_profile_1_good.mp4", mtime=2_000)
    bad = add_profile_video(tmp_path, filename="dating_profile_1_bad.mp4", mtime=3_000)
    original_stat = Path.stat
    calls: list[Path] = []

    def _deny_bad(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == bad:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8cached")

    original_is_file = Path.is_file

    def _present(self: Path) -> bool:
        if self == bad:
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "stat", _deny_bad)
    monkeypatch.setattr(Path, "is_file", _present)
    monkeypatch.setattr(preview, "extract_preview", _extract)

    preview.profile_preview(tmp_path, _profile(tmp_path))

    assert calls == [good]


def test_profile_preview_logs_unreadable_content_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    directory = tmp_path / CONTENT_DIR / "dating" / "ru"
    directory.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def _deny(self: Path) -> object:
        if self == directory:
            raise PermissionError("permission denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _deny)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) is None
    records = [record for record in log_records if record["function"] == "_newest_source"]
    assert records[-1]["level"].name == "DEBUG"
    assert records[-1]["extra"]["path"] == str(directory)


def test_profile_preview_rebuilds_when_cache_stat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_video(tmp_path, mtime=1_000)
    cache = _cache(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"old")
    original_stat = Path.stat
    calls: list[Path] = []

    def _deny_cache(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == cache:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.write_bytes(b"\xff\xd8new")

    monkeypatch.setattr(Path, "stat", _deny_cache)
    monkeypatch.setattr(preview, "extract_preview", _extract)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) == ProfilePreview(
        cache, "image/jpeg"
    )
    assert calls


def test_profile_preview_logs_text_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    add_profile_video(tmp_path, mtime=1_000)

    def _fail(_source: Path, _target: Path) -> None:
        error = OSError("ffmpeg failed")
        error.stderr = "text stderr"  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(preview, "extract_preview", _fail)

    with pytest.raises(ProfilePreviewError, match="preview unavailable"):
        preview.profile_preview(tmp_path, _profile(tmp_path))

    records = [record for record in log_records if record["function"] == "profile_preview"]
    assert records[-1]["extra"]["stderr"] == "text stderr"


def test_profile_preview_breaks_tie_by_file_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_video(tmp_path, filename="dating_profile_1_aaa.mp4", mtime=2_000)
    latest = add_profile_video(tmp_path, filename="dating_profile_1_zzz.mp4", mtime=2_000)
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8cached")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    preview.profile_preview(tmp_path, _profile(tmp_path))

    assert calls == [latest]


def test_profile_preview_falls_back_to_the_newest_photo_without_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_photo(tmp_path, filename="dating_profile_1_old.jpg", mtime=1_000)
    newest = add_profile_photo(tmp_path, filename="dating_profile_1_new.jpg", mtime=2_000)
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)

    monkeypatch.setattr(preview, "extract_preview", _extract)

    result = preview.profile_preview(tmp_path, _profile(tmp_path))

    assert result == ProfilePreview(newest, "image/jpeg")
    assert calls == []
    assert not (tmp_path / PREVIEW_DIR).exists()


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".webp", "image/webp"),
    ],
)
def test_profile_preview_serves_photo_media_type_by_suffix(
    tmp_path: Path,
    suffix: str,
    media_type: str,
) -> None:
    photo = add_profile_photo(
        tmp_path,
        filename=f"dating_profile_1_post{suffix}",
        data=b"image",
    )

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) == ProfilePreview(
        photo, media_type
    )


def test_profile_preview_breaks_photo_tie_by_file_name(tmp_path: Path) -> None:
    add_profile_photo(tmp_path, filename="dating_profile_1_aaa.jpg", mtime=2_000)
    latest = add_profile_photo(tmp_path, filename="dating_profile_1_zzz.jpg", mtime=2_000)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) == ProfilePreview(
        latest, "image/jpeg"
    )


def test_profile_preview_prefers_video_over_a_newer_photo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = add_profile_video(tmp_path, mtime=1_000)
    add_profile_photo(tmp_path, mtime=3_000)
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"\xff\xd8frame")

    monkeypatch.setattr(preview, "extract_preview", _extract)

    result = preview.profile_preview(tmp_path, _profile(tmp_path))

    assert result == ProfilePreview(_cache(tmp_path), "image/jpeg")
    assert calls == [video]


def test_profile_preview_does_not_fall_back_to_photo_on_video_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_video(tmp_path, mtime=1_000)
    add_profile_photo(tmp_path, mtime=3_000)

    def _fail(_source: Path, _target: Path) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"boom")

    monkeypatch.setattr(preview, "extract_preview", _fail)

    with pytest.raises(ProfilePreviewError, match="preview unavailable"):
        preview.profile_preview(tmp_path, _profile(tmp_path))


def test_profile_preview_serves_a_photo_without_sidecar(tmp_path: Path) -> None:
    photo = add_profile_photo(tmp_path, sidecar=False, mtime=1_000)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) == ProfilePreview(
        photo, "image/jpeg"
    )


def test_profile_preview_ignores_foreign_photos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile_photo(tmp_path, name="profile_2", mtime=3_000)
    directory = tmp_path / CONTENT_DIR / "dating" / "ru"
    (directory / "supplier.jpg").write_bytes(b"x")
    (directory / "dating_profile_1_run_001.json").write_text("{}", encoding="utf-8")
    calls: list[Path] = []

    def _extract(source_path: Path, target_path: Path) -> None:
        calls.append(source_path)

    monkeypatch.setattr(preview, "extract_preview", _extract)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) is None
    assert calls == []


def test_profile_preview_skips_unreadable_photo_stat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = add_profile_photo(tmp_path, filename="dating_profile_1_good.jpg", mtime=2_000)
    bad = add_profile_photo(tmp_path, filename="dating_profile_1_bad.jpg", mtime=3_000)
    original_stat = Path.stat

    def _deny_bad(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == bad:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    original_is_file = Path.is_file

    def _present(self: Path) -> bool:
        if self == bad:
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "stat", _deny_bad)
    monkeypatch.setattr(Path, "is_file", _present)

    assert preview.profile_preview(tmp_path, _profile(tmp_path)) == ProfilePreview(
        good, "image/jpeg"
    )
