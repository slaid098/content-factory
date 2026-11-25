"""Profile description: prompt, model answer, retries and atomic write."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from tests.unit.common.profile_helpers import add_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.errors import ProfileDescriptionError
from content_factory.exceptions.generator import TextError
from content_factory.schemas.profiles.description import ProfileDescription
from content_factory.services.generator import TextClient
from content_factory.services.profiles import describe as describe_module
from content_factory.services.profiles.describe import describe_profile

_VALID_TITLE = "Знакомства для 30+"
_VALID_SUMMARY = "Спокойные знакомства с акцентом на серьёзные отношения."


class _Writer:
    """Text model double that records prompts and returns a fixed answer."""

    def __init__(self, answer: str | Exception = "") -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.user_prompts: list[str] = []

    def write(self, system_prompt: str, user_prompt: str = "") -> str:
        self.prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _settings() -> GeneratorSettings:
    return GeneratorSettings(
        OPENAI_BASE_URL="http://api.test/v1",
        OPENAI_API_KEY="sk-test",
    )


def _answer(title: str = _VALID_TITLE, summary: str = _VALID_SUMMARY) -> str:
    return json.dumps({"title": title, "summary": summary}, ensure_ascii=False)


def _describe(tmp_path: Path, writer: _Writer) -> ProfileDescription:
    return describe_profile(tmp_path, "dating/profile_1", _settings(), writer=writer)


def _yaml_text(profile: Path) -> str:
    return (profile / "profile.yaml").read_text(encoding="utf-8")


def test_prompt_contains_profile_fields_and_prompt_fragments(tmp_path: Path) -> None:
    profile = add_profile(
        tmp_path,
        "dating",
        "profile_1",
        language="en",
        title=None,
        looks=[{"age": 30, "hair": "dark"}],
    )
    (profile / "prompt_template.txt").write_text("Сцена: {age}", encoding="utf-8")
    (profile / "text_prompt.txt").write_text("Голос: спокойный", encoding="utf-8")
    writer = _Writer(_answer())

    result = _describe(tmp_path, writer)

    assert result == ProfileDescription(_VALID_TITLE, _VALID_SUMMARY)
    system = writer.prompts[0]
    user = writer.user_prompts[0]
    assert "category (категория): dating" in system
    assert "name (имя): profile_1" in system
    assert "language (язык): en" in system
    assert "niche (ниша): dating" in system
    assert "JSON" in system
    assert "Сцена: {age}" in user
    assert "Голос: спокойный" in user
    assert "Опиши профиль" in user
    assert "Сцена: {age}" not in system
    assert "Голос: спокойный" not in system
    assert len(writer.prompts) == 1


def test_valid_answer_is_written_and_keeps_other_keys(tmp_path: Path) -> None:
    profile = add_profile(
        tmp_path,
        "dating",
        "profile_1",
        looks=[{"age": 30, "hair": "dark"}],
        production=[{"producer": "vast", "type": "video", "qa": ["media", "clef"]}],
    )

    _describe(tmp_path, _Writer(_answer()))

    data = yaml.safe_load(_yaml_text(profile))
    assert data["title"] == _VALID_TITLE
    assert data["summary"] == _VALID_SUMMARY
    assert data["language"] == "ru"
    assert data["looks"] == [{"age": 30, "hair": "dark"}]
    assert data["production"] == [{"producer": "vast", "type": "video", "qa": ["media", "clef"]}]


def test_second_call_does_not_ask_the_model(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1")
    first = _Writer(_answer())
    _describe(tmp_path, first)
    second = _Writer(_answer())

    result = _describe(tmp_path, second)

    assert result == ProfileDescription(_VALID_TITLE, _VALID_SUMMARY)
    assert second.prompts == []


def test_partial_profile_is_completed(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1", title="Старое имя")
    writer = _Writer(_answer())

    _describe(tmp_path, writer)

    assert len(writer.prompts) == 1
    data = yaml.safe_load(_yaml_text(tmp_path / "profiles" / "dating" / "profile_1"))
    assert data["title"] == _VALID_TITLE
    assert data["summary"] == _VALID_SUMMARY


def test_markdown_fence_and_extra_keys_are_accepted(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    answer = (
        "```json\n"
        + json.dumps(
            {"title": _VALID_TITLE, "summary": _VALID_SUMMARY, "extra": "ignored"},
            ensure_ascii=False,
        )
        + "\n```"
    )
    writer = _Writer(answer)

    result = _describe(tmp_path, writer)

    assert result == ProfileDescription(_VALID_TITLE, _VALID_SUMMARY)
    assert len(writer.prompts) == 1
    data = yaml.safe_load(_yaml_text(profile))
    assert "extra" not in data


@pytest.mark.parametrize(
    "answer",
    [
        "не JSON",
        "[1, 2]",
        json.dumps({"title": _VALID_TITLE}),
        json.dumps({"title": "ok", "summary": _VALID_SUMMARY}),
        json.dumps({"title": "я" * 81, "summary": _VALID_SUMMARY}, ensure_ascii=False),
        json.dumps({"title": _VALID_TITLE, "summary": "коротко"}, ensure_ascii=False),
        json.dumps(
            {"title": _VALID_TITLE, "summary": "я" * 301},
            ensure_ascii=False,
        ),
        json.dumps(
            {"title": "Первая строка\nвторая", "summary": _VALID_SUMMARY},
            ensure_ascii=False,
        ),
        json.dumps(
            {"title": _VALID_TITLE, "summary": "Первая строка\nвторая"},
            ensure_ascii=False,
        ),
        json.dumps({"title": "abc\x00def", "summary": _VALID_SUMMARY}, ensure_ascii=False),
        json.dumps({"title": "abc\u202edef", "summary": _VALID_SUMMARY}, ensure_ascii=False),
    ],
)
def test_invalid_answer_retries_once_and_keeps_file(
    tmp_path: Path,
    answer: str,
) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    before = _yaml_text(profile)
    writer = _Writer(answer)

    with pytest.raises(ProfileDescriptionError):
        _describe(tmp_path, writer)

    assert len(writer.prompts) == 2
    assert _yaml_text(profile) == before


def test_lone_surrogate_answer_retries_once_and_keeps_file(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    before = _yaml_text(profile)
    answer = json.dumps({"title": "\ud800" * 3, "summary": _VALID_SUMMARY})
    writer = _Writer(answer)

    with pytest.raises(ProfileDescriptionError, match="непечатаемые"):
        _describe(tmp_path, writer)

    assert len(writer.prompts) == 2
    assert _yaml_text(profile) == before


def test_model_error_retries_once_and_keeps_file(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    before = _yaml_text(profile)
    writer = _Writer(TextError("текстовая модель недоступна"))

    with pytest.raises(ProfileDescriptionError, match="недоступна") as raised:
        _describe(tmp_path, writer)

    assert len(writer.prompts) == 2
    assert _yaml_text(profile) == before
    assert "sk-test" not in str(raised.value)


def test_error_message_does_not_repeat_the_model_answer(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1")
    secret = '{"title": "секрет", "summary": "секрет"}'
    writer = _Writer(secret)

    with pytest.raises(ProfileDescriptionError) as raised:
        _describe(tmp_path, writer)

    assert "секрет" not in str(raised.value)


def test_write_failure_keeps_the_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    before = _yaml_text(profile)

    def _fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk is full")

    monkeypatch.setattr(describe_module.os, "replace", _fail)

    with pytest.raises(ProfileDescriptionError, match="записать"):
        _describe(tmp_path, _Writer(_answer()))

    assert _yaml_text(profile) == before
    assert not list(profile.glob("*.tmp"))


def test_write_keeps_the_original_permissions(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    path = profile / "profile.yaml"
    path.chmod(0o640)

    _describe(tmp_path, _Writer(_answer()))

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_prompt_without_prompt_files_uses_profile_yaml_only(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1")
    writer = _Writer(_answer())

    _describe(tmp_path, writer)

    user = writer.user_prompts[0]
    assert "Сцена (prompt_template):" not in user
    assert "Голос и стиль (text_prompt):" not in user
    assert "language (язык): ru" in writer.prompts[0]
    assert "Опиши профиль" in user


def test_video_prompt_is_the_legacy_scene_prompt(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    (profile / "video_prompt.txt").write_text("Старая сцена", encoding="utf-8")
    writer = _Writer(_answer())

    _describe(tmp_path, writer)

    assert "Старая сцена" in writer.user_prompts[0]


def test_prompt_fragment_is_capped(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    (profile / "prompt_template.txt").write_text("x" * 25_000, encoding="utf-8")
    writer = _Writer(_answer())

    _describe(tmp_path, writer)

    assert writer.user_prompts[0].count("x") == 20_000


def test_payload_keeps_all_fragments_and_task_user_message(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    scene = "s" * 25_000
    voice = "v" * 10_000
    (profile / "prompt_template.txt").write_text(scene, encoding="utf-8")
    (profile / "text_prompt.txt").write_text(voice, encoding="utf-8")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _answer()}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        describe_profile(
            tmp_path,
            "dating/profile_1",
            _settings(),
            writer=TextClient(_settings(), client=client),
        )

    messages = seen["body"]["messages"]
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert "Опиши профиль" in user
    assert "Сгенерируй один текст для одного видео" not in user
    assert "Опиши профиль" not in system
    assert "s" * 20_000 in user
    assert "v" * 10_000 in user
    assert "s" * 20_000 not in system


def test_missing_profile_raises_description_error(tmp_path: Path) -> None:
    with pytest.raises(ProfileDescriptionError, match="недоступен"):
        describe_profile(tmp_path, "dating/missing", _settings(), writer=_Writer(_answer()))


def test_unreadable_prompt_file_raises_description_error(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    (profile / "text_prompt.txt").write_bytes(b"\xff")
    writer = _Writer(_answer())

    with pytest.raises(ProfileDescriptionError, match="text_prompt"):
        _describe(tmp_path, writer)

    assert writer.prompts == []


def test_unreadable_profile_yaml_raises_description_error(tmp_path: Path) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    (profile / "profile.yaml").write_bytes(b"title: \xff\n")

    with pytest.raises(ProfileDescriptionError, match="профиль недоступен"):
        _describe(tmp_path, _Writer(_answer()))


@pytest.mark.parametrize("answer", [None, []])
def test_non_mapping_profile_yaml_is_rejected(
    tmp_path: Path,
    answer: Any,
) -> None:
    profile = add_profile(tmp_path, "dating", "profile_1")
    (profile / "profile.yaml").write_text(
        yaml.safe_dump(answer, allow_unicode=True),
        encoding="utf-8",
    )

    with pytest.raises(ProfileDescriptionError, match="профиль недоступен"):
        _describe(tmp_path, _Writer(_answer()))
