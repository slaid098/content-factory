"""Stock breakdown by search topic."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.schemas.content.topics import TopicCount, TopicsResponse
from content_factory.services.delivery.metadata import load_video_meta
from content_factory.services.stock import language_files


def collect_topics(data_dir: Path, language: str) -> TopicsResponse:
    """Count General stock mp4 files by their exact ``videos.query`` topic.

    Only General videos are counted: files with a profile id are left out of
    the breakdown and of ``unclassified``. Files without a database row or with
    an empty query are reported as unclassified. Any missing content directory
    yields an empty breakdown.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two- or three-letter language code.

    Returns:
        Topic counts sorted by query and the number of unclassified files.
    """
    hashes = _stock_hashes(data_dir / CONTENT_DIR, language)
    meta = load_video_meta(data_dir / DB_DIR / DB_FILENAME, hashes)
    counts: Counter[str] = Counter()
    unclassified = 0
    for sha256 in hashes:
        item = meta.get(sha256)
        if item is None or not item.query:
            unclassified += 1
            continue
        counts[item.query] += 1
    topics = tuple(
        TopicCount(query=query, count=count) for query, count in sorted(counts.items()) if count > 0
    )
    return TopicsResponse(language=language, topics=topics, unclassified=unclassified)


def _stock_hashes(content_dir: Path, language: str) -> list[str]:
    """Return SHA-256 stems of the General video files of one language."""
    return [
        item.path.stem for item in language_files(content_dir, language) if item.profile_id is None
    ]
