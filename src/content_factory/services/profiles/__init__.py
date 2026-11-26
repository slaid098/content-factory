"""Generator profile registry, API views, descriptions and refill decisions."""

from content_factory.services.profiles.describe import describe_profile
from content_factory.services.profiles.service import (
    decide_refill,
    get_profile,
    get_profile_view,
    is_safe_profile_id,
    list_profiles,
    preview_for_profile,
    update_profile_settings,
)
from content_factory.services.profiles.titles import title_policy
from content_factory.services.profiles.views import (
    list_profile_views,
    list_profiles_response,
)

__all__ = [
    "decide_refill",
    "describe_profile",
    "get_profile",
    "get_profile_view",
    "is_safe_profile_id",
    "list_profile_views",
    "list_profiles",
    "list_profiles_response",
    "preview_for_profile",
    "title_policy",
    "update_profile_settings",
]
