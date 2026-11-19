"""Persistence of the production loop state."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import cast

from content_factory.schemas.state.state import RuntimeState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_production_at TEXT,
    captcha_blocked_until TEXT,
    captcha_streak INTEGER NOT NULL DEFAULT 0,
    zero_runs INTEGER NOT NULL DEFAULT 0,
    last_alert_at TEXT,
    last_alert_kind TEXT,
    updated_at TEXT NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the single-row ``runtime_state`` table when missing."""
    conn.execute(_SCHEMA)
    conn.commit()


def load_state(conn: sqlite3.Connection) -> RuntimeState:
    """Load the saved state, or an empty state when nothing was saved yet."""
    ensure_schema(conn)
    return read_state(conn)


def read_state(conn: sqlite3.Connection) -> RuntimeState:
    """Read the saved state without creating or changing the schema."""
    if not _state_table_exists(conn):
        return RuntimeState.empty()
    row = conn.execute(
        """
        SELECT last_production_at, captcha_blocked_until, captcha_streak,
               zero_runs, last_alert_at, last_alert_kind, updated_at
        FROM runtime_state
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return RuntimeState.empty()
    return RuntimeState(
        last_production_at=_parse_time(row["last_production_at"]),
        captcha_blocked_until=_parse_time(row["captcha_blocked_until"]),
        captcha_streak=row["captcha_streak"],
        zero_runs=row["zero_runs"],
        last_alert_at=_parse_time(row["last_alert_at"]),
        last_alert_kind=row["last_alert_kind"],
        updated_at=cast(datetime, _parse_time(row["updated_at"])),
    )


def _state_table_exists(conn: sqlite3.Connection) -> bool:
    """Return whether the state table has been created yet."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runtime_state'"
    ).fetchone()
    return row is not None


def save_state(conn: sqlite3.Connection, state: RuntimeState) -> None:
    """Upsert the single state row and refresh ``updated_at``."""
    ensure_schema(conn)
    updated_at = datetime.now(UTC)
    conn.execute(
        """
        INSERT INTO runtime_state (
            id, last_production_at, captcha_blocked_until, captcha_streak,
            zero_runs, last_alert_at, last_alert_kind, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_production_at = excluded.last_production_at,
            captcha_blocked_until = excluded.captcha_blocked_until,
            captcha_streak = excluded.captcha_streak,
            zero_runs = excluded.zero_runs,
            last_alert_at = excluded.last_alert_at,
            last_alert_kind = excluded.last_alert_kind,
            updated_at = excluded.updated_at
        """,
        (
            _format_time(state.last_production_at),
            _format_time(state.captcha_blocked_until),
            state.captcha_streak,
            state.zero_runs,
            _format_time(state.last_alert_at),
            state.last_alert_kind,
            updated_at.isoformat(),
        ),
    )
    conn.commit()


def _format_time(value: datetime | None) -> str | None:
    """Serialize an aware datetime to ISO 8601."""
    return value.isoformat() if value is not None else None


def _parse_time(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime stored in the database."""
    return datetime.fromisoformat(value) if value is not None else None
