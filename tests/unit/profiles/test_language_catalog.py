"""Static language catalog: volume, labels, order and fallback."""

from __future__ import annotations

from content_factory.constants import LANGUAGE_CODE_RE, LANGUAGE_LABELS, LANGUAGES, language_label

_POPULAR_CODES = {
    "ar",
    "bn",
    "cs",
    "de",
    "el",
    "en",
    "es",
    "fa",
    "fr",
    "gu",
    "he",
    "hi",
    "id",
    "it",
    "ja",
    "kk",
    "ko",
    "mr",
    "nl",
    "pl",
    "pt",
    "ro",
    "ru",
    "sv",
    "sw",
    "ta",
    "te",
    "th",
    "tr",
    "uk",
    "ur",
    "vi",
    "zh",
}


def test_catalog_has_only_popular_languages() -> None:
    assert 25 <= len(LANGUAGE_LABELS) <= 45
    assert set(LANGUAGE_LABELS) == _POPULAR_CODES


def test_catalog_codes_match_the_language_format() -> None:
    assert all(LANGUAGE_CODE_RE.fullmatch(code) for code in LANGUAGE_LABELS)


def test_catalog_labels_start_with_a_capital_letter() -> None:
    assert all(label[:1] == label[:1].upper() for label in LANGUAGE_LABELS.values())


def test_catalog_has_no_rare_codes() -> None:
    assert not {"aa", "ab", "ace", "zxx"} & set(LANGUAGE_LABELS)


def test_known_labels_are_russian() -> None:
    assert language_label("ru") == "Русский"
    assert language_label("de") == "Немецкий"
    assert language_label("kk") == "Казахский"


def test_unknown_code_falls_back_to_the_uppercase_code() -> None:
    assert language_label("kaz") == "KAZ"


def test_languages_are_unique_and_sorted_by_label() -> None:
    assert len(set(LANGUAGES)) == len(LANGUAGES)
    assert set(LANGUAGES) == set(LANGUAGE_LABELS)
    assert list(LANGUAGES) == sorted(LANGUAGES, key=lambda code: LANGUAGE_LABELS[code])
