"""Manual profiles_describe flow: filled profiles, failures and disabled mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.unit.common.profile_helpers import add_profile

from content_factory.exceptions.generator import TextError
from content_factory.flows.profiles_describe.flow import profiles_describe

_ENV_NAMES = (
    "OPENAI_BASE_URL",
    "GENERATOR_MODEL",
    "GENERATOR_OUTPUT_DIR",
)
_TITLE = "Знакомства для 30+"
_SUMMARY = "Спокойные знакомства с акцентом на серьёзные отношения."


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory and a fake model."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://api.test/v1")


class _Writer:
    """Text model double that fails for profiles named in *failing*."""

    def __init__(self, failing: tuple[str, ...] = ()) -> None:
        self.failing = failing
        self.prompts: list[str] = []

    def write(self, system_prompt: str, user_prompt: str = "") -> str:
        self.prompts.append(system_prompt)
        if any(f"name (имя): {name}" in system_prompt for name in self.failing):
            raise TextError("текстовая модель недоступна")
        return json.dumps({"title": _TITLE, "summary": _SUMMARY}, ensure_ascii=False)


def _data(profile: Path) -> dict[str, Any]:
    return yaml.safe_load((profile / "profile.yaml").read_text(encoding="utf-8"))


def test_flow_is_callable_and_named() -> None:
    assert callable(profiles_describe)
    assert profiles_describe.name == "profiles_describe"


def test_filled_profile_is_skipped_and_empty_one_is_described(tmp_path: Path) -> None:
    filled = add_profile(
        tmp_path,
        "dating",
        "profile_1",
        title="Уже описан",
        summary="У профиля уже есть название и пояснение для оператора.",
    )
    empty = add_profile(tmp_path, "dating", "profile_2")
    writer = _Writer()

    outcome = profiles_describe.fn(writer=writer)

    assert outcome == "described=1 skipped=1 failed=0"
    assert len(writer.prompts) == 1
    assert "name (имя): profile_2" in writer.prompts[0]
    assert _data(filled)["title"] == "Уже описан"
    assert _data(empty)["title"] == _TITLE
    assert _data(empty)["summary"] == _SUMMARY


def test_model_failure_does_not_stop_the_next_profile(tmp_path: Path) -> None:
    failing = add_profile(tmp_path, "dating", "profile_1")
    described = add_profile(tmp_path, "dating", "profile_2")
    writer = _Writer(failing=("profile_1",))

    outcome = profiles_describe.fn(writer=writer)

    assert outcome == "described=1 skipped=0 failed=1"
    assert "title" not in _data(failing)
    assert _data(described)["title"] == _TITLE


def test_flow_without_api_key_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    add_profile(tmp_path, "dating", "profile_1")
    writer = _Writer()
    monkeypatch.delenv("OPENAI_API_KEY")

    outcome = profiles_describe.fn(writer=writer)

    assert outcome == "disabled"
    assert writer.prompts == []


def test_second_run_skips_the_described_profile(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1")

    assert profiles_describe.fn(writer=_Writer()) == "described=1 skipped=0 failed=0"
    second = _Writer()

    assert profiles_describe.fn(writer=second) == "described=0 skipped=1 failed=0"
    assert second.prompts == []
