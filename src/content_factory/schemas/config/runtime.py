"""Typed runtime configuration stored in ``app_data/config/config.yaml``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Production limits for one scheduled tick.

    Durations are stored in seconds.
    """

    batch: int
    interval: float
    max_duration: float
    pause_between_languages: float


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Search stage limits and pacing."""

    max_queries: int
    pause: float


@dataclass(frozen=True, slots=True)
class FilterConfig:
    """Filter stage limits and pacing."""

    max: int
    pause: float


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    """Download stage limits and pacing."""

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
class RuntimeConfig:
    """Complete runtime configuration."""

    run: RunConfig
    search: SearchConfig
    filter: FilterConfig
    download: DownloadConfig
    captcha: CaptchaConfig
    alerts: AlertsConfig
    languages: dict[str, LanguageConfig]
