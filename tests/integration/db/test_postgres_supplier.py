"""Supplier repository against PostgreSQL: partial UNIQUE and transactions."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.video import (
    STATUS_DOWNLOADED,
    STATUS_FOUND,
    DownloadedVideo,
    Video,
    VideoMetadata,
)
from content_factory.services.supplier.repository import Repository

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _video(video_id: str, author: str = "alice") -> Video:
    return Video(
        video_id=video_id,
        url=f"https://www.tiktok.com/@{author}/video/{video_id}",
        author=author,
        query="q",
        language="ru",
    )


def _metadata(video: Video) -> VideoMetadata:
    return VideoMetadata(
        video_id=video.video_id,
        url=video.url,
        author_id=video.author,
        author_name=video.author,
        description="-",
        views=None,
        followers=None,
        duration=None,
        is_aigc=False,
        aigc_description="",
        width=None,
        height=None,
    )


def test_partial_unique_index_keeps_one_downloaded_video_per_author(pg: Path) -> None:
    repo = Repository(pg)
    first = _video("1")
    second = _video("2")
    assert repo.add_found(first)
    assert repo.add_found(second)
    repo.save_download(DownloadedVideo(video=first, path="videos/1.mp4", sha256=_SHA_A, size=1))

    with pytest.raises(SupplierError, match="не удалось записать скачивание"):
        repo.save_download(
            DownloadedVideo(video=second, path="videos/2.mp4", sha256=_SHA_B, size=1)
        )

    assert repo.used_authors() == {"alice"}
    assert [item.video_id for item in repo.list_videos(STATUS_DOWNLOADED)] == ["1"]
    assert [item.video_id for item in repo.list_videos(STATUS_FOUND)] == []


def test_save_download_rolls_back_every_statement(pg: Path) -> None:
    repo = Repository(pg)
    first = _video("1")
    second = _video("2", author="bob")
    assert repo.add_found(first)
    assert repo.add_found(second)
    repo.save_decision(first, _metadata(first), FilterDecision(True))
    repo.save_decision(second, _metadata(second), FilterDecision(True))
    repo.save_download(DownloadedVideo(video=first, path="videos/1.mp4", sha256=_SHA_A, size=1))

    with pytest.raises(SupplierError, match="не удалось записать скачивание"):
        repo.save_download(
            DownloadedVideo(video=second, path="videos/2.mp4", sha256=_SHA_A, size=1)
        )

    assert repo.has_sha256(_SHA_A) is True
    assert repo.has_sha256(_SHA_B) is False
    assert [item.video_id for item in repo.list_videos(STATUS_DOWNLOADED)] == ["1"]
    assert [item.video_id for item in repo.list_videos("approved")] == ["2"]


def test_repository_roundtrip(pg: Path) -> None:
    repo = Repository(pg)
    video = _video("1")
    assert repo.add_found(video) is True
    assert repo.add_found(video) is False
    repo.save_rejection(video, "author_used")

    assert repo.decided_video_ids() == {"1"}
    assert repo.list_videos() == [video]
    with pytest.raises(ValueError, match="неизвестный статус"):
        repo.list_videos("unknown")
