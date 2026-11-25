"""Shared builders for profile API, delivery and preview tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME, PROFILES_DIR
from content_factory.db.connection import connect
from content_factory.db.profiles import upsert_refill
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.services.stock.layout import photo_sidecar

_FINISHED = datetime(2026, 5, 1, 12, 30, tzinfo=UTC)
_STARTED = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def add_profile(
    data_dir: Path,
    category: str = "dating",
    name: str = "profile_1",
    **values: Any,
) -> Path:
    """Create ``profiles/<category>/<name>/profile.yaml``."""
    directory = data_dir / PROFILES_DIR / category / name
    directory.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "language": "ru",
        "niche": category,
        "width": 544,
        "height": 960,
        "audio": True,
    }
    data.update(values)
    (directory / "profile.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    return directory


def add_profile_video(
    data_dir: Path,
    category: str = "dating",
    name: str = "profile_1",
    *,
    niche: str | None = None,
    language: str = "ru",
    filename: str | None = None,
    data: bytes = b"video",
    mtime: float | None = None,
) -> Path:
    """Create one generated mp4 named after its profile slug."""
    directory = data_dir / CONTENT_DIR / (niche or category) / language
    path = directory / (filename or f"{category}_{name}_run_001.mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def add_profile_photo(
    data_dir: Path,
    category: str = "dating",
    name: str = "profile_1",
    *,
    niche: str | None = None,
    language: str = "ru",
    filename: str | None = None,
    data: bytes = b"\xff\xd8photo",
    mtime: float | None = None,
    sidecar: bool = True,
) -> Path:
    """Create one generated photo post named after its profile slug.

    A readable text sidecar is written by default, because the stock index
    counts only photos whose sidecar can be read.
    """
    directory = data_dir / CONTENT_DIR / (niche or category) / language
    path = directory / (filename or f"{category}_{name}_run_001.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if sidecar:
        photo_sidecar(path).write_text(json.dumps({"text": "текст поста"}), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def write_run_report(
    output_dir: Path,
    category: str = "dating",
    name: str = "profile_1",
    *,
    language: str = "ru",
    run_id: str = "run-1",
    started_at: datetime = _STARTED,
    finished_at: datetime | None = _FINISHED,
    status: str = "done",
    items: list[dict[str, Any]] | None = None,
    generated: int = 0,
    failed: int = 0,
) -> Path:
    """Write one generator ``run.json`` under ``<output>/<lang>/<slug>/<run>``."""
    directory = output_dir / language / f"{category}_{name}" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": None if finished_at is None else finished_at.isoformat(),
        "status": status,
        "profiles": [f"{category}/{name}"],
        "count": len(items or ()),
        "items": items or [],
        "totals": {"generated": generated, "failed": failed, "bytes": 0},
    }
    path = directory / "run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def set_profile_refill(
    data_dir: Path,
    profile_id: str,
    *,
    enabled: bool,
    target: int | None = None,
    refill_at: int | None = None,
) -> None:
    """Persist one profile settings row in the operational database."""
    connection = connect(data_dir / DB_DIR / DB_FILENAME)
    try:
        upsert_refill(
            connection,
            profile_id,
            ProfileRefill(enabled=enabled, target=target, refill_at=refill_at),
            _STARTED.isoformat(),
        )
        connection.commit()
    finally:
        connection.close()
