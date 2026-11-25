"""Persistence of the production loop state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from content_factory.db.connection import Connection
from content_factory.schemas.state.state import RuntimeState


def load_state(conn: Connection) -> RuntimeState:
    """Load the saved state, or an empty state when nothing was saved yet."""
    return read_state(conn)


def read_state(conn: Connection) -> RuntimeState:
    """Read the saved state without creating or changing the schema."""
    if not conn.has_table("runtime_state"):
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


def save_state(conn: Connection, state: RuntimeState) -> None:
    """Upsert the single state row and refresh ``updated_at``."""
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


def load_alert_times(conn: Connection) -> dict[str, datetime]:
    """Load the last successful send time of every alert kind."""
    rows = conn.execute("SELECT kind, sent_at FROM alert_cooldowns").fetchall()
    return {row["kind"]: cast(datetime, _parse_time(row["sent_at"])) for row in rows}


def save_alert_time(conn: Connection, kind: str, sent_at: datetime) -> None:
    """Persist the last successful send time of one alert kind."""
    conn.execute(
        """
        INSERT INTO alert_cooldowns (kind, sent_at) VALUES (?, ?)
        ON CONFLICT(kind) DO UPDATE SET sent_at = excluded.sent_at
        """,
        (kind, sent_at.isoformat()),
    )
    conn.commit()


def _parse_time(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime stored in the database."""
    return datetime.fromisoformat(value) if value is not None else None


__all__ = [
    "load_alert_times",
    "load_state",
    "read_state",
    "save_alert_time",
    "save_state",
]
