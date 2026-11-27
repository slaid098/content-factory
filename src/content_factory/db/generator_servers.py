"""Journal rows of rented Vast.ai generator servers.

The table is the single source of truth about a rental: a row appears before
``create_instance`` and reaches ``destroyed`` only after a verified teardown.
A rental that disappeared without a verified teardown ends in ``lost``. All
statements go through the shared connection port so SQLite and PostgreSQL
behave the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from content_factory.db.connection import Connection
from content_factory.schemas.database.port import Row

RENTING = "renting"
READY = "ready"
DESTROYING = "destroying"
DESTROYED = "destroyed"
LOST = "lost"

UNFINISHED_STATUSES = (RENTING, READY, DESTROYING)

_COLUMNS = (
    "id, run_id, profile, status, contract_id, ip, port, gpu, image, "
    "cost_per_hour, created_at, updated_at, destroyed_at, last_error"
)

_INSERT = """
INSERT INTO generator_servers (
    id, run_id, profile, status, contract_id, ip, port, gpu, image,
    cost_per_hour, created_at, updated_at, destroyed_at, last_error
) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, NULL, NULL)
"""

_BIND = """
UPDATE generator_servers
SET contract_id = ?, updated_at = ?
WHERE id = ? AND contract_id IS NULL
"""

_READY = """
UPDATE generator_servers
SET status = ?, ip = ?, port = ?, gpu = ?, image = ?,
    cost_per_hour = ?, updated_at = ?
WHERE id = ? AND status = ?
"""

_DESTROYING = """
UPDATE generator_servers
SET status = ?, updated_at = ?, last_error = COALESCE(?, last_error)
WHERE id = ? AND status IN (?, ?, ?)
"""

_DESTROYED = """
UPDATE generator_servers
SET status = ?, updated_at = ?, destroyed_at = ?, last_error = NULL
WHERE id = ? AND status IN (?, ?, ?)
"""

_LOST = """
UPDATE generator_servers
SET status = ?, updated_at = ?, last_error = COALESCE(?, last_error)
WHERE id = ? AND status IN (?, ?, ?)
"""


@dataclass(frozen=True, slots=True)
class GeneratorServerRow:
    """One ``generator_servers`` row exactly as stored."""

    id: str
    run_id: str | None
    profile: str | None
    status: str
    contract_id: str | None
    ip: str | None
    port: int | None
    gpu: str | None
    image: str | None
    cost_per_hour: float | None
    created_at: str
    updated_at: str
    destroyed_at: str | None
    last_error: str | None


def insert_renting(
    connection: Connection,
    *,
    server_id: str,
    run_id: str | None,
    profile: str | None,
    gpu: str | None,
    image: str | None,
    cost_per_hour: float | None,
    now: str,
) -> None:
    """Insert the ``renting`` row of a server that is about to be created.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        run_id: Generator run that rents the server, when known.
        profile: Profile name that requests the server, when known.
        gpu: Requested GPU name.
        image: Requested Docker image.
        cost_per_hour: Offer price per hour, when known.
        now: ISO 8601 moment of the row.
    """
    connection.execute(
        _INSERT,
        (server_id, run_id, profile, RENTING, gpu, image, cost_per_hour, now, now),
    )


def bind_contract(connection: Connection, server_id: str, contract_id: str, now: str) -> None:
    """Bind the Vast.ai contract id as soon as ``create_instance`` returns.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        contract_id: Vast.ai contract id returned by the provider.
        now: ISO 8601 moment of the update.
    """
    connection.execute(_BIND, (contract_id, now, server_id))


def mark_ready(
    connection: Connection,
    server_id: str,
    *,
    ip: str,
    port: int,
    gpu: str,
    image: str,
    cost_per_hour: float | None,
    now: str,
) -> None:
    """Store the address of a server whose ComfyUI is ready.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        ip: Public IP address of the instance.
        port: Public ComfyUI port.
        gpu: GPU reported by the instance.
        image: Image reported by the instance.
        cost_per_hour: Price per hour of the rented offer.
        now: ISO 8601 moment of the update.
    """
    connection.execute(
        _READY,
        (READY, ip, port, gpu, image, cost_per_hour, now, server_id, RENTING),
    )


def mark_destroying(
    connection: Connection,
    server_id: str,
    now: str,
    error: str | None = None,
) -> None:
    """Mark the rental as being destroyed now.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        now: ISO 8601 moment of the update.
        error: Reason of the teardown, when it already failed once.
    """
    connection.execute(
        _DESTROYING,
        (DESTROYING, now, error, server_id, RENTING, READY, DESTROYING),
    )


def mark_destroyed(connection: Connection, server_id: str, now: str) -> None:
    """Mark the rental as destroyed after a verified teardown.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        now: ISO 8601 moment of the verified teardown.
    """
    connection.execute(
        _DESTROYED,
        (DESTROYED, now, now, server_id, RENTING, READY, DESTROYING),
    )


def mark_lost(
    connection: Connection,
    server_id: str,
    now: str,
    error: str | None = None,
) -> None:
    """Mark the terminal ``lost`` state of a rental whose instance vanished.

    Args:
        connection: Open connection to the operational database.
        server_id: Journal identifier of the rental.
        now: ISO 8601 moment the loss was noticed.
        error: Reason the rental was declared lost.
    """
    connection.execute(
        _LOST,
        (LOST, now, error, server_id, RENTING, READY, DESTROYING),
    )


def list_unfinished(connection: Connection) -> list[GeneratorServerRow]:
    """Return every row that has not reached a terminal state yet."""
    placeholders = ", ".join("?" for _ in UNFINISHED_STATUSES)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS} FROM generator_servers
        WHERE status IN ({placeholders})
        ORDER BY created_at, id
        """,
        UNFINISHED_STATUSES,
    ).fetchall()
    return [_row(row) for row in rows]


def get_row(connection: Connection, server_id: str) -> GeneratorServerRow | None:
    """Return one journal row by id, or ``None``."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM generator_servers WHERE id = ?",
        (server_id,),
    ).fetchone()
    return None if row is None else _row(row)


def _row(row: Row) -> GeneratorServerRow:
    """Build one journal row from a database row."""
    return GeneratorServerRow(
        id=str(row["id"]),
        run_id=None if row["run_id"] is None else str(row["run_id"]),
        profile=None if row["profile"] is None else str(row["profile"]),
        status=str(row["status"]),
        contract_id=None if row["contract_id"] is None else str(row["contract_id"]),
        ip=None if row["ip"] is None else str(row["ip"]),
        port=None if row["port"] is None else int(row["port"]),
        gpu=None if row["gpu"] is None else str(row["gpu"]),
        image=None if row["image"] is None else str(row["image"]),
        cost_per_hour=(None if row["cost_per_hour"] is None else float(row["cost_per_hour"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        destroyed_at=None if row["destroyed_at"] is None else str(row["destroyed_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


__all__ = [
    "DESTROYED",
    "DESTROYING",
    "LOST",
    "READY",
    "RENTING",
    "UNFINISHED_STATUSES",
    "GeneratorServerRow",
    "bind_contract",
    "get_row",
    "insert_renting",
    "list_unfinished",
    "mark_destroyed",
    "mark_destroying",
    "mark_lost",
    "mark_ready",
]
