"""Grouping of profiles that share one production recipe."""

from __future__ import annotations

from collections.abc import Sequence

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.generator.pipeline import Layer, RecipeProfile
from content_factory.utils.run_id import profile_slug


def group_profiles[P: RecipeProfile](profiles: Sequence[P]) -> tuple[tuple[P, ...], ...]:
    """Group profiles by the exact signature of their recipe layers.

    Layer names, producers, inputs, outputs and checks all take part in the
    signature. Groups keep the order of the first profile of each recipe, and
    profiles inside a group keep their input order.

    Args:
        profiles: Loaded profiles in the order the flow received them.

    Returns:
        One tuple of profiles per distinct recipe.

    Raises:
        ProfileError: The input is empty, a recipe is empty, or a layer name
            repeats inside one recipe.
    """
    if not profiles:
        raise ProfileError("не указан ни один профиль")
    _validate_unique_dirs(profiles)
    groups: dict[tuple[Layer, ...], list[P]] = {}
    for profile in profiles:
        layers = tuple(profile.production)
        _validate_recipe(profile.name, layers)
        groups.setdefault(layers, []).append(profile)
    return tuple(tuple(group) for group in groups.values())


def _validate_unique_dirs(profiles: Sequence[RecipeProfile]) -> None:
    """Reject profiles that would share one output directory.

    Raises:
        ProfileError: Two profiles have the same language and directory slug.
    """
    seen: dict[tuple[str, str], str] = {}
    for profile in sorted(profiles, key=lambda item: item.name):
        slug = profile_slug(profile)
        key = (profile.language, slug)
        previous = seen.get(key)
        if previous is not None:
            raise ProfileError(
                f"профили {previous!r} и {profile.name!r} дают одинаковый каталог "
                f"{profile.language}/{slug}: переименуйте один из них"
            )
        seen[key] = profile.name


def _validate_recipe(name: str, layers: tuple[Layer, ...]) -> None:
    """Reject an empty recipe or a repeated layer name.

    Raises:
        ProfileError: The recipe has no layers or repeats a layer name.
    """
    if not layers:
        raise ProfileError(f"у профиля {name} пустой рецепт production")
    seen: set[str] = set()
    for layer in layers:
        if layer.name in seen:
            raise ProfileError(f"повтор имени слоя {layer.name!r} в профиле {name}")
        seen.add(layer.name)


__all__ = ["group_profiles"]
