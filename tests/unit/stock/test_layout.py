"""Stock layout: nested and flat reading, migration and profile prefixes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from tests.unit.common.delivery_helpers import touch_mp4, touch_photo

from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.stock.layout import StockFile
from content_factory.services.stock import language_files, migrate, stock_files
from content_factory.services.stock.layout import (
    migrate_flat_layout,
    photo_sidecar,
    photo_text,
    profile_slugs,
)


def _profile(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "profile.yaml").write_text("", encoding="utf-8")


def test_stock_files_reads_nested_and_flat_layouts(tmp_path: Path) -> None:
    content = tmp_path / "content"
    flat = touch_mp4(content / "ru" / "flat.mp4")
    supplier = touch_mp4(content / "general" / "ru" / "supplier.mp4")
    generated = touch_mp4(content / "dating" / "ru" / "dating_profile_1_run_001.mp4")
    english = touch_mp4(content / "dating" / "en" / "english.mp4")

    files = stock_files(content)

    assert files == [
        StockFile("dating", "en", english, None),
        StockFile("dating", "ru", generated, None),
        StockFile("general", "ru", flat, None),
        StockFile("general", "ru", supplier, None),
    ]


def test_stock_files_ignores_foreign_entries_and_deep_directories(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "notes.txt").write_text("not a video", encoding="utf-8")
    (content / "long-name").mkdir()
    touch_mp4(content / "long-name" / "video.mp4")
    (content / "ru").mkdir()
    touch_mp4(content / "ru" / "deeper" / "inside.mp4")
    touch_mp4(content / "ru" / "notes.txt")

    assert stock_files(content) == []
    assert stock_files(tmp_path / "missing") == []


def test_stock_files_warns_about_invalid_directory_names(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    content = tmp_path / "content"
    touch_mp4(content / "Dating" / "ru" / "video.mp4")
    touch_mp4(content / "dating" / "RU" / "video.mp4")

    assert stock_files(content) == []
    messages = [record["message"] for record in log_records]
    assert any("Dating" in message for message in messages)
    assert any("RU" in message for message in messages)


def test_stock_files_counts_a_shared_file_once(tmp_path: Path) -> None:
    content = tmp_path / "content"
    flat = touch_mp4(content / "ru" / "same.mp4")
    nested = content / "general" / "ru" / "same.mp4"
    nested.parent.mkdir(parents=True)
    os.link(flat, nested)

    files = stock_files(content)

    assert files == [StockFile("general", "ru", nested, None)]


def test_language_files_filters_one_language(tmp_path: Path) -> None:
    content = tmp_path / "content"
    touch_mp4(content / "ru" / "a.mp4")
    touch_mp4(content / "general" / "ru" / "b.mp4")
    touch_mp4(content / "general" / "en" / "c.mp4")

    assert [item.path.name for item in language_files(content, "ru")] == ["a.mp4", "b.mp4"]
    assert language_files(content, "de") == []


def test_stock_files_recognizes_video_and_photo_suffixes(tmp_path: Path) -> None:
    content = tmp_path / "content"
    directory = content / "dating" / "ru"
    touch_mp4(directory / "a.mp4")
    touch_mp4(directory / "b.mov")
    touch_mp4(directory / "c.webm")
    touch_mp4(directory / "d.mkv")
    touch_photo(directory / "e.jpg", text="Текст")
    touch_photo(directory / "f.jpeg")
    touch_photo(directory / "g.png")
    touch_photo(directory / "h.webp")
    touch_mp4(directory / "i.gif")
    touch_mp4(directory / "notes.txt")

    files = stock_files(content, content_type=None)

    assert [(item.path.name, item.content_type) for item in files] == [
        ("a.mp4", ContentType.VIDEO),
        ("b.mov", ContentType.VIDEO),
        ("c.webm", ContentType.VIDEO),
        ("d.mkv", ContentType.VIDEO),
        ("e.jpg", ContentType.PHOTO),
        ("f.jpeg", ContentType.PHOTO),
        ("g.png", ContentType.PHOTO),
        ("h.webp", ContentType.PHOTO),
    ]


def test_stock_files_keeps_a_video_and_a_photo_with_the_same_name(tmp_path: Path) -> None:
    content = tmp_path / "content" / "dating" / "ru"
    video = touch_mp4(content / "same.mp4", b"video")
    photo = touch_photo(content / "same.jpg", b"photo", text="Текст")

    files = stock_files(content.parents[1], content_type=None)

    assert [(item.path, item.content_type) for item in files] == [
        (photo, ContentType.PHOTO),
        (video, ContentType.VIDEO),
    ]


def test_stock_files_skips_a_photo_with_a_broken_sidecar(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    directory = tmp_path / "content" / "dating" / "ru"
    touch_photo(directory / "broken.jpg")
    sidecar = directory / "broken.json"
    sidecar.write_text("{not json", encoding="utf-8")
    good = touch_photo(directory / "good.jpg", text="Текст")

    files = stock_files(tmp_path / "content", content_type=ContentType.PHOTO)

    assert [item.path for item in files] == [good]
    records = [record for record in log_records if record["function"] == "_sidecar_is_readable"]
    assert [record["level"].name for record in records] == ["WARNING"]
    assert records[0]["extra"]["path"] == str(sidecar)
    assert "{not json" not in records[0]["message"]


@pytest.mark.parametrize(
    ("sidecar", "expected"),
    [
        (None, ""),
        ("{}", ""),
        ('{"text": ""}', ""),
        ('{"text": "Текст поста"}', "Текст поста"),
    ],
)
def test_photo_text_reads_the_sidecar_or_returns_empty(
    tmp_path: Path,
    sidecar: str | None,
    expected: str,
) -> None:
    photo = tmp_path / "post.jpg"
    photo.write_bytes(b"photo")
    if sidecar is not None:
        photo_sidecar(photo).write_text(sidecar, encoding="utf-8")

    assert photo_text(photo) == expected


@pytest.mark.parametrize("sidecar", ["not json", "[]", '{"text": 1}'])
def test_photo_text_rejects_a_malformed_sidecar(tmp_path: Path, sidecar: str) -> None:
    photo = tmp_path / "post.jpg"
    photo.write_bytes(b"photo")
    photo_sidecar(photo).write_text(sidecar, encoding="utf-8")

    with pytest.raises(ValueError):
        photo_text(photo)


def test_language_files_filters_by_content_type(tmp_path: Path) -> None:
    content = tmp_path / "content"
    touch_mp4(content / "ru" / "a.mp4")
    touch_photo(content / "ru" / "b.jpg", text="Текст")
    touch_photo(content / "general" / "ru" / "c.png")

    assert [item.path.name for item in language_files(content, "ru")] == ["a.mp4"]
    assert [
        item.path.name for item in language_files(content, "ru", content_type=ContentType.PHOTO)
    ] == ["b.jpg", "c.png"]
    assert [item.path.name for item in language_files(content, "ru", content_type=None)] == [
        "a.mp4",
        "b.jpg",
        "c.png",
    ]


def test_profile_slugs_skips_shared_profiles(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "dating/profile_1")
    (profiles / "_shared" / "fonts").mkdir(parents=True)
    (profiles / "dating" / "broken").mkdir()

    assert profile_slugs(profiles) == {"dating_profile_1": "dating/profile_1"}


def test_profile_slugs_drops_an_ambiguous_slug(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "a_b/c")
    _profile(profiles, "a/b_c")
    _profile(profiles, "dating/profile_2")

    slugs = profile_slugs(profiles)

    assert slugs == {"dating_profile_2": "dating/profile_2"}
    records = [record for record in log_records if record["function"] == "_conflicting_profile_ids"]
    assert [record["level"].name for record in records] == ["WARNING"]
    assert records[0]["extra"] == {
        "value": "a_b_c",
        "profiles": ["a/b_c", "a_b/c"],
    }


def test_profile_slugs_keeps_the_same_name_in_two_categories(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "dating/profile_1")
    _profile(profiles, "overlay/profile_1")

    assert profile_slugs(profiles) == {
        "dating_profile_1": "dating/profile_1",
        "overlay_profile_1": "overlay/profile_1",
    }


def test_profile_slugs_keeps_the_same_name_in_three_categories(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "dating/profile_1")
    _profile(profiles, "overlay/profile_1")
    _profile(profiles, "general/profile_1")

    assert profile_slugs(profiles) == {
        "dating_profile_1": "dating/profile_1",
        "general_profile_1": "general/profile_1",
        "overlay_profile_1": "overlay/profile_1",
    }


def test_stock_files_attributes_a_name_shared_across_categories(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "dating/profile_1")
    _profile(profiles, "overlay/profile_1")
    content = tmp_path / "content"
    dating = touch_mp4(content / "dating" / "ru" / "dating_profile_1_run_001.mp4")
    overlay = touch_mp4(content / "overlay" / "ru" / "overlay_profile_1_run_001.mp4")

    files = stock_files(content)

    assert files == [
        StockFile("dating", "ru", dating, "dating/profile_1"),
        StockFile("overlay", "ru", overlay, "overlay/profile_1"),
    ]


def test_stock_files_matches_profile_id_on_underscore_boundary(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _profile(profiles, "dating/profile_1")
    _profile(profiles, "dating/profile_10")
    content = tmp_path / "content"
    first = touch_mp4(content / "dating" / "ru" / "dating_profile_1_run_001.mp4")
    tenth = touch_mp4(content / "dating" / "ru" / "dating_profile_10_run_001.mp4")
    unknown = touch_mp4(content / "dating" / "ru" / "other.mp4")

    files = stock_files(content)

    assert files == [
        StockFile("dating", "ru", tenth, "dating/profile_10"),
        StockFile("dating", "ru", first, "dating/profile_1"),
        StockFile("dating", "ru", unknown, None),
    ]


def test_migrate_flat_layout_moves_files_and_repeats_with_zero(tmp_path: Path) -> None:
    content = tmp_path / "content"
    first = touch_mp4(content / "ru" / "a.mp4", b"a")
    second = touch_mp4(content / "ru" / "b.mp4", b"b")
    touch_mp4(content / "ru" / "notes.txt")

    assert migrate_flat_layout(tmp_path) == 2

    assert not first.exists()
    assert not second.exists()
    assert (content / "general" / "ru" / "a.mp4").read_bytes() == b"a"
    assert (content / "general" / "ru" / "b.mp4").read_bytes() == b"b"
    assert (content / "ru" / "notes.txt").is_file()
    assert migrate_flat_layout(tmp_path) == 0


def test_migrate_flat_layout_keeps_a_name_conflict(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    content = tmp_path / "content"
    flat = touch_mp4(content / "ru" / "same.mp4", b"flat")
    nested = touch_mp4(content / "general" / "ru" / "same.mp4", b"nested")

    assert migrate_flat_layout(tmp_path) == 0

    assert flat.read_bytes() == b"flat"
    assert nested.read_bytes() == b"nested"
    records = [record for record in log_records if record["function"] == "_warn_conflict"]
    assert [record["level"].name for record in records] == ["WARNING"]
    assert records[0]["extra"] == {"source": str(flat), "destination": str(nested)}


def test_migrate_flat_layout_ignores_missing_and_invalid_directories(tmp_path: Path) -> None:
    assert migrate_flat_layout(tmp_path) == 0
    content = tmp_path / "content"
    touch_mp4(content / "RU" / "video.mp4")
    touch_mp4(content / "notes.txt")

    assert migrate_flat_layout(tmp_path) == 0
    assert (content / "RU" / "video.mp4").is_file()


def test_migrate_module_returns_the_moved_count(tmp_path: Path) -> None:
    touch_mp4(tmp_path / "content" / "ru" / "a.mp4")

    assert migrate.main([str(tmp_path)]) == 1
    assert migrate.main([str(tmp_path)]) == 0


def test_migrate_module_requires_a_data_dir() -> None:
    with pytest.raises(SystemExit) as error:
        migrate.main([])

    assert error.value.code == 2
