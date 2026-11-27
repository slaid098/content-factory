"""Journal of rented Vast.ai generator servers: schema and lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.db import generator_servers
from content_factory.db.connection import connect
from content_factory.db.errors import IntegrityError
from content_factory.db.server_journal import ServerJournal
from content_factory.exceptions.generator import JournalError
from content_factory.schemas.generator.server import ServerConnection

_T0 = "2026-11-27T10:00:00+00:00"
_T1 = "2026-11-27T10:05:00+00:00"
_T2 = "2026-11-27T10:10:00+00:00"


@pytest.fixture
def connection(tmp_path: Path):
    """Open the migrated operational database of one test."""
    opened = connect(tmp_path / "db" / "db.sqlite3")
    try:
        yield opened
    finally:
        opened.close()


def _renting(connection, server_id: str = "s1", **kwargs: object) -> None:
    values = {
        "run_id": "run-1",
        "profile": "dating/profile_1",
        "gpu": "L40S",
        "image": "img",
        "cost_per_hour": 0.4,
        "now": _T0,
    }
    values.update(kwargs)
    generator_servers.insert_renting(connection, server_id=server_id, **values)  # type: ignore[arg-type]


def _row(connection, server_id: str = "s1") -> generator_servers.GeneratorServerRow:
    row = generator_servers.get_row(connection, server_id)
    assert row is not None
    return row


def _connection() -> ServerConnection:
    return ServerConnection(contract_id="c1", ip="1.2.3.4", port=40000, gpu="L40S", image="img")


def test_insert_records_a_renting_row(connection) -> None:
    _renting(connection)
    connection.commit()

    row = _row(connection)

    assert row.status == generator_servers.RENTING
    assert row.run_id == "run-1"
    assert row.profile == "dating/profile_1"
    assert row.gpu == "L40S"
    assert row.image == "img"
    assert row.cost_per_hour == 0.4
    assert row.contract_id is None
    assert row.created_at == row.updated_at == _T0
    assert row.destroyed_at is None


def test_bind_ready_and_destroyed_follow_the_lifecycle(connection) -> None:
    _renting(connection)
    generator_servers.bind_contract(connection, "s1", "c1", _T1)
    generator_servers.mark_ready(
        connection,
        "s1",
        ip="1.2.3.4",
        port=40000,
        gpu="L40S",
        image="img",
        cost_per_hour=0.4,
        now=_T1,
    )
    bind = _row(connection)
    assert bind.contract_id == "c1"
    assert bind.status == generator_servers.READY
    assert (bind.ip, bind.port, bind.gpu, bind.image) == ("1.2.3.4", 40000, "L40S", "img")

    generator_servers.mark_destroying(connection, "s1", _T2, "still there")
    destroying = _row(connection)
    assert destroying.status == generator_servers.DESTROYING
    assert destroying.last_error == "still there"

    generator_servers.mark_destroyed(connection, "s1", _T2)
    destroyed = _row(connection)
    assert destroyed.status == generator_servers.DESTROYED
    assert destroyed.destroyed_at == _T2
    assert destroyed.last_error is None


def test_lost_is_terminal_and_keeps_the_reason(connection) -> None:
    _renting(connection)
    generator_servers.mark_lost(connection, "s1", _T2, "instance vanished")
    lost = _row(connection)
    assert lost.status == generator_servers.LOST
    assert lost.last_error == "instance vanished"

    generator_servers.mark_destroyed(connection, "s1", _T2)
    generator_servers.mark_destroying(connection, "s1", _T2)
    assert _row(connection).status == generator_servers.LOST


def test_contract_id_is_unique_when_filled(connection) -> None:
    _renting(connection, "s1")
    _renting(connection, "s2")
    generator_servers.bind_contract(connection, "s2", "c1", _T1)

    with pytest.raises(IntegrityError):
        generator_servers.bind_contract(connection, "s1", "c1", _T1)


def test_two_rows_may_wait_without_a_contract(connection) -> None:
    _renting(connection, "s1")
    _renting(connection, "s2")

    assert {row.id for row in generator_servers.list_unfinished(connection)} == {"s1", "s2"}


def test_list_unfinished_skips_terminal_rows(connection) -> None:
    _renting(connection, "s1")
    _renting(connection, "s2")
    generator_servers.mark_lost(connection, "s1", _T2)
    generator_servers.mark_destroyed(connection, "s2", _T2)

    assert generator_servers.list_unfinished(connection) == []


def test_server_journal_writes_through_its_own_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    journal = ServerJournal(db_path)

    server_id = journal.start(
        run_id="run-1",
        profile="dating/profile_1",
        gpu="L40S",
        image="img",
        cost_per_hour=0.4,
    )
    journal.bind(server_id, "c1")
    journal.ready(server_id, _connection(), 0.4)
    journal.destroying(server_id)
    journal.destroyed(server_id)

    connection = connect(db_path)
    try:
        row = generator_servers.get_row(connection, server_id)
    finally:
        connection.close()
    assert row is not None
    assert row.status == generator_servers.DESTROYED
    assert row.contract_id == "c1"
    assert row.destroyed_at is not None
    assert row.run_id == "run-1"
    assert row.profile == "dating/profile_1"


def test_journal_translates_database_failures_to_the_domain_error(tmp_path: Path) -> None:
    journal = ServerJournal(tmp_path)

    with pytest.raises(JournalError, match="журнал серверов"):
        journal.start(
            run_id=None,
            profile=None,
            gpu=None,
            image=None,
            cost_per_hour=None,
        )
