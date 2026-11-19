"""Stock accounting."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect_readonly
from content_factory.db.runtime_state import read_state
from content_factory.schemas.config.runtime import LanguageConfig
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.stock.stock import LanguageStock, StockSnapshot


def count_stock(content_dir: Path, languages: Iterable[str]) -> dict[str, int]:
    """Посчитать готовые mp4-файлы по каталогам языков."""
    return {language: _count_language(content_dir / language) for language in languages}


def collect_stock(
    data_dir: Path,
    languages: Mapping[str, LanguageConfig],
    *,
    now: datetime,
) -> tuple[StockSnapshot, RuntimeState]:
    """Собрать остатки включённых языков и сохранённое состояние без записи.

    Args:
        data_dir: Корень ``app_data``.
        languages: Языки из конфигурации.
        now: Момент снятия остатков.

    Returns:
        Снимок остатков и последнее сохранённое состояние цикла.
    """
    counts = count_stock(data_dir / CONTENT_DIR, languages)
    rows = tuple(
        LanguageStock(
            language=code,
            stock=counts[code],
            target=config.stock_target,
            deficit=max(0, config.stock_target - counts[code]),
        )
        for code, config in sorted(languages.items())
        if config.enabled
    )
    snapshot = StockSnapshot(generated_at=now, languages=rows)
    return snapshot, _read_runtime_state(data_dir)


def _count_language(directory: Path) -> int:
    """Count regular ``*.mp4`` files in one language directory."""
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.iterdir() if path.is_file() and path.suffix == ".mp4")


def _read_runtime_state(data_dir: Path) -> RuntimeState:
    """Read the loop state without creating a database file."""
    db_path = data_dir / DB_DIR / DB_FILENAME
    if not db_path.is_file():
        return RuntimeState.empty()
    connection = connect_readonly(db_path)
    try:
        return read_state(connection)
    finally:
        connection.close()
