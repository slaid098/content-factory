"""Delivery address schema: validation and one-time language resolution."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from content_factory.schemas.content.delivery import (
    ContentType,
    DeliveryRequest,
    ResolvedDeliveryRequest,
)

_REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


def _request(**overrides: object) -> DeliveryRequest:
    values: dict[str, object] = {"count": 1, "request_id": _REQUEST_ID}
    values.update(overrides)
    return DeliveryRequest.model_validate(values)


def test_general_request_requires_language() -> None:
    with pytest.raises(ValidationError):
        _request()


def test_profile_request_allows_missing_language() -> None:
    assert _request(profile="dating/profile_1").language is None


def test_general_alias_requires_language() -> None:
    with pytest.raises(ValidationError):
        _request(profile="general")


def test_resolve_prefers_request_language_over_profile_language() -> None:
    resolved = _request(language="ru", profile="dating/profile_1").resolve("en")

    assert resolved == ResolvedDeliveryRequest(
        language="ru",
        profile="dating/profile_1",
        count=1,
        request_id=_REQUEST_ID,
    )


def test_resolve_falls_back_to_profile_language() -> None:
    resolved = _request(profile="dating/profile_1").resolve("en")

    assert resolved.language == "en"
    assert resolved.profile == "dating/profile_1"
    assert resolved.type is ContentType.VIDEO


def test_resolve_without_any_language_raises() -> None:
    request = DeliveryRequest.model_construct(
        language=None,
        profile=None,
        type=ContentType.VIDEO,
        count=1,
        request_id=_REQUEST_ID,
        query=None,
    )

    with pytest.raises(ValueError, match="language"):
        request.resolve(None)
