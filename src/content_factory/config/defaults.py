"""Built-in defaults and the seeded configuration template."""

from __future__ import annotations

from typing import Any

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
    "generator": {"target": 20, "refill_at": 2},
    "delivery": {"max_count": 100},
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
  # Мягкий лимит одного запуска: проверяется между шагами производства.
  max_duration: "45m"
  # Пауза между языками внутри одного запуска.
  pause_between_languages: "5m"

search:
  # Сколько поисковых запросов использовать за запуск, суммарно по всем языкам.
  max_queries: 30
  # Пауза между поисковыми запросами.
  pause: "45s"

filter:
  # Сколько найденных видео проверять фильтрами за запуск, суммарно по языкам.
  max: 60
  # Пауза после проверки в каждом потоке.
  pause: "20s"

download:
  # Сколько видео скачивать параллельно.
  workers: 2
  # Пауза после скачивания в каждом потоке.
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

generator:
  # Цель по стоку на один профиль генератора, видео.
  target: 20
  # Остаток, при котором запускается добор профиля.
  refill_at: 2

delivery:
  # Максимум видео в одном архиве ручной выдачи.
  max_count: 100

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
