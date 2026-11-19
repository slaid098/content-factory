"""Built-in defaults and the seeded configuration template."""

from __future__ import annotations

from typing import Any

from content_factory.schemas.config.runtime import (
    AlertsConfig,
    CaptchaConfig,
    DownloadConfig,
    FilterConfig,
    LanguageConfig,
    RunConfig,
    RuntimeConfig,
    SearchConfig,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "run": {
        "batch": 20,
        "interval": "6h",
        "max_duration": "45m",
        "pause_between_languages": "5m",
    },
    "search": {"max_queries": 30, "pause": "45s"},
    "filter": {"max": 60, "pause": "20s"},
    "download": {"workers": 2, "pause": "10s"},
    "captcha": {"streak": 3, "pause": "2h", "long_pause": "6h"},
    "alerts": {"cooldown": "6h"},
    "languages": {
        "ru": {"enabled": True, "stock_target": 150},
        "en": {"enabled": False, "stock_target": 100},
    },
}

CONFIG_TEMPLATE = """\
# Параметры производства. Файл создаётся при первом старте, дальше его
# содержимое главнее кода: правки на диске не перезаписываются.
run:
  # Максимальный объём производства за один тик, видео.
  batch: 20
  # Как часто запускать производство.
  interval: "6h"
  # Максимальная длительность одного запуска поставщика.
  max_duration: "45m"
  # Пауза между языками внутри одного запуска.
  pause_between_languages: "5m"

search:
  # Сколько поисковых запросов использовать за запуск на язык.
  max_queries: 30
  # Пауза между поисковыми запросами.
  pause: "45s"

filter:
  # Сколько найденных видео проверять фильтрами за запуск.
  max: 60
  # Пауза между проверками.
  pause: "20s"

download:
  # Сколько видео скачивать параллельно.
  workers: 2
  # Пауза между стартами скачиваний.
  pause: "10s"

captcha:
  # Сколько капч подряд считаются серией.
  streak: 3
  # Пауза после одной капчи.
  pause: "2h"
  # Пауза после серии капч.
  long_pause: "6h"

alerts:
  # Минимальный интервал между одинаковыми алертами.
  cooldown: "6h"

languages:
  ru:
    # Производить для этого языка.
    enabled: true
    # Целевой остаток готовых видео.
    stock_target: 150
  en:
    # Производить для этого языка.
    enabled: false
    # Целевой остаток готовых видео.
    stock_target: 100
"""


def default_config() -> RuntimeConfig:
    """Build the built-in runtime configuration."""
    return RuntimeConfig(
        run=RunConfig(
            batch=20,
            interval=6 * 3600.0,
            max_duration=45 * 60.0,
            pause_between_languages=5 * 60.0,
        ),
        search=SearchConfig(max_queries=30, pause=45.0),
        filter=FilterConfig(max=60, pause=20.0),
        download=DownloadConfig(workers=2, pause=10.0),
        captcha=CaptchaConfig(streak=3, pause=2 * 3600.0, long_pause=6 * 3600.0),
        alerts=AlertsConfig(cooldown=6 * 3600.0),
        languages={
            "ru": LanguageConfig(enabled=True, stock_target=150),
            "en": LanguageConfig(enabled=False, stock_target=100),
        },
    )
