"""Оценка объёма поиска под остаток цели."""

from __future__ import annotations

import math

_CANDIDATE_HEADROOM = 12


def plan_candidates(need: int, cap: int) -> int:
    """Посчитать, сколько новых кандидатов искать под остаток цели.

    Args:
        need: Сколько готовых видео ещё нужно скачать.
        cap: Максимум кандидатов, который разрешено проверить фильтрами.

    Returns:
        Размер одного поискового батча; 0, когда искать нечего.
    """
    if need <= 0 or cap <= 0:
        return 0
    return max(1, min(cap, math.ceil(need * _CANDIDATE_HEADROOM)))
