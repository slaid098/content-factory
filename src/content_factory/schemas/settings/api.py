"""Editable runtime settings request and response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from content_factory.constants import LANGUAGE_CODE_RE
from content_factory.exceptions.errors import ConfigError
from content_factory.utils.duration import parse_duration


def _duration_text(value: str) -> str:
    """Return a non-empty duration string such as ``6h`` or ``45m``."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("длительность не может быть пустой")
    try:
        seconds = parse_duration(normalized)
    except ConfigError as ex:
        raise ValueError(str(ex)) from ex
    if seconds <= 0:
        raise ValueError("длительность должна быть больше нуля")
    return normalized


class LanguageSettings(BaseModel):
    """Editable fields of one configured language."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: str = Field(pattern=LANGUAGE_CODE_RE.pattern)
    enabled: bool
    stock_target: int = Field(ge=0, le=100_000)


class RunSettings(BaseModel):
    """Editable run stage limits.

    Durations stay as source strings so the file keeps ``6h`` instead of a
    normalized seconds value. ``pause_between_languages`` is intentionally
    absent: the issue keeps it out of the API, but validation still checks it
    against ``max_duration``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    batch: int = Field(ge=1, le=1000)
    interval: str
    max_duration: str

    @field_validator("interval", "max_duration")
    @classmethod
    def _valid_duration(cls, value: str) -> str:
        return _duration_text(value)


class SearchSettings(BaseModel):
    """Editable search stage limits."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_queries: int = Field(ge=1, le=500)


class FilterSettings(BaseModel):
    """Editable filter stage limits."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max: int = Field(ge=1, le=10_000)


class DownloadSettings(BaseModel):
    """Editable download stage limits."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    workers: int = Field(ge=1, le=8)


class DeliverySettings(BaseModel):
    """Editable manual delivery limits."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_count: int = Field(ge=1, le=300)


class RuntimeSettings(BaseModel):
    """Safe editable slice of the runtime configuration.

    Only fields exposed by the API live here. Captcha, alerts, stage pauses,
    ``run.pause_between_languages`` and supplier environment settings stay out
    of the request and response.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    languages: tuple[LanguageSettings, ...]
    run: RunSettings
    search: SearchSettings
    filter: FilterSettings
    download: DownloadSettings
    delivery: DeliverySettings


class SettingsPreviewResponse(BaseModel):
    """Result of a preview: diff and the settings it would write."""

    model_config = ConfigDict(frozen=True)

    changed: bool
    diff: str
    settings: RuntimeSettings


class SettingsApplyResponse(BaseModel):
    """Result of an apply: diff, backup name and stored settings."""

    model_config = ConfigDict(frozen=True)

    changed: bool
    diff: str
    backup: str | None
    settings: RuntimeSettings


class GeneratorSettingsResponse(BaseModel):
    """Safe read-only slice of the generator settings.

    Secrets (``vast_api_token`` and the OpenAI ``api_key``) and the output
    directory stay out of the response.
    """

    model_config = ConfigDict(frozen=True)

    rent_pool_size: int
    videos_per_server: int
    max_servers: int
    offer_refresh_seconds: int
    server_ready_timeout: int
    comfy_health_timeout: int
    comfy_job_timeout: int
    comfy_health_interval: int
    max_cost_per_hour: float | None
    stale_instance_hours: int
    safety_enabled: bool
    safety_frames: int
    safety_threshold: float
    safety_nude_threshold: float
    safety_frame_attempts: int
    model: str
