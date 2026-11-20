"""Typed runtime configuration stored in ``app_data/config/config.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Production limits for one scheduled tick.

    Durations are stored in seconds. ``max_duration`` is a soft cap checked
    between pipeline steps, not inside a single long stage.
    """

    batch: int
    interval: float
    max_duration: float
    pause_between_languages: float


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Search stage limits and pacing.

    ``max_queries`` is a run-wide budget shared by all languages.
    """

    max_queries: int
    pause: float


@dataclass(frozen=True, slots=True)
class FilterConfig:
    """Filter stage limits and pacing.

    ``max`` is a run-wide budget shared by all languages; ``pause`` is applied
    inside every probe worker.
    """

    max: int
    pause: float


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    """Download stage limits and pacing.

    ``pause`` is applied inside every worker after its download.
    """

    workers: int
    pause: float


@dataclass(frozen=True, slots=True)
class CaptchaConfig:
    """Captcha backoff policy."""

    streak: int
    pause: float
    long_pause: float


@dataclass(frozen=True, slots=True)
class AlertsConfig:
    """Alert throttling policy."""

    cooldown: float


@dataclass(frozen=True, slots=True)
class LanguageConfig:
    """Per-language production switch and stock target."""

    enabled: bool
    stock_target: int


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """Limits of the manual temporary-link delivery."""

    max_count: int


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Complete runtime configuration."""

    run: RunConfig
    search: SearchConfig
    filter: FilterConfig
    download: DownloadConfig
    captcha: CaptchaConfig
    alerts: AlertsConfig
    languages: dict[str, LanguageConfig]
    # Kept last with a default so configurations built before the manual
    # delivery key existed stay valid.
    delivery: DeliveryConfig = field(default_factory=lambda: DeliveryConfig(max_count=100))
