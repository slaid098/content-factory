"""Read-only profile registry: layout, shared folder and id safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.profiles.delivery import DeliveryInfo, TitleConfig, TitleMode
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles.registry import get_profile, list_profiles


def _make_profile(
    root: Path,
    category: str = "dating",
    name: str = "profile_2",
    **values: Any,
) -> Path:
    directory = root / "profiles" / category / name
    directory.mkdir(parents=True)
    data: dict[str, Any] = {
        "language": "ru",
        "niche": "dating",
        "width": 512,
        "height": 896,
        "audio": True,
    }
    data.update(values)
    (directory / "profile.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )
    return directory


def test_lists_profiles_sorted_by_id_and_reads_fields(tmp_path: Path) -> None:
    _make_profile(tmp_path, "other", "profile_1", language="en", niche="", audio=False)
    _make_profile(tmp_path, "dating", "profile_2")
    _make_profile(tmp_path, "dating", "profile_1")

    profiles = list_profiles(tmp_path)

    assert [profile.id for profile in profiles] == [
        "dating/profile_1",
        "dating/profile_2",
        "other/profile_1",
    ]
    first = profiles[0]
    assert first.category == "dating"
    assert first.name == "profile_1"
    assert first.language == "ru"
    assert first.niche == "dating"
    assert first.width == 512
    assert first.height == 896
    assert first.audio is True
    assert first.dir == tmp_path / "profiles" / "dating" / "profile_1"


def test_empty_niche_falls_back_to_category(tmp_path: Path) -> None:
    _make_profile(tmp_path, "auto", "profile_1", niche="")

    profile = list_profiles(tmp_path)[0]

    assert profile.niche == "auto"


def test_shared_directory_is_not_a_profile(tmp_path: Path) -> None:
    _make_profile(tmp_path, "_shared", "fonts")
    _make_profile(tmp_path, "dating", "profile_2")

    profiles = list_profiles(tmp_path)

    assert [profile.id for profile in profiles] == ["dating/profile_2"]


def test_directory_without_profile_yaml_is_skipped(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2")
    (tmp_path / "profiles" / "dating" / "draft").mkdir(parents=True)

    assert [profile.id for profile in list_profiles(tmp_path)] == ["dating/profile_2"]


def test_broken_yaml_skips_only_that_profile(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    _make_profile(tmp_path, "dating", "profile_1")
    broken = tmp_path / "profiles" / "dating" / "profile_2"
    broken.mkdir(parents=True)
    (broken / "profile.yaml").write_text("width: [\n", encoding="utf-8")

    profiles = list_profiles(tmp_path)

    assert [profile.id for profile in profiles] == ["dating/profile_1"]
    assert any(
        record["level"].name == "WARNING" and "dating/profile_2" in record["message"]
        for record in log_records
    )


def test_non_utf8_yaml_skips_only_that_profile(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    _make_profile(tmp_path, "dating", "profile_1")
    broken = tmp_path / "profiles" / "dating" / "profile_2"
    broken.mkdir(parents=True)
    (broken / "profile.yaml").write_bytes(b"width: \xff\n")

    profiles = list_profiles(tmp_path)

    assert [profile.id for profile in profiles] == ["dating/profile_1"]
    assert any(
        record["level"].name == "WARNING" and "dating/profile_2" in record["message"]
        for record in log_records
    )
    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")


def test_get_profile_rejects_shared_directory(tmp_path: Path) -> None:
    _make_profile(tmp_path, "_shared", "fonts")

    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "_shared/fonts")


def test_get_profile_requires_profile_yaml(tmp_path: Path) -> None:
    (tmp_path / "profiles" / "dating" / "draft").mkdir(parents=True)

    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/draft")


def test_invalid_profile_field_skips_only_that_profile(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_1")
    _make_profile(tmp_path, "dating", "profile_2", width=0)

    assert [profile.id for profile in list_profiles(tmp_path)] == ["dating/profile_1"]


@pytest.mark.parametrize("root", ["missing", "empty"])
def test_missing_or_empty_profiles_root_yields_empty_tuple(tmp_path: Path, root: str) -> None:
    if root == "empty":
        (tmp_path / "profiles").mkdir()

    assert list_profiles(tmp_path) == ()


def test_get_profile_returns_one_profile(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2", language="en", audio=False)

    profile = get_profile(tmp_path, "dating/profile_2")

    assert profile.id == "dating/profile_2"
    assert profile.language == "en"
    assert profile.audio is False


def test_get_profile_rejects_missing_profile(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")


@pytest.mark.parametrize(
    "profile_id",
    [
        "",
        "profile_2",
        "a/b/c",
        "dating//profile_2",
        "dating/../profile_2",
        "../dating/profile_2",
        "/etc/passwd",
        "dating\\profile_2",
        ".",
        "dating/.",
    ],
)
def test_get_profile_rejects_unsafe_ids(tmp_path: Path, profile_id: str) -> None:
    with pytest.raises(ProfileError, match="профил"):
        get_profile(tmp_path, profile_id)


def test_get_profile_rejects_invalid_language(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2", language="RU")

    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")


def test_get_profile_rejects_invalid_niche(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2", niche="Dating")

    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")


def test_get_profile_rejects_non_positive_size(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2", height=-1)

    with pytest.raises(ProfileError, match="профиль не найден") as raised:
        get_profile(tmp_path, "dating/profile_2")

    assert str(tmp_path) not in str(raised.value)


def test_registry_does_not_need_prompts_fonts_or_looks(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2")

    profile = get_profile(tmp_path, "dating/profile_2")

    assert sorted(path.name for path in profile.dir.iterdir()) == ["profile.yaml"]


def test_profile_without_delivery_is_generated_without_hashtags(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2")

    profile = get_profile(tmp_path, "dating/profile_2")

    assert profile.delivery == DeliveryInfo(
        title=TitleConfig(mode=TitleMode.GENERATED),
        hashtags=(),
    )


@pytest.mark.parametrize("delivery", [None, {}])
def test_empty_delivery_is_generated_without_hashtags(
    tmp_path: Path,
    delivery: object,
) -> None:
    _make_profile(tmp_path, "dating", "profile_2", delivery=delivery)

    assert get_profile(tmp_path, "dating/profile_2").delivery == DeliveryInfo()


def test_fixed_delivery_keeps_title_with_emoji_and_unique_hashtags(tmp_path: Path) -> None:
    _make_profile(
        tmp_path,
        "dating",
        "profile_2",
        delivery={
            "title": {
                "mode": "fixed",
                "value": "Давай знакомиться, ищу счастье ✨",
            },
            "hashtags": ["#знакомиться", "#Dating", "#dating", "познакомимся"],
        },
    )

    delivery = get_profile(tmp_path, "dating/profile_2").delivery

    assert delivery == DeliveryInfo(
        title=TitleConfig(
            mode=TitleMode.FIXED,
            value="Давай знакомиться, ищу счастье ✨",
        ),
        hashtags=("#знакомиться", "#Dating", "#познакомимся"),
    )


def test_fixed_title_collapses_whitespace(tmp_path: Path) -> None:
    _make_profile(
        tmp_path,
        "dating",
        "profile_2",
        delivery={"title": {"mode": "fixed", "value": "  Давай \n знакомиться  "}},
    )

    assert get_profile(tmp_path, "dating/profile_2").delivery.title.value == ("Давай знакомиться")


@pytest.mark.parametrize(
    "delivery",
    [
        "fixed",
        {"title": "fixed"},
        {"title": None},
        {"title": {"mode": "unknown"}},
        {"title": {"mode": "fixed"}},
        {"title": {"mode": "fixed", "value": 123}},
        {"title": {"mode": "fixed", "value": "   "}},
        {"title": {"mode": "fixed", "value": "я" * 101}},
        {"title": {"mode": "generated", "value": "Название"}},
        {"title": {"mode": "generated", "value": ""}},
        {"title": {"mode": "generated", "value": None}},
        {"hashtags": "#tag"},
        {"hashtags": [42]},
        {"hashtags": [""]},
        {"hashtags": ["#"]},
        {"hashtags": ["пробел внутри"]},
        {"hashtags": [f"#tag{index}" for index in range(11)]},
    ],
)
def test_invalid_delivery_skips_only_that_profile(
    tmp_path: Path,
    delivery: object,
) -> None:
    _make_profile(tmp_path, "dating", "profile_1")
    _make_profile(tmp_path, "dating", "profile_2", delivery=delivery)

    assert [profile.id for profile in list_profiles(tmp_path)] == ["dating/profile_1"]
    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")


def test_profile_info_without_delivery_uses_generated_default(tmp_path: Path) -> None:
    profile = ProfileInfo(
        id="dating/profile_1",
        category="dating",
        name="profile_1",
        language="ru",
        niche="dating",
        width=544,
        height=960,
        audio=True,
        dir=tmp_path / "profiles" / "dating" / "profile_1",
    )

    assert profile.delivery == DeliveryInfo()


def test_reads_profile_title_and_summary(tmp_path: Path) -> None:
    _make_profile(
        tmp_path,
        "dating",
        "profile_2",
        title="  Знакомства для 30+  ",
        summary="  Спокойные знакомства с акцентом на серьёзные отношения.  ",
    )

    profile = get_profile(tmp_path, "dating/profile_2")

    assert profile.title == "Знакомства для 30+"
    assert profile.summary == "Спокойные знакомства с акцентом на серьёзные отношения."


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_title_and_summary_are_none(
    tmp_path: Path,
    value: object,
) -> None:
    _make_profile(tmp_path, "dating", "profile_2", title=value, summary=value)

    profile = get_profile(tmp_path, "dating/profile_2")

    assert profile.title is None
    assert profile.summary is None


def test_profile_without_description_fields_has_none(tmp_path: Path) -> None:
    _make_profile(tmp_path, "dating", "profile_2")

    profile = get_profile(tmp_path, "dating/profile_2")

    assert profile.title is None
    assert profile.summary is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", 42),
        ("title", "я"),
        ("title", "я" * 81),
        ("title", "Первая строка\nвторая"),
        ("summary", 42),
        ("summary", "коротко"),
        ("summary", "я" * 301),
        ("summary", "Первая строка\nвторая"),
    ],
)
def test_invalid_description_field_skips_only_that_profile(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _make_profile(tmp_path, "dating", "profile_1")
    _make_profile(tmp_path, "dating", "profile_2", **{field: value})

    assert [profile.id for profile in list_profiles(tmp_path)] == ["dating/profile_1"]
    with pytest.raises(ProfileError, match="профиль не найден"):
        get_profile(tmp_path, "dating/profile_2")
