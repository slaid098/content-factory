"""Recovery pass over orphaned Vast.ai generator servers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from content_factory.db import generator_servers
from content_factory.exceptions.generator import RentError
from content_factory.schemas.generator.recovery import RecoveryOutcome
from content_factory.services.generator import GENERATOR_LABEL, VastProvider, destroy_verified

if TYPE_CHECKING:
    from content_factory.db.connection import Connection

GRACE_SECONDS = 10 * 60
"""Age after which an orphaned instance or row may be destroyed."""

TTL_SECONDS = 6 * 60 * 60
"""Age after which an unfinished row is considered stale for sure."""


def recover(
    connection: Connection,
    provider: VastProvider,
    *,
    now: float,
) -> RecoveryOutcome:
    """Destroy own orphaned instances and mark vanished journal rows lost.

    Only instances labelled ``content-factory-generator`` are ever touched.
    An instance without a journal row is destroyed after *GRACE_SECONDS*; a
    bound instance is destroyed when it or its row is older than the grace
    period, and rows older than *TTL_SECONDS* are destroyed in the same pass.
    A row whose contract id vanished from the account becomes ``lost``, as
    does a ``renting`` row that never received a contract id. A failed
    teardown keeps the row for the next tick.

    Args:
        connection: Open connection to the operational database.
        provider: Vast.ai client.
        now: Epoch seconds used as the current moment.

    Returns:
        Counters of the pass.

    Raises:
        RentError: The account instance list is unavailable.
        DatabaseError: A journal write fails.
    """
    rows = generator_servers.list_unfinished(connection)
    present = _instances(provider.list_instances())
    bound = {row.contract_id: row for row in rows if row.contract_id is not None}
    destroyed = 0
    failed = 0
    handled: set[str] = set()
    for contract_id, instance in present.items():
        if str(instance.get("label") or "") != GENERATOR_LABEL:
            continue
        row = bound.get(contract_id)
        started = _number(instance.get("start_date"))
        if row is None:
            if not _old_enough(started, now, "инстанс без строки журнала"):
                continue
            if _destroy_unbound(provider, contract_id):
                destroyed += 1
            else:
                failed += 1
            continue
        handled.add(row.id)
        if not _orphaned(row, started, now):
            continue
        if _destroy_bound(connection, provider, row, now):
            destroyed += 1
        else:
            failed += 1
    lost = _mark_lost(connection, rows, present, handled, now)
    return RecoveryOutcome(destroyed=destroyed, lost=lost, failed=failed)


def _instances(instances: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index account instances by contract id, skipping entries without one."""
    present: dict[str, dict[str, Any]] = {}
    for instance in instances:
        contract_id = str(instance.get("id") or "").strip()
        if contract_id:
            present[contract_id] = instance
    return present


def _orphaned(row: generator_servers.GeneratorServerRow, started: float | None, now: float) -> bool:
    """Return whether a bound instance is old enough to be an orphan."""
    row_age = _age(row.created_at, now)
    if row_age is not None and row_age >= TTL_SECONDS:
        return True
    instance_age = None if started is None or started <= 0 else now - started
    age = instance_age if instance_age is not None else row_age
    if age is None:
        logger.bind(contract_id=row.contract_id, row_id=row.id).warning(
            f"Восстановление: строка {row.id} без корректного времени — проверьте вручную"
        )
        return False
    return age >= GRACE_SECONDS


def _old_enough(started: float | None, now: float, what: str) -> bool:
    """Return whether an instance start date proves it outlived the grace."""
    if started is None or started <= 0:
        logger.bind(start_date=started).warning(
            f"Восстановление: {what} без корректного start_date ({started}) — проверьте вручную"
        )
        return False
    return now - started >= GRACE_SECONDS


def _destroy_unbound(provider: VastProvider, contract_id: str) -> bool:
    """Destroy an owned instance that has no journal row."""
    try:
        destroy_verified(provider, contract_id)
    except RentError as ex:
        logger.bind(contract_id=contract_id, error=str(ex)).error(
            f"Восстановление: инстанс {contract_id} без строки не уничтожился — "
            f"следующий тик повторит: {ex}"
        )
        return False
    logger.bind(contract_id=contract_id).info(
        f"Восстановление: осиротевший инстанс {contract_id} уничтожен"
    )
    return True


def _destroy_bound(
    connection: Connection,
    provider: VastProvider,
    row: generator_servers.GeneratorServerRow,
    now: float,
) -> bool:
    """Destroy the instance of one orphaned row and persist its outcome."""
    if row.contract_id is None:
        return False
    generator_servers.mark_destroying(connection, row.id, _iso(now))
    connection.commit()
    try:
        destroy_verified(provider, row.contract_id)
    except RentError as ex:
        generator_servers.mark_destroying(connection, row.id, _iso(now), str(ex))
        connection.commit()
        logger.bind(contract_id=row.contract_id, row_id=row.id, error=str(ex)).error(
            f"Восстановление: инстанс {row.contract_id} не уничтожился — "
            f"строка остаётся для следующего тика: {ex}"
        )
        return False
    generator_servers.mark_destroyed(connection, row.id, _iso(now))
    connection.commit()
    logger.bind(contract_id=row.contract_id, row_id=row.id).info(
        f"Восстановление: инстанс {row.contract_id} уничтожен, строка закрыта"
    )
    return True


def _mark_lost(
    connection: Connection,
    rows: list[generator_servers.GeneratorServerRow],
    present: Mapping[str, Mapping[str, Any]],
    handled: set[str],
    now: float,
) -> int:
    """Mark rows whose instance vanished, and old contract-less rentals, lost."""
    lost = 0
    for row in rows:
        if row.id in handled:
            continue
        if row.contract_id is None:
            if row.status != generator_servers.RENTING:
                continue
            age = _age(row.created_at, now)
            if age is None or age < GRACE_SECONDS:
                continue
            generator_servers.mark_lost(
                connection,
                row.id,
                _iso(now),
                "contract_id не появился за grace-период",
            )
            connection.commit()
            lost += 1
            logger.bind(row_id=row.id, run_id=row.run_id).warning(
                f"Восстановление: строка {row.id} без contract_id помечена lost"
            )
            continue
        if row.contract_id in present:
            continue
        generator_servers.mark_lost(
            connection,
            row.id,
            _iso(now),
            "инстанс исчез из аккаунта Vast.ai",
        )
        connection.commit()
        lost += 1
        logger.bind(row_id=row.id, contract_id=row.contract_id).warning(
            f"Восстановление: инстанс {row.contract_id} исчез — строка {row.id} помечена lost"
        )
    return lost


def _age(value: str | None, now: float) -> float | None:
    """Return the age in seconds of an ISO 8601 timestamp, or ``None``."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now - moment.timestamp()


def _iso(now: float) -> str:
    """Render epoch seconds as the ISO 8601 used by the journal."""
    return datetime.fromtimestamp(now, UTC).isoformat()


def _number(value: object) -> float | None:
    """Parse an optional number from the Vast.ai instance JSON."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


__all__ = [
    "GRACE_SECONDS",
    "TTL_SECONDS",
    "recover",
]
