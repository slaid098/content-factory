"""Единственный вход капсулы поставщика: производство по плану языков."""

from __future__ import annotations

import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from content_factory.config.settings import Settings, SupplierSettings, build_supplier_config
from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME, QUERIES_DIR
from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.config.runtime import RuntimeConfig
from content_factory.schemas.supplier.progress import Budget
from content_factory.schemas.supplier.results import (
    LanguageResult,
    ProduceResult,
    StopReason,
)
from content_factory.services.supplier.mcp import McpClient
from content_factory.services.supplier.pipeline import run_language
from content_factory.services.supplier.queries import load_language_queries
from content_factory.services.supplier.repository import Repository

_SEED_QUERIES_DIR = Path(__file__).parent / "queries"
_STOP_PLAN = frozenset({StopReason.CAPTCHA, StopReason.DURATION_EXCEEDED})


def produce(
    plan: Mapping[str, int],
    *,
    runtime: RuntimeConfig,
    settings: Settings,
    supplier_settings: SupplierSettings,
) -> ProduceResult:
    """Произвести видео по плану языков за один проход.

    BitBrowser открывается один раз на весь план, языки идут последовательно.
    Цель языка — уже скачанное этим языком плюс его доля плана, поэтому
    удаление файлов партнёром не мешает добору.

    Args:
        plan: Сколько новых видео скачать для каждого языка.
        runtime: Лимиты запуска из ``app_data/config/config.yaml``.
        settings: Общие настройки сервиса с корнем данных.
        supplier_settings: Настройки поставщика из окружения.

    Returns:
        Счётчики по языкам, общий итог и причина остановки.

    Raises:
        SupplierError: BitBrowser или профиль недоступны, либо каталог данных
            нельзя подготовить.
    """
    batches = {language: amount for language, amount in plan.items() if amount > 0}
    if not batches:
        return ProduceResult({}, 0, StopReason.ENOUGH_STOCK)

    content_dir, queries_dir, db_path = _prepare_paths(settings.data_dir, batches)
    profile_id = supplier_settings.profile_id.strip()
    if not profile_id:
        raise SupplierError("SUPPLIER_PROFILE_ID не задан: укажите ID профиля BitBrowser")

    repo = Repository(db_path)
    repo.init_schema()
    budget = Budget(
        queries=runtime.search.max_queries,
        filters=runtime.filter.max,
    )
    deadline = time.monotonic() + runtime.run.max_duration
    mcp = McpClient(
        supplier_settings.mcp_url.strip().rstrip("/"),
        timeout=supplier_settings.mcp_timeout,
    )
    opened = False

    try:
        mcp.open_profile(profile_id, headless=supplier_settings.headless)
        opened = True
        produced, results = _run_languages(
            batches,
            repo=repo,
            mcp=mcp,
            budget=budget,
            deadline=deadline,
            content_dir=content_dir,
            queries_dir=queries_dir,
            db_path=db_path,
            runtime=runtime,
            supplier_settings=supplier_settings,
        )
    finally:
        if opened and supplier_settings.close_profile_after_crawl:
            try:
                mcp.close_profile(profile_id)
            except SupplierError as ex:
                logger.bind(error=ex, error_type=type(ex).__name__).warning(
                    "Не удалось закрыть профиль BitBrowser"
                )

    return _result(produced, results, sum(batches.values()))


def _prepare_paths(data_dir: Path, languages: Mapping[str, int]) -> tuple[Path, Path, Path]:
    """Создать каталоги и сиды запросов, вернуть рабочие пути."""
    content_dir = data_dir / CONTENT_DIR
    queries_dir = data_dir / QUERIES_DIR
    db_path = data_dir / DB_DIR / DB_FILENAME
    for language in languages:
        _seed_queries(queries_dir, language)
        _ensure_directory(content_dir / language)
    return content_dir, queries_dir, db_path


