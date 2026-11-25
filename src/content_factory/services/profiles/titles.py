"""Title policy: how one profile names its delivered videos."""

from __future__ import annotations

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.profiles.delivery import TitleConfig, TitleMode, TitlePolicy
from content_factory.services.profiles.fixed_title import FixedTitle
from content_factory.services.profiles.generated_title import GeneratedTitle


def title_policy(config: TitleConfig) -> TitlePolicy:
    """Build the title policy of one profile.

    A future mode is added as a policy class and one branch here; delivery and
    the titles flow keep using the interface.

    Args:
        config: Validated ``delivery.title`` of the profile.

    Returns:
        The policy of the configured mode.

    Raises:
        ProfileError: A ``fixed`` config has no title.
    """
    if config.mode is TitleMode.FIXED:
        if config.value is None or not config.value.strip():
            raise ProfileError("в профиле fixed-название не задано")
        return FixedTitle(config.value)
    return GeneratedTitle()


__all__ = ["FixedTitle", "GeneratedTitle", "TitlePolicy", "title_policy"]
