"""Runtime state of the production loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Persisted counters and timestamps of the production loop."""

    last_production_at: datetime | None
    captcha_blocked_until: datetime | None
    captcha_streak: int
    zero_runs: int
    last_alert_at: datetime | None
    last_alert_kind: str | None
    updated_at: datetime

    @classmethod
    def empty(cls) -> RuntimeState:
        """Build an empty state for a database without saved rows."""
        return cls(
            last_production_at=None,
            captcha_blocked_until=None,
            captcha_streak=0,
            zero_runs=0,
            last_alert_at=None,
            last_alert_kind=None,
            updated_at=datetime.now(UTC),
        )