def _run_languages(
    batches: Mapping[str, int],
    *,
    repo: Repository,
    mcp: McpClient,
    budget: Budget,
    deadline: float,
    content_dir: Path,
    queries_dir: Path,
    db_path: Path,
    runtime: RuntimeConfig,
    supplier_settings: SupplierSettings,
) -> tuple[dict[str, int], list[LanguageResult]]:
    """Пройти языки по порядку, пока не кончится план, время или капча."""
    produced: dict[str, int] = dict.fromkeys(batches, 0)
    results: list[LanguageResult] = []

    for position, (language, amount) in enumerate(batches.items()):
        if time.monotonic() >= deadline:
            results.append(LanguageResult(0, StopReason.DURATION_EXCEEDED))
            break

        cfg = build_supplier_config(
            supplier_settings,
            runtime,
            db_path=db_path,
            queries_dir=queries_dir,
            content_dir=content_dir / language,
            language=language,
        )
        queries = load_language_queries(queries_dir, language)
        if not queries:
            logger.bind(language=language).warning("Для языка {} нет поисковых запросов", language)
            results.append(LanguageResult(0, StopReason.NO_QUERIES))
            continue

        target = repo.downloaded_count(language) + amount
        result = run_language(
            cfg,
            repo=repo,
            mcp=mcp,
            queries=queries,
            target=target,
            budget=budget,
            deadline=deadline,
        )
        produced[language] = result.downloaded
        results.append(result)
        logger.bind(language=language, stop_reason=result.stop_reason.value).info(
            "Язык {}: скачано {} из {}, причина {}",
            language,
            result.downloaded,
            amount,
            result.stop_reason.value,
        )

        if result.stop_reason in _STOP_PLAN:
            break
        if position < len(batches) - 1:
            _pause_between(runtime.run.pause_between_languages, deadline)

    return produced, results


def _seed_queries(queries_dir: Path, language: str) -> None:
    """Скопировать сид запросов, не перезаписывая существующий файл."""
    destination = queries_dir / f"{language}.txt"
    source = _SEED_QUERIES_DIR / f"{language}.txt"
    if destination.exists() or not source.is_file():
        return
    _ensure_directory(queries_dir)
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
    except FileExistsError:
        return
    except OSError as ex:
        raise SupplierError(f"не удалось создать файл запросов {destination}: {ex}") from ex


def _ensure_directory(directory: Path) -> None:
    """Create a data directory or raise a supplier error."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise SupplierError(f"не удалось создать каталог {directory}: {ex}") from ex


def _pause_between(seconds: float, deadline: float) -> None:
    """Wait between languages without crossing the run deadline."""
    if seconds <= 0:
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    time.sleep(min(seconds, remaining))


def _result(
    produced: Mapping[str, int],
    results: list[LanguageResult],
    total_plan: int,
) -> ProduceResult:
    """Aggregate language outcomes into one production result."""
    total = sum(produced.values())
    reasons = [result.stop_reason for result in results]
    blocked_until: str | None = None

    if StopReason.CAPTCHA in reasons:
        blocked_until = next(
            (
                result.blocked_until
                for result in results
                if result.stop_reason is StopReason.CAPTCHA
            ),
            None,
        )
        reason = StopReason.CAPTCHA
    elif StopReason.DURATION_EXCEEDED in reasons:
        reason = StopReason.DURATION_EXCEEDED
    elif total >= total_plan:
        reason = StopReason.TARGET_REACHED
    elif results and all(item is StopReason.NO_QUERIES for item in reasons):
        reason = StopReason.NO_QUERIES
    elif results and all(item is StopReason.NO_PROGRESS for item in reasons):
        reason = StopReason.NO_PROGRESS
    elif any(item in {StopReason.QUERIES_EXHAUSTED, StopReason.NO_QUERIES} for item in reasons):
        reason = StopReason.QUERIES_EXHAUSTED
    else:
        reason = StopReason.STOCK_SHORTFALL

    return ProduceResult(
        produced_by_language=dict(produced),
        produced_total=total,
        stop_reason=reason,
        blocked_until=blocked_until,
    )
