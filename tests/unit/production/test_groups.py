"""Recipe groups: profiles sharing one pipeline run."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.generator.pipeline import ArtifactKind, Layer
from content_factory.services.production.groups import group_profiles


@dataclass(frozen=True, slots=True)
class _Profile:
    """Minimal profile with a recipe."""

    name: str
    production: tuple[Layer, ...]
    language: str = "ru"


_VIDEO = Layer("vast", "vast", (), ArtifactKind.VIDEO, ("media", "clef"))
_PHOTO = Layer("card", "card", (), ArtifactKind.PHOTO, ("image",))


def _names(groups: tuple[tuple[_Profile, ...], ...]) -> list[list[str]]:
    return [[profile.name for profile in group] for group in groups]


def test_profiles_with_the_same_recipe_share_one_group() -> None:
    profiles = [
        _Profile("dating/profile_2", (_VIDEO,)),
        _Profile("dating/profile_1", (_VIDEO,)),
    ]

    groups = group_profiles(profiles)

    assert _names(groups) == [["dating/profile_2", "dating/profile_1"]]


def test_groups_follow_the_first_profile_of_each_recipe() -> None:
    profiles = [
        _Profile("a", (_PHOTO,)),
        _Profile("b", (_VIDEO,)),
        _Profile("c", (_PHOTO,)),
        _Profile("d", (_VIDEO,)),
    ]

    groups = group_profiles(profiles)

    assert _names(groups) == [["a", "c"], ["b", "d"]]


def test_a_mixed_recipe_is_one_group_of_independent_chains() -> None:
    profiles = [
        _Profile("a", (_VIDEO, _PHOTO)),
        _Profile("b", (_VIDEO, _PHOTO)),
    ]

    groups = group_profiles(profiles)

    assert _names(groups) == [["a", "b"]]
    assert groups[0][0].production == (_VIDEO, _PHOTO)


@pytest.mark.parametrize(
    "other",
    [
        Layer("other", "vast", (), ArtifactKind.VIDEO, ("media", "clef")),
        Layer("vast", "other", (), ArtifactKind.VIDEO, ("media", "clef")),
        Layer("vast", "vast", ("source",), ArtifactKind.VIDEO, ("media", "clef")),
        Layer("vast", "vast", (), ArtifactKind.PHOTO, ("media", "clef")),
        Layer("vast", "vast", (), ArtifactKind.VIDEO, ("clef", "media")),
    ],
)
def test_any_layer_field_changes_the_recipe_signature(other: Layer) -> None:
    profiles = [
        _Profile("a", (_VIDEO,)),
        _Profile("b", (other,)),
    ]

    assert _names(group_profiles(profiles)) == [["a"], ["b"]]


def test_layer_order_changes_the_recipe_signature() -> None:
    profiles = [
        _Profile("a", (_VIDEO, _PHOTO)),
        _Profile("b", (_PHOTO, _VIDEO)),
    ]

    assert _names(group_profiles(profiles)) == [["a"], ["b"]]


def test_empty_input_recipe_or_repeated_layer_name_is_rejected() -> None:
    with pytest.raises(ProfileError, match="ни один профиль"):
        group_profiles([])

    with pytest.raises(ProfileError, match="пустой рецепт"):
        group_profiles([_Profile("a", ())])

    with pytest.raises(ProfileError, match="повтор имени слоя"):
        group_profiles([_Profile("a", (_VIDEO, _VIDEO))])


def test_profiles_with_the_same_directory_slug_are_rejected() -> None:
    profiles = [
        _Profile("dating/p1", (_VIDEO,)),
        _Profile("dating_p1", (_VIDEO,)),
    ]

    with pytest.raises(ProfileError, match="одинаковый каталог"):
        group_profiles(profiles)


def test_same_slug_in_different_languages_is_not_a_conflict() -> None:
    profiles = [
        _Profile("dating/p1", (_VIDEO,), language="ru"),
        _Profile("dating_p1", (_VIDEO,), language="en"),
    ]

    assert _names(group_profiles(profiles)) == [["dating/p1", "dating_p1"]]
