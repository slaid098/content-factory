"""MP4 checks of a finished video."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.exceptions.generator import QaError
from content_factory.services.generator import qa
from content_factory.services.generator.media import _MediaInfo


def _info(
    path: Path,
    *,
    duration: float = 5.0,
    size: int = 20_000,
    has_video: bool = True,
    has_audio: bool = True,
) -> _MediaInfo:
    return _MediaInfo(
        path=path,
        duration=duration,
        width=512,
        height=896,
        has_video=has_video,
        has_audio=has_audio,
        size=size,
    )


def test_valid_video_returns_its_facts(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "video.mp4"
    info = _info(path)
    monkeypatch.setattr(qa, "probe", lambda path: info)

    assert qa.check_video(path, 5.0) is info


def test_unreadable_video_is_an_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(qa, "probe", lambda path: None)

    with pytest.raises(QaError, match="не удалось прочитать"):
        qa.check_video(tmp_path / "video.mp4", 5.0)


def test_too_small_and_silent_files_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"

    with pytest.raises(QaError, match="слишком мал"):
        qa._check_media(_info(path, size=10), 5.0)

    with pytest.raises(QaError, match="нет видеопотока"):
        qa._check_media(_info(path, has_video=False), 5.0)


def test_duration_tolerance_uses_the_larger_of_ratio_and_minimum(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"

    qa._check_media(_info(path, duration=5.6), 5.0)
    qa._check_media(_info(path, duration=0.5), 1.0)

    with pytest.raises(QaError, match="вне допуска"):
        qa._check_media(_info(path, duration=6.0), 5.0)


def test_expected_audio_requires_a_track(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"

    qa._check_media(_info(path, has_audio=False), 5.0, expect_audio=False)
    qa._check_media(_info(path), 5.0, expect_audio=True)

    with pytest.raises(QaError, match="нет аудиодорожки"):
        qa._check_media(_info(path, has_audio=False), 5.0, expect_audio=True)


def test_check_video_passes_the_audio_expectation(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "video.mp4"
    info = _info(path, has_audio=False)
    monkeypatch.setattr(qa, "probe", lambda path: info)

    qa.check_video(path, 5.0)

    with pytest.raises(QaError, match="нет аудиодорожки"):
        qa.check_video(path, 5.0, expect_audio=True)
