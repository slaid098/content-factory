"""Rotation cursor: safe read of a broken file and atomic write."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from content_factory.services.generator.rotation import (
    ROTATION_FILENAME,
    advance_cursor,
    read_cursor,
)

_NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"


def _cursor(tmp_path: Path) -> Path:
    return tmp_path / "generated" / "ru" / "dating_profile_2" / ROTATION_FILENAME


def test_missing_file_means_no_cursor(tmp_path: Path) -> None:
    assert read_cursor(_cursor(tmp_path)) is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        '{"last_look_id": 42}',
        '{"run_id": "old"}',
    ],
)
def test_unusable_file_means_no_cursor(tmp_path: Path, content: str) -> None:
    path = _cursor(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    assert read_cursor(path) is None


def test_advance_creates_directory_and_writes_three_fields(tmp_path: Path) -> None:
    path = _cursor(tmp_path)

    advance_cursor(path, "c07", _RUN_ID, _NOW)

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["last_look_id"] == "c07"
    assert payload["run_id"] == _RUN_ID
    assert datetime.fromisoformat(payload["updated_at"]) == _NOW
    assert text.endswith("\n")


def test_advance_replaces_the_previous_value(tmp_path: Path) -> None:
    path = _cursor(tmp_path)
    advance_cursor(path, "c07", "run-1", _NOW)

    advance_cursor(path, "c08", "run-2", _NOW)

    assert read_cursor(path) == "c08"
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-2"


def test_advance_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = _cursor(tmp_path)

    advance_cursor(path, "c07", _RUN_ID, _NOW)

    assert [entry.name for entry in path.parent.iterdir()] == [ROTATION_FILENAME]
