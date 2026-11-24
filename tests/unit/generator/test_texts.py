"""Text step: markup parsing, retries and the saved card JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import TextError
from content_factory.services.generator.texts import (
    _Overlay,
    _TextBlock,
    generate_overlay,
    parse_overlays,
    save_overlay,
)


class _Writer:
    def __init__(self, answers: list[str | Exception]) -> None:
        self.answers = answers
        self.calls = 0

    def write(self, system_prompt: str) -> str:
        del system_prompt
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_two_results_with_two_blocks_are_parsed() -> None:
    raw = (
        "<result>\n<block>Первая строка\nВторая строка</block>\n"
        "<block>напиши мне</block>\n</result>\n"
        "<result><block>Другой текст</block><block>женись на мне</block></result>"
    )

    overlays = parse_overlays(raw)

    assert len(overlays) == 2
    assert overlays[0].blocks[0].lines == ("Первая строка", "Вторая строка")
    assert overlays[0].blocks[1].lines == ("напиши мне",)
    assert overlays[1].blocks[0].lines == ("Другой текст",)


def test_single_block_and_plain_answer_are_accepted() -> None:
    single = parse_overlays("<result><block>Только один блок</block></result>")
    plain = parse_overlays("Просто текст без тегов")

    assert single[0].blocks == (_TextBlock(lines=("Только один блок",)),)
    assert plain[0].blocks == (_TextBlock(lines=("Просто текст без тегов",)),)


def test_empty_and_short_answers_are_rejected() -> None:
    assert parse_overlays("") == ()
    assert parse_overlays("<result><block>абв</block></result>") == ()


def test_retry_returns_the_first_parsed_answer(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    writer = _Writer([TextError("нет сети"), "<result><block>Текст карточки</block></result>"])
    pauses: list[float] = []

    overlay = generate_overlay(profile, writer, attempts=2, pause=0.5, sleep=pauses.append)

    assert writer.calls == 2
    assert pauses == [0.5]
    assert overlay.blocks[0].lines == ("Текст карточки",)


def test_all_attempts_failing_raise_text_error(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    writer = _Writer([TextError("нет сети"), "аб", TextError("снова нет сети")])

    with pytest.raises(TextError, match="не получен"):
        generate_overlay(profile, writer, attempts=3, pause=0, sleep=lambda _: None)


def test_overlay_is_saved_as_json(tmp_path: Path) -> None:
    path = tmp_path / "text" / "video.json"
    overlay = _Overlay(blocks=(_TextBlock(lines=("Строка",)),))

    save_overlay(path, overlay)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"blocks": [{"lines": ["Строка"]}]}


@pytest.mark.parametrize(
    "raw",
    [
        "<result><block>Привет OK</block></result>",
        "<result><block>Привет Instagram</block></result>",
        "<result><block>Привет 世界</block></result>",
        "<result><block>Привет \ufffd</block></result>",
        "<result><block>Привет κόσμε</block></result>",
        "Привет OK",
        "世界世界",
    ],
)
def test_answers_with_foreign_letters_are_rejected(raw: str) -> None:
    assert parse_overlays(raw) == ()


def test_punctuation_numbers_and_emoji_are_allowed() -> None:
    raw = "<result><block>Мне 25 — «привет»… 😊</block></result>"

    overlays = parse_overlays(raw)

    assert overlays[0].blocks[0].lines == ("Мне 25 — «привет»… 😊",)


def test_foreign_result_rejects_the_whole_answer() -> None:
    raw = "<result><block>Привет OK</block></result><result><block>Привет мир</block></result>"

    assert parse_overlays(raw) == ()


def test_foreign_answer_is_retried_like_an_empty_one(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    writer = _Writer(
        [
            "<result><block>Привет OK</block></result>",
            "<result><block>Привет 世界</block></result>",
            "<result><block>Привет мир</block></result>",
        ]
    )
    pauses: list[float] = []

    overlay = generate_overlay(profile, writer, attempts=3, pause=0.5, sleep=pauses.append)

    assert writer.calls == 3
    assert pauses == [0.5, 0.5]
    assert overlay.blocks[0].lines == ("Привет мир",)


def test_only_foreign_answers_exhaust_the_attempts(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    writer = _Writer(["<result><block>Привет OK</block></result>"] * 3)

    with pytest.raises(TextError, match="не получен"):
        generate_overlay(profile, writer, attempts=3, pause=0, sleep=lambda _: None)

    assert writer.calls == 3
