"""Vast.ai CLI wrapper: query dialect, parsing, errors and instance calls."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from content_factory.exceptions.generator import RentError
from content_factory.services.generator import vast_cli
from content_factory.services.generator.vast_cli import (
    VastProvider,
    _already_gone,
    _classify,
    _cli_error,
    _cli_order,
    _cli_query,
    _cli_value,
    _default_command,
    _env_line,
    _json_error,
    _json_object,
    _new_contract,
    _numeric,
    _run_cli,
    _stderr_noise,
    _VastFatal,
    _VastInsufficientBalance,
    _VastTransient,
)


def test_cli_query_renders_flags_orders_and_operator_tokens() -> None:
    flags, tokens = _cli_query(
        {
            "limit": "5",
            "order": ["dph_total", ["ram", "desc"]],
            "type": "bid",
            "rentable": {"eq": None},
            "gpu_name": {"in": ["L40S", None]},
        }
    )

    assert flags == ["--limit", "5", "--order", "dph_total,-ram", "--type", "bid"]
    assert tokens == ["gpu_name in L40S"]


def test_cli_query_skips_unknown_operators_and_empty_orders() -> None:
    flags, tokens = _cli_query({"order": [], "gpu_ram": {"near": 30, "gte": None}})

    assert flags == []
    assert tokens == []


def test_cli_order_keeps_plain_entries_and_directions() -> None:
    assert _cli_order([["a", "desc"], ["b", "asc"], "c", (), None]) == "-a,b,c"
    assert _cli_order(None) == ""


def test_cli_value_renders_bool_numbers_and_spaces() -> None:
    assert _cli_value(True) == "true"
    assert _cli_value(False) == "false"
    assert _cli_value(30) == "30"
    assert _cli_value(30.0) == "30"
    assert _cli_value(30.5) == "30.5"
    assert _cli_value("L 40 S") == "L_40_S"


def test_numeric_drops_trailing_zero_and_handles_none() -> None:
    assert _numeric("80") == "80"
    assert _numeric(1.5) == "1.5"
    assert _numeric(None) == "0"


def test_env_line_joins_container_and_port_options() -> None:
    default = _env_line({"A": 1}, None)
    custom = _env_line(None, ["--gpus all", ""])
    empty = _env_line(None, [])

    assert default == "-e A=1 -p 8188:8188 --dns=8.8.8.8 --dns=8.8.4.4 --network=bridge"
    assert custom == "--gpus all"
    assert empty == ""


def test_new_contract_requires_a_value() -> None:
    assert _new_contract({"new_contract": " 42 "}) == "42"

    with pytest.raises(RentError, match="new_contract"):
        _new_contract({})


def test_default_command_uses_the_installed_package(monkeypatch) -> None:
    monkeypatch.setattr(
        vast_cli.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin="/opt/vastai/vast.py"),
    )

    assert _default_command() == [vast_cli.sys.executable, "/opt/vastai/vast.py"]


def test_default_command_reports_missing_and_broken_packages(monkeypatch) -> None:
    monkeypatch.setattr(vast_cli.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(_VastFatal, match="vastai"):
        _default_command()

    monkeypatch.setattr(
        vast_cli.importlib.util,
        "find_spec",
        lambda name: SimpleNamespace(origin=None),
    )

    with pytest.raises(_VastFatal, match="vastai"):
        _default_command()

    def broken(name: str) -> Any:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(vast_cli.importlib.util, "find_spec", broken)

    with pytest.raises(_VastFatal, match="vastai"):
        _default_command()


def test_run_cli_returns_output_and_passes_environment(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(vast_cli.subprocess, "run", fake_run)

    assert _run_cli(["vast", "show"], {"TOKEN": "x"}, 3.0) == ("ok", "", 0)
    assert captured["command"] == ["vast", "show"]
    assert captured["env"] == {"TOKEN": "x"}
    assert captured["timeout"] == 3.0
    assert captured["check"] is False


def test_run_cli_maps_timeout_and_oserror(monkeypatch) -> None:
    def timeout(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 3.0)

    monkeypatch.setattr(vast_cli.subprocess, "run", timeout)

    with pytest.raises(_VastTransient, match="не ответил"):
        _run_cli(["vast"], {}, 3.0)

    def broken(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no exec")

    monkeypatch.setattr(vast_cli.subprocess, "run", broken)

    with pytest.raises(_VastFatal, match="запустить"):
        _run_cli(["vast"], {}, 3.0)


def test_cli_error_prefers_json_stderr_and_failed_stdout() -> None:
    auth = _cli_error(
        "",
        'noise\n{"error": true, "status_code": 401, "msg": "bad key"}\n',
        1,
    )
    transient = _cli_error("failed with error 429: rate limit", "", 1)
    success = _cli_error("{}", "DEPRECATED message\n", 0)

    assert isinstance(auth, _VastFatal)
    assert isinstance(transient, _VastTransient)
    assert success is None


def test_cli_error_falls_back_to_exit_code() -> None:
    error = _cli_error("", "   ", 7)

    assert error is not None
    assert "exit code 7" in str(error)


def test_json_error_reads_last_object_and_ignores_broken_lines() -> None:
    assert _json_error('{"error": false}\nnot json\n{"error": true, "msg": "boom"}') is not None
    assert _json_error("") is None
    assert _json_object("[1, 2]") is None
    assert _json_object("{broken") is None


def test_stderr_noise_drops_informational_lines() -> None:
    text = "DEPRECATED flag\nSent key from env\n  Unset the VAST_API_KEY\n real error \n"

    assert _stderr_noise(text) == "real error"


def test_classify_separates_auth_balance_transient_and_generic() -> None:
    assert isinstance(_classify(401, "nope"), _VastFatal)
    assert isinstance(_classify(403, "nope"), _VastFatal)
    assert isinstance(_classify(None, "invalid api key"), _VastFatal)
    assert isinstance(_classify(None, "insufficient credit"), _VastInsufficientBalance)
    assert isinstance(_classify(429, "slow down"), _VastTransient)
    assert isinstance(_classify(503, "unavailable"), _VastTransient)
    assert isinstance(_classify(None, "connection reset"), _VastTransient)
    assert type(_classify(None, "boom")) is RentError


def test_already_gone_matches_known_phrases() -> None:
    assert _already_gone("Instance 7 not found")
    assert _already_gone("ALREADY DESTROYED")
    assert not _already_gone("permission denied")


def test_search_offers_parses_wrapped_and_plain_lists(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ('{"offers": [{"id": 1}]}', "", 0))
    assert provider.search_offers() == [{"id": 1}]

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ('[{"id": 2}]', "", 0))
    assert provider.search_offers({"limit": 1}) == [{"id": 2}]

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ("null", "", 0))
    assert provider.search_offers() == []

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ('{"offers": 3}', "", 0))
    assert provider.search_offers() == []


def test_create_instance_builds_full_cli_arguments(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])
    captured: list[str] = []

    def fake_run(args: list[str]) -> dict[str, Any]:
        captured.extend(args)
        return {"new_contract": 77}

    monkeypatch.setattr(provider, "_run", fake_run)

    contract = provider.create_instance(
        {
            "offer_id": 9,
            "image": "image:tag",
            "disk": 80,
            "env": {"OPEN_BUTTON_TOKEN": "comfy"},
            "label": "content-factory-generator",
            "onstart_cmd": "bash /start.sh",
            "image_login": "user",
        }
    )

    assert contract == "77"
    assert captured[:6] == ["create", "instance", "9", "--image", "image:tag", "--disk"]
    assert "80" in captured
    assert captured[-8:] == [
        "--env",
        "-e OPEN_BUTTON_TOKEN=comfy -p 8188:8188 --dns=8.8.8.8 --dns=8.8.4.4 --network=bridge",
        "--label",
        "content-factory-generator",
        "--onstart-cmd",
        "bash /start.sh",
        "--login",
        "user",
    ]


def test_create_instance_rejects_missing_offer_or_image(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    with pytest.raises(_VastFatal, match="offer_id"):
        provider.create_instance({"image": "image"})

    with pytest.raises(_VastFatal, match="image"):
        provider.create_instance({"offer_id": "1"})


def test_destroy_instance_treats_gone_instance_as_success(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    def gone(args: list[str]) -> dict[str, Any]:
        raise RentError("Instance 7 not found")

    monkeypatch.setattr(provider, "_run", gone)
    assert provider.destroy_instance("7") is True

    def broken(args: list[str]) -> dict[str, Any]:
        raise RentError("permission denied")

    monkeypatch.setattr(provider, "_run", broken)
    with pytest.raises(RentError, match="permission denied"):
        provider.destroy_instance("7")

    def transient(args: list[str]) -> dict[str, Any]:
        raise _VastTransient("timeout")

    monkeypatch.setattr(provider, "_run", transient)
    with pytest.raises(_VastTransient):
        provider.destroy_instance("7")


def test_destroy_instance_checks_false_success(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    monkeypatch.setattr(
        provider,
        "_run",
        lambda args: {"success": False, "msg": "already destroyed"},
    )
    assert provider.destroy_instance("7") is True

    monkeypatch.setattr(provider, "_run", lambda args: {"success": False, "msg": "boom"})
    with pytest.raises(RentError, match="не уничтожил"):
        provider.destroy_instance("7")

    monkeypatch.setattr(provider, "_run", lambda args: {"success": True})
    assert provider.destroy_instance("7") is True


def test_show_and_list_instances(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    monkeypatch.setattr(
        provider,
        "_run",
        lambda args: {"instances": [{"id": 1}, {"id": 2}]},
    )
    assert provider.show_instance(1) == {"id": 1}
    assert provider.list_instances() == [{"id": 1}, {"id": 2}]

    with pytest.raises(RentError, match="not found"):
        provider.show_instance(3)

    monkeypatch.setattr(provider, "_run", lambda args: None)
    assert provider.list_instances() == []

    monkeypatch.setattr(provider, "_run", lambda args: {"instances": "broken"})
    assert provider.list_instances() == []


def test_run_returns_none_for_empty_output_and_rejects_bad_json(monkeypatch) -> None:
    provider = VastProvider(api_token="token", command=["vast"])

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ("   ", "", 0))
    assert provider.list_instances() == []

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ("not-json", "", 0))
    with pytest.raises(RentError, match="не-JSON"):
        provider.list_instances()

    monkeypatch.setattr(vast_cli, "_run_cli", lambda *args: ("failed with error 401: bad", "", 1))
    with pytest.raises(_VastFatal):
        provider.list_instances()


def test_env_reads_token_from_argument_or_environment(monkeypatch) -> None:
    provider = VastProvider(api_token=" token ", command=["vast"])
    assert provider._env()["VAST_API_KEY"] == "token"

    monkeypatch.setenv("VAST_API_TOKEN", " env-token ")
    from_env = VastProvider(command=["vast"])
    assert from_env._env()["VAST_API_KEY"] == "env-token"

    monkeypatch.delenv("VAST_API_TOKEN")
    with pytest.raises(_VastFatal, match="VAST_API_TOKEN"):
        VastProvider(command=["vast"])._env()
