"""Resolved supplier settings passed to the engine stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SupplierConfig:
    """Environment settings merged with runtime limits and filesystem paths."""

    profile_id: str
    bitbrowser_url: str
    max_views: int
    max_followers: int
    min_duration: int
    max_duration: int
    min_age_days: int
    max_queries: int
    scrolls_min: int
    scrolls_max: int
    queries_per_session_min: int
    queries_per_session_max: int
    search_pause: float
    pause_scroll_min: float
    pause_scroll_max: float
    page_settle_min: float
    page_settle_max: float
    session_break_min: int
    session_break_max: int
    captcha_cooldown_min: int
    captcha_cooldown_max: int
    probe_workers: int
    filter_pause: float
    download_workers: int
    download_pause: float
    db_path: Path
    content_dir: Path
    proxy: str
    language: str
