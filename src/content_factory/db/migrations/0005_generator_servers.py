"""Journal of rented Vast.ai generator servers.

A row is inserted before ``create_instance`` and reaches ``destroyed`` only
after a verified teardown. The table is the single source of truth about a
rental; ``run.json`` remains a report. ``contract_id`` is unique only among
filled values, so many rows may still wait for their contract. The statements
are portable between SQLite and PostgreSQL and safe to apply twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0005"

_GENERATOR_SERVERS = """
CREATE TABLE IF NOT EXISTS generator_servers (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    profile TEXT,
    status TEXT NOT NULL,
    contract_id TEXT,
    ip TEXT,
    port INTEGER,
    gpu TEXT,
    image TEXT,
    cost_per_hour REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    destroyed_at TEXT,
    last_error TEXT
)
"""

_CONTRACT_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS generator_servers_contract_id
ON generator_servers (contract_id)
WHERE contract_id IS NOT NULL
"""


def apply(connection: Connection) -> None:
    """Create the generator-server journal table and its unique contract index.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    connection.execute(_GENERATOR_SERVERS)
    connection.execute(_CONTRACT_INDEX)


__all__ = ["VERSION", "apply"]
