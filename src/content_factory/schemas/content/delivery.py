"""Request body and job state of the content delivery endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from content_factory.constants import LANGUAGE_CODE_RE

DeliveryStatus = Literal["building", "ready", "ready_local", "empty", "failed"]

GENERAL_PROFILE = "general"


class ContentType(StrEnum):
    """Media type of one stock file and of one delivery archive."""

    VIDEO = "video"
    PHOTO = "photo"


class DeliveryRequest(BaseModel):
    """One delivery address: a language or a profile, plus the media type.

    A ``profile`` such as ``dating/profile_1`` already carries the language in
    the registry, so ``language`` is optional there; the explicit profile
    ``general`` means no profile and keeps ``language`` required. ``type`` is
    the media type of the stock and defaults to video; photos exist only in a
    profile, because General is video by language. A ``query`` topic belongs to
    supplier videos and is allowed only for videos without a profile.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str | None = Field(default=None, pattern=LANGUAGE_CODE_RE.pattern)
    profile: str | None = Field(default=None, min_length=1, max_length=200)
    type: ContentType = ContentType.VIDEO
    count: int = Field(ge=1, le=300)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("profile", mode="before")
    @classmethod
    def _general_is_no_profile(cls, value: object) -> object:
        """Treat the explicit ``general`` profile as the General mode."""
        return None if value == GENERAL_PROFILE else value

    @model_validator(mode="after")
    def _check_address(self) -> DeliveryRequest:
        """Reject a request the two delivery modes cannot serve."""
        if self.profile is None and self.language is None:
            raise ValueError("language обязателен без profile")
        if self.type is ContentType.PHOTO and self.profile is None:
            raise ValueError("type=photo требует profile: General — только видео")
        if self.profile is not None and self.query is not None:
            raise ValueError("query нельзя сочетать с profile")
        if self.type is ContentType.PHOTO and self.query is not None:
            raise ValueError("query нельзя сочетать с type=photo")
        return self

    def resolve(self, profile_language: str | None) -> ResolvedDeliveryRequest:
        """Fill the address language from the request or from its profile.

        The client language wins over the registry language of the profile, so
        a profile can never be delivered under another language. General has no
        profile and therefore no fallback.

        Args:
            profile_language: Registry language of ``profile``, or ``None``
                when the request is General.

        Returns:
            The request with a mandatory language, ready for the service layer.

        Raises:
            ValueError: Neither the request nor the profile provides a language.
        """
        language = self.language if self.language is not None else profile_language
        if language is None:
            raise ValueError("language обязателен без profile")
        return ResolvedDeliveryRequest(
            language=language,
            profile=self.profile,
            type=self.type,
            count=self.count,
            request_id=self.request_id,
            query=self.query,
        )


class ResolvedDeliveryRequest(BaseModel):
    """A delivery address whose language is already resolved.

    Built only by :meth:`DeliveryRequest.resolve`; the delivery service never
    deals with an optional language.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(pattern=LANGUAGE_CODE_RE.pattern)
    profile: str | None = Field(default=None, min_length=1, max_length=200)
    type: ContentType = ContentType.VIDEO
    count: int = Field(ge=1, le=300)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)


class DeliveryJobInfo(BaseModel):
    """State of one asynchronous temporary-link delivery job."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID
    language: str
    status: DeliveryStatus
    created_at: datetime
    expires_at: datetime
    size: int = 0
    files: int = 0
    url: str | None = None
    error: str | None = None
