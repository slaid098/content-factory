"""Stock endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.schemas.stock.api import LanguageStockResponse, StockResponse
from content_factory.services.stock.service import collect_stock

stock_router = APIRouter()


@stock_router.get("/stock", response_model=StockResponse)
def get_stock() -> StockResponse:
    """Return stock per enabled language and the production loop state."""
    settings = Settings()
    runtime = load_runtime_config(
        settings.data_dir / CONFIG_DIR / CONFIG_FILENAME,
        seed=False,
    )
    snapshot, state = collect_stock(
        settings.data_dir,
        runtime.languages,
        now=datetime.now(UTC),
    )
    return StockResponse(
        generated_at=snapshot.generated_at,
        last_production_at=state.last_production_at,
        captcha_blocked_until=state.captcha_blocked_until,
        captcha_streak=state.captcha_streak,
        zero_runs=state.zero_runs,
        languages=tuple(
            LanguageStockResponse(
                language=row.language,
                stock=row.stock,
                target=row.target,
                deficit=row.deficit,
                titled=row.titled,
                untitled=row.untitled,
                video_bytes=row.video_bytes,
            )
            for row in snapshot.languages
        ),
    )
