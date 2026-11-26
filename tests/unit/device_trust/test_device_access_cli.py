"""Operator CLI of the trusted-device capsule."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.services.device_trust import redeem_link

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "device_access.py"


def _load_cli() -> ModuleType:
    """Load ``scripts/device_access.py`` without making scripts a package."""
    spec = importlib.util.spec_from_file_location("device_access", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Point the CLI at a fresh data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_PUBLIC_BASE_URL", raising=False)
    return _load_cli()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return the database path the CLI resolves from the data directory."""
    return tmp_path / DB_DIR / DB_FILENAME


def _token_from(stdout: str) -> str:
    """Extract the raw link token from the printed join URL."""
    return stdout.strip().rsplit("/join/", 1)[1]


def _device_id_from(stderr: str) -> str:
    """Extract the device id from the issue details."""
    for line in stderr.splitlines():
        if line.startswith("device_id="):
            return line.split("=", 1)[1]
    raise AssertionError(f"no device_id in {stderr!r}")


def test_issue_prints_one_link_and_details(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["issue", "--label", "phone"]) == 0

    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    assert captured.out.startswith("https://content.slaid098.dev/join/")
    assert "device_id=" in captured.err
    assert "link_expires_at=" in captured.err
    assert "expires_at=" in captured.err


def test_issue_accepts_link_and_device_overrides(
    cli: ModuleType,
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "issue",
                "--label",
                "tablet",
                "--ttl-hours",
                "2",
                "--device-days",
                "7",
                "--base-url",
                "https://example.test/",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out.startswith("https://example.test/join/")
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT created_at, link_expires_at, expires_at FROM trusted_devices"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    created = datetime.fromisoformat(row["created_at"])
    assert datetime.fromisoformat(row["link_expires_at"]) - created == timedelta(hours=2)
    assert datetime.fromisoformat(row["expires_at"]) - created == timedelta(days=7)


def test_issue_uses_public_base_url_from_settings(
    cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_PUBLIC_BASE_URL", "https://partner.test")

    assert cli.main(["issue", "--label", "phone"]) == 0

    assert capsys.readouterr().out.startswith("https://partner.test/join/")


def test_list_shows_an_active_device(
    cli: ModuleType,
    db_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["issue", "--label", "phone"]) == 0
    issued = capsys.readouterr()
    token = _token_from(issued.out)
    redeemed = redeem_link(db_path, token)
    assert redeemed is not None

    assert cli.main(["list"]) == 0

    table = capsys.readouterr().out
    header = table.splitlines()[0]
    assert header.split() == [
        "id",
        "label",
        "status",
        "created_at",
        "activated_at",
        "last_seen_at",
        "expires_at",
    ]
    assert redeemed.device_id in table
    assert "phone" in table
    assert "active" in table


def test_revoke_unknown_id_exits_1_and_repeat_succeeds(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["revoke", "--id", "missing"]) == 1
    assert "not found" in capsys.readouterr().err

    assert cli.main(["issue", "--label", "phone"]) == 0
    device_id = _device_id_from(capsys.readouterr().err)

    assert cli.main(["revoke", "--id", device_id]) == 0
    assert cli.main(["revoke", "--id", device_id]) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["issue", "--label", "   "],
        ["issue", "--label", "x" * 151],
        ["issue", "--label", "phone", "--ttl-hours", "0"],
        ["issue", "--label", "phone", "--device-days", "3651"],
    ],
)
def test_invalid_arguments_exit_1(
    cli: ModuleType,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    assert cli.main(argv) == 1
    assert capsys.readouterr().err


def test_settings_resolve_the_same_database(cli: ModuleType, db_path: Path) -> None:
    settings = get_settings()

    assert cli.db_path(settings) == db_path
