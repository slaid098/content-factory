"""Trusted-device capsule against SQLite."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.schemas.database.port import Connection
from content_factory.services.device_trust import (
    authenticate,
    issue_link,
    list_devices,
    redeem_link,
    revoke_device,
)

_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capsule at a fresh SQLite database."""
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    return tmp_path / DB_DIR / DB_FILENAME


@pytest.fixture
def connection(db_path: Path) -> Iterator[Connection]:
    """Open the database the capsule has just migrated."""
    store = connect(db_path)
    yield store
    store.close()


def test_issue_link_stores_only_the_digest(
    db_path: Path,
    connection: Connection,
) -> None:
    issued = issue_link(db_path, " phone ", 24, 180, now=_NOW)

    assert len(issued.device_id) == 32
    assert issued.token
    assert issued.link_expires_at == _NOW + timedelta(hours=24)
    assert issued.expires_at == _NOW + timedelta(days=180)
    row = connection.execute(
        """
        SELECT label, link_hash, link_expires_at, token_hash, created_at, expires_at
        FROM trusted_devices WHERE id = ?
        """,
        (issued.device_id,),
    ).fetchone()
    assert row is not None
    assert row["label"] == "phone"
    assert row["link_hash"] == hashlib.sha256(issued.token.encode()).hexdigest()
    assert row["link_hash"] != issued.token
    assert row["link_expires_at"] == (_NOW + timedelta(hours=24)).isoformat()
    assert row["token_hash"] is None
    assert row["created_at"] == _NOW.isoformat()
    assert row["expires_at"] == (_NOW + timedelta(days=180)).isoformat()
    assert list_devices(db_path, now=_NOW)[0].status == "pending"


def test_redeem_swaps_the_link_for_a_device_token(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 24, 180, now=_NOW)

    redeemed = redeem_link(db_path, issued.token, now=_NOW + timedelta(hours=1))

    assert redeemed is not None
    assert redeemed.device_id == issued.device_id
    assert redeemed.label == "phone"
    assert redeemed.token != issued.token
    assert redeemed.expires_at == _NOW + timedelta(days=180)
    info = authenticate(db_path, redeemed.token, now=_NOW + timedelta(hours=2))
    assert info is not None
    assert info.status == "active"
    assert info.activated_at == _NOW + timedelta(hours=1)
    assert redeem_link(db_path, issued.token, now=_NOW + timedelta(hours=2)) is None


def test_authenticate_rejects_unknown_and_revoked_tokens(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 24, 180, now=_NOW)
    redeemed = redeem_link(db_path, issued.token, now=_NOW)
    assert redeemed is not None

    assert authenticate(db_path, "unknown-token", now=_NOW) is None
    assert revoke_device(db_path, redeemed.device_id, now=_NOW + timedelta(hours=1))
    assert authenticate(db_path, redeemed.token, now=_NOW + timedelta(hours=2)) is None


def test_revoke_is_idempotent_and_reports_unknown_ids(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 24, 180, now=_NOW)

    assert revoke_device(db_path, issued.device_id, now=_NOW)
    assert revoke_device(db_path, issued.device_id, now=_NOW)
    assert not revoke_device(db_path, "missing", now=_NOW)


def test_redeem_refuses_an_expired_link(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 1, 180, now=_NOW)

    assert redeem_link(db_path, issued.token, now=_NOW + timedelta(hours=2)) is None


def test_redeem_refuses_an_expired_device_with_a_longer_link(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 720, 1, now=_NOW)

    assert redeem_link(db_path, issued.token, now=_NOW + timedelta(days=2)) is None


def test_authenticate_refuses_an_expired_device(db_path: Path) -> None:
    issued = issue_link(db_path, "phone", 24, 1, now=_NOW)
    redeemed = redeem_link(db_path, issued.token, now=_NOW + timedelta(hours=1))
    assert redeemed is not None

    assert authenticate(db_path, redeemed.token, now=_NOW + timedelta(days=2)) is None
    assert list_devices(db_path, now=_NOW + timedelta(days=2))[0].status == "expired"


def test_list_reports_each_lifecycle_status(db_path: Path) -> None:
    pending = issue_link(db_path, "pending", 24, 180, now=_NOW)
    active = issue_link(db_path, "active", 24, 180, now=_NOW)
    revoked = issue_link(db_path, "revoked", 24, 180, now=_NOW)
    expired = issue_link(db_path, "expired", 24, 1, now=_NOW)
    active_device = redeem_link(db_path, active.token, now=_NOW)
    revoked_device = redeem_link(db_path, revoked.token, now=_NOW)
    assert active_device is not None
    assert revoked_device is not None
    assert revoke_device(db_path, revoked_device.device_id, now=_NOW)

    statuses = {
        device.id: device.status for device in list_devices(db_path, now=_NOW + timedelta(days=2))
    }

    assert statuses[pending.device_id] == "pending"
    assert statuses[active_device.device_id] == "active"
    assert statuses[revoked.device_id] == "revoked"
    assert statuses[expired.device_id] == "expired"


@pytest.mark.parametrize("label", ["", "   ", "x" * 151])
def test_invalid_labels_are_rejected(db_path: Path, label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        issue_link(db_path, label, 24, 180, now=_NOW)


@pytest.mark.parametrize(
    ("link_ttl_hours", "device_ttl_days"),
    [(0, 180), (721, 180), (24, 0), (24, 3651)],
)
def test_ttls_outside_bounds_are_rejected(
    db_path: Path,
    link_ttl_hours: int,
    device_ttl_days: int,
) -> None:
    with pytest.raises(ValueError):
        issue_link(db_path, "phone", link_ttl_hours, device_ttl_days, now=_NOW)


def test_last_seen_refreshes_at_most_once_an_hour(
    db_path: Path,
    connection: Connection,
) -> None:
    issued = issue_link(db_path, "phone", 24, 180, now=_NOW)
    redeemed = redeem_link(db_path, issued.token, now=_NOW)
    assert redeemed is not None

    authenticate(db_path, redeemed.token, now=_NOW + timedelta(minutes=30))
    stored = connection.execute(
        "SELECT last_seen_at FROM trusted_devices WHERE id = ?",
        (redeemed.device_id,),
    ).fetchone()
    assert stored is not None
    assert stored["last_seen_at"] == _NOW.isoformat()

    authenticate(db_path, redeemed.token, now=_NOW + timedelta(minutes=90))
    refreshed = connection.execute(
        "SELECT last_seen_at FROM trusted_devices WHERE id = ?",
        (redeemed.device_id,),
    ).fetchone()
    assert refreshed is not None
    assert refreshed["last_seen_at"] == (_NOW + timedelta(minutes=90)).isoformat()


def test_migration_creates_the_trusted_devices_table(connection: Connection) -> None:
    assert connection.has_table("trusted_devices") is True
    assert connection.columns("trusted_devices") == {
        "id",
        "label",
        "link_hash",
        "link_expires_at",
        "token_hash",
        "created_at",
        "activated_at",
        "last_seen_at",
        "revoked_at",
        "expires_at",
    }
    versions = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert [row[0] for row in versions] == ["0001", "0002", "0003", "0004"]
