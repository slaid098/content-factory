"""Stock breakdown by search topic."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.schemas.content.topics import TopicCount, TopicsResponse
from content_factory.services.delivery.metadata import load_video_meta


def collect_topics(data_dir: Path, language: str) -> TopicsResponse:
    """Count stock mp4 files by their exact ``videos.query`` topic.

    Files without a database row or with an empty query are reported as
    unclassified; a missing language directory yields an empty breakdown.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.

    Returns:
        Topic counts sorted by query and the number of unclassified files.
    """
    hashes = _stock_hashes(data_dir / CONTENT_DIR / language)
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


def _stock_hashes(directory: Path) -> list[str]:
    """Return sorted SHA-256 stems of regular mp4 files in one language."""
    if not directory.is_dir():
        return []
    return sorted(
        path.stem for path in directory.iterdir() if path.is_file() and path.suffix == ".mp4"
    )
