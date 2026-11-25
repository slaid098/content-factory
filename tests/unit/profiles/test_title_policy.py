"""Title policy of a delivery profile: fixed, generated and the factory."""

from __future__ import annotations

import pytest

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.profiles.delivery import TitleConfig, TitleMode
from content_factory.services.profiles.fixed_title import FixedTitle
from content_factory.services.profiles.generated_title import GeneratedTitle
from content_factory.services.profiles.titles import title_policy

_SHA256 = "a" * 64


def test_fixed_policy_needs_no_generation_and_returns_profile_title() -> None:
    policy = title_policy(TitleConfig(mode=TitleMode.FIXED, value="Давай знакомиться ✨"))

    assert isinstance(policy, FixedTitle)
    assert policy.needs_generation is False
    assert policy.title_for(_SHA256, {}) == "Давай знакомиться ✨"
    assert policy.title_for(_SHA256, {_SHA256: "Старое название"}) == "Давай знакомиться ✨"


def test_generated_policy_returns_stored_title_unchanged() -> None:
    policy = title_policy(TitleConfig())

    assert isinstance(policy, GeneratedTitle)
    assert policy.needs_generation is True
    title = "  Тихий   вечер #рек  " + "я" * 100
    assert policy.title_for(_SHA256, {_SHA256: title}) == title


@pytest.mark.parametrize("stored", [{}, {_SHA256: ""}, {_SHA256: " \t\n "}])
def test_generated_policy_returns_none_without_title(stored: dict[str, str]) -> None:
    policy = title_policy(TitleConfig())

    assert policy.title_for(_SHA256, stored) is None


def test_fixed_policy_without_value_is_rejected() -> None:
    with pytest.raises(ProfileError, match="fixed-название"):
        title_policy(TitleConfig(mode=TitleMode.FIXED))


def test_fixed_policy_with_blank_value_is_rejected() -> None:
    with pytest.raises(ProfileError, match="fixed-название"):
        title_policy(TitleConfig(mode=TitleMode.FIXED, value="   "))


def test_generated_policy_with_explicit_mode() -> None:
    policy = title_policy(TitleConfig(mode=TitleMode.GENERATED, value=None))

    assert policy.needs_generation is True
    assert policy.title_for(_SHA256, {_SHA256: "Название"}) == "Название"
