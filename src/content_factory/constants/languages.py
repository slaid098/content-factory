"""Language codes, their Russian labels and shared code formats."""

from __future__ import annotations

import re
from collections.abc import Mapping

# Two or three lowercase Latin letters: ``ru``, ``en``, ``eng``. Uppercase,
# region tags (``ru-RU``) and one-letter codes are rejected.
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}$")

# One lowercase niche name: ``general``, ``dating``, ``auto-parts``. The same
# format is a directory name under ``content/`` and a profile value.
NICHE_CODE_RE = re.compile(r"^[a-z0-9_-]+$")

# Russian labels of the popular languages offered by the profile filter.
# The catalog is deliberately short: operators need common languages, not
# the full ISO list. Babel is not a project dependency: the catalog is static
# data. Profile codes outside it still reach the API through
# ``_language_options`` and get ``code.upper()`` as their label.
LANGUAGE_LABELS: Mapping[str, str] = {
    "ar": "Арабский",
    "bn": "Бенгальский",
    "cs": "Чешский",
    "de": "Немецкий",
    "el": "Греческий",
    "en": "Английский",
    "es": "Испанский",
    "fa": "Персидский",
    "fr": "Французский",
    "gu": "Гуджарати",
    "he": "Иврит",
    "hi": "Хинди",
    "id": "Индонезийский",
    "it": "Итальянский",
    "ja": "Японский",
    "kk": "Казахский",
    "ko": "Корейский",
    "mr": "Маратхи",
    "nl": "Нидерландский",
    "pl": "Польский",
    "pt": "Португальский",
    "ro": "Румынский",
    "ru": "Русский",
    "sv": "Шведский",
    "sw": "Суахили",
    "ta": "Тамильский",
    "te": "Телугу",
    "th": "Тайский",
    "tr": "Турецкий",
    "uk": "Украинский",
    "ur": "Урду",
    "vi": "Вьетнамский",
    "zh": "Китайский",
}

# Codes sorted by their Russian label: the order of a language select.
LANGUAGES: tuple[str, ...] = tuple(sorted(LANGUAGE_LABELS, key=lambda code: LANGUAGE_LABELS[code]))


def language_label(code: str) -> str:
    """Return the Russian label of a language code, or the uppercase code.

    The catalog holds only popular languages; a profile may use any code.

    Args:
        code: Language code such as ``ru``, or a profile value outside the
            catalog.

    Returns:
        A catalog label, or ``code.upper()`` when the code is outside it.
    """
    return LANGUAGE_LABELS.get(code) or code.upper()
