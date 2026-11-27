"""Server rental, readiness checks and verified teardown on Vast.ai."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import JournalError, RentError
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator.profiles import _Profile
from content_factory.services.generator.vast_cli import (
    VastProvider,
    _already_gone,
    _VastFatal,
    _VastTransient,
)

if TYPE_CHECKING:
    from content_factory.schemas.generator.journal import ServerJournal

GENERATOR_LABEL = "content-factory-generator"
_LABEL = GENERATOR_LABEL
_COMFY_PORT = 8188
_RELIABILITY_MIN = 0.80
_CUDA_MIN = 12.6
_DESTROY_ATTEMPTS = 3
_DESTROY_PAUSE = 5.0
_POLL_SECONDS = 5.0
_POOL_TICK_SECONDS = 0.05
_GONE_GRACE_SECONDS = 60.0
HEALTH_TIMEOUT = 10.0
_VERIFIED_PASSES: tuple[bool | None, ...] = (True, False, None)


class _TeardownError(RentError):
    """A failed rental could not be destroyed; no further server may be rented."""


class _RentCancelled(RentError):
    """Another pool member won; the server is left for the main thread to destroy."""


@dataclass
class _PoolState:
    """Threads and outcomes shared by one floating rent pool."""

    stop: threading.Event = field(default_factory=threading.Event)
    outcomes: queue.Queue[ServerInfo | RentError] = field(default_factory=queue.Queue)
    workers: list[threading.Thread] = field(default_factory=list)


class VastSession:
    """One rented, ready ComfyUI server with a guaranteed teardown."""

    def __init__(
        self,
        profile: _Profile,
        settings: GeneratorSettings,
        *,
        provider: VastProvider | None = None,
        journal: ServerJournal,
        run_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the profile, journal and timings; no network call happens here."""
        self._profile = profile
        self._settings = settings
        self._provider = provider
        self._journal = journal
        self._run_id = run_id
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._contract_id: str | None = None
        self._contracts: set[str] = set()
        self._servers: dict[str, str] = {}
        self._info: ServerInfo | None = None

    def open(self) -> ServerInfo:
        """Clean stale instances, rent a server and wait for ComfyUI.

        Raises:
            RentError: No offer fits the filters, the balance is empty, the
                image or ComfyUI did not start in time, or the token is unset.
        """
        provider = self._provider or VastProvider(_token(self._settings))
        self._provider = provider
        reap_stale(provider, self._settings.stale_instance_hours)
        self._info = self._rent(provider)
        return self._info

    def destroy(self) -> None:
        """Destroy every rented contract and prove each one is gone.

        Raises:
            RentError: At least one instance is still listed after every
                attempt. Remaining contract ids stay tracked for a retry.
        """
        if not self._tracked():
            return
        provider = self._provider
        if provider is None:
            raise RentError("нет провайдера Vast.ai для уничтожения сервера")
        errors: list[str] = []
        for contract_id in sorted(self._tracked()):
            try:
                self._destroy_one(provider, contract_id)
            except RentError as ex:
                errors.append(str(ex))
                continue
            logger.bind(contract_id=contract_id).info(f"Сервер {contract_id} уничтожен и проверен")
        if errors:
            raise RentError("; ".join(errors))

    def _rent(self, provider: VastProvider) -> ServerInfo:
        """Race offers of every verified pass until one server is ready."""
        used_offers: set[str] = set()
        used_ips: set[str] = set()
        reasons: list[str] = []
        for verified in _VERIFIED_PASSES:
            try:
                if self._settings.rent_pool_size > 1:
                    return self._rent_pool(provider, verified, used_offers, used_ips)
                return self._rent_sequential(provider, verified, used_offers, used_ips)
            except _TeardownError:
                raise
            except _VastFatal:
                raise
            except RentError as ex:
                reasons.append(str(ex))
                logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                    f"Пул офферов не подошёл — следующие: {ex}"
                )
        detail = "; ".join(reasons) or "причины не записаны"
        raise RentError(f"годного сервера Vast.ai нет: {detail}")

    def _candidates(
        self,
        provider: VastProvider,
        verified: bool | None,
        used_offers: set[str],
        used_ips: set[str],
        *,
        limit: int | None = None,
    ) -> list[Mapping[str, Any]]:
        """Return the offers of one pass, cheapest first and never repeated.

        Only the returned offers are marked used, so offers beyond *limit*
        stay available to the next search of a floating pool.
        """
        candidates: list[Mapping[str, Any]] = []
        for offer in _offers(provider, self._profile, self._settings, verified):
            if limit is not None and len(candidates) >= limit:
                break
            offer_id = str(offer.get("id", ""))
            ip = str(offer.get("public_ipaddr") or "")
            if not offer_id or offer_id in used_offers or (ip and ip in used_ips):
                continue
            used_offers.add(offer_id)
            if ip:
                used_ips.add(ip)
            candidates.append(offer)
        return candidates

    def _rent_sequential(
        self,
        provider: VastProvider,
        verified: bool | None,
        used_offers: set[str],
        used_ips: set[str],
    ) -> ServerInfo:
        """Rent the offers of one pass one by one without extra searches."""
        candidates = self._candidates(provider, verified, used_offers, used_ips)
        last: RentError | None = None
        for offer in candidates:
            try:
                return self._rent_offer(provider, offer)
            except _TeardownError:
                raise
            except _VastFatal:
                raise
            except RentError as ex:
                last = ex
                logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                    f"Оффер не подошёл — следующий: {ex}"
                )
        raise last or RentError("рынок Vast.ai не дал офферов под фильтры профиля")

    def _rent_pool(
        self,
        provider: VastProvider,
        verified: bool | None,
        used_offers: set[str],
        used_ips: set[str],
    ) -> ServerInfo:
        """Keep a floating pool of one verified pass full until a server is ready.

        New offers are searched at most once per ``offer_refresh_seconds`` and
        fill free slots only. Every attempt waits its own
        ``server_ready_timeout`` from the moment it starts; the first ready
        server wins and every other member is destroyed before the pool returns.

        Raises:
            _TeardownError: A loser is still listed after ``destroy_verified``.
            RentError: The market ran dry before any member became ready.
        """
        state = _PoolState()
        try:
            try:
                result = self._pool_loop(provider, verified, used_offers, used_ips, state)
            finally:
                state.stop.set()
                for worker in state.workers:
                    worker.join()
        except Exception as error:
            try:
                self._destroy_losers(provider)
            except _TeardownError as teardown:
                raise teardown from error
            raise
        if isinstance(result, ServerInfo):
            self._destroy_losers(provider, result)
            self._set_current(result.connection.contract_id)
            return result
        if isinstance(result, _TeardownError):
            raise result
        self._destroy_losers(provider)
        raise result

    def _pool_loop(
        self,
        provider: VastProvider,
        verified: bool | None,
        used_offers: set[str],
        used_ips: set[str],
        state: _PoolState,
    ) -> ServerInfo | RentError:
        """Fill a floating pool from one verified pass until it wins or runs dry."""
        size = self._settings.rent_pool_size
        offers = self._candidates(provider, verified, used_offers, used_ips, limit=size)
        if not offers:
            return RentError("рынок Vast.ai не дал офферов под фильтры профиля")
        started = self._start_attempts(provider, offers, state)
        last_search = self._clock()
        last: RentError | None = None
        _log_pool(started, started)
        while True:
            result, last = self._pool_outcomes(state, last)
            if result is not None:
                return result
            now = self._clock()
            active = self._active(state)
            if state.stop.is_set() and active == 0:
                return self._final_result(state, last, "пул остановлен без готового сервера")
            if (
                active < size
                and not state.stop.is_set()
                and now >= last_search + self._settings.offer_refresh_seconds
            ):
                fresh = self._candidates(
                    provider, verified, used_offers, used_ips, limit=size - active
                )
                last_search = self._clock()
                result, last = self._pool_outcomes(state, last)
                if result is not None:
                    return result
                if state.stop.is_set():
                    continue
                added = self._start_attempts(provider, fresh, state)
                active = self._active(state)
                _log_pool(added, active)
                if not fresh and active == 0:
                    return self._final_result(state, last, "рынок Vast.ai не дал новых офферов")
                continue
            result, last = self._pool_sleep(state, last, active, now, last_search)
            if result is not None:
                return result

    def _pool_sleep(
        self,
        state: _PoolState,
        last: RentError | None,
        active: int,
        now: float,
        last_search: float,
    ) -> tuple[ServerInfo | _TeardownError | _VastFatal | None, RentError | None]:
        """Return a late outcome, otherwise sleep until the next pool check."""
        if active == 0 or state.stop.is_set():
            result, last = self._pool_outcomes(state, last)
            if result is not None:
                return result, last
        pause = _pool_pause(
            active,
            now,
            last_search,
            self._settings.offer_refresh_seconds,
        )
        if state.stop.is_set():
            pause = min(pause, _POOL_TICK_SECONDS)
        self._sleep(pause)
        return None, last

    def _active(self, state: _PoolState) -> int:
        """Return how many pool attempts are still running."""
        return sum(worker.is_alive() for worker in state.workers)

    def _pool_outcomes(
        self,
        state: _PoolState,
        last: RentError | None,
    ) -> tuple[ServerInfo | _TeardownError | _VastFatal | None, RentError | None]:
        """Drain ready outcomes; stop only for a winner, a fatal error or teardown."""
        while True:
            outcome = _poll_outcome(state.outcomes)
            if outcome is None:
                return None, last
            if isinstance(outcome, ServerInfo | _TeardownError | _VastFatal):
                return outcome, last
            last = _note_pool_failure(outcome)

    def _final_result(
        self, state: _PoolState, last: RentError | None, empty_error: str
    ) -> ServerInfo | RentError:
        """Read outcomes once more and return the last failure reason."""
        result, last = self._pool_outcomes(state, last)
        if result is not None:
            return result
        reason = last or RentError(empty_error)
        logger.bind(error=str(reason), error_type=type(reason).__name__).warning(
            f"Пул аренды завершён без готового сервера: {reason}"
        )
        return reason

    def _start_attempts(
        self,
        provider: VastProvider,
        offers: list[Mapping[str, Any]],
        state: _PoolState,
    ) -> int:
        """Start one worker per offer; stop early if the pool already ended."""
        started = 0
        for offer in offers:
            if state.stop.is_set():
                break
            worker = threading.Thread(
                target=self._attempt,
                args=(provider, offer, state.stop, state.outcomes),
                name=f"vast-rent-{offer.get('id')}",
                daemon=True,
            )
            worker.start()
            state.workers.append(worker)
            started += 1
        return started

    def _attempt(
        self,
        provider: VastProvider,
        offer: Mapping[str, Any],
        stop: threading.Event,
        outcomes: queue.Queue[ServerInfo | RentError],
    ) -> None:
        """Rent one pool candidate and report its ready server or failure."""
        if stop.is_set():
            return
        contract_id: str | None = None
        started = self._clock()
        timeout = self._settings.server_ready_timeout
        health_timeout = self._settings.comfy_health_timeout
        cost = _float_or_none(offer.get("dph_total"))
        try:
            contract_id = self._create(provider, offer)
            started = self._clock()
            connection = self._wait_ready(
                provider, contract_id, deadline=started + timeout, stop=stop
            )
            self._mark_ready(contract_id, connection, cost)
        except _RentCancelled:
            return
        except _VastFatal as ex:
            _log_attempt_failure(contract_id, timeout, health_timeout, self._clock() - started, ex)
            stop.set()
            self._failed_candidate(provider, contract_id, ex)
            outcomes.put(ex)
        except RentError as ex:
            _log_attempt_failure(contract_id, timeout, health_timeout, self._clock() - started, ex)
            outcomes.put(self._failed_candidate(provider, contract_id, ex))
        except Exception as ex:  # noqa: BLE001 — a provider bug must not stall the pool
            error = RentError(f"{type(ex).__name__}: {ex}")
            _log_attempt_failure(
                contract_id, timeout, health_timeout, self._clock() - started, error
            )
            outcomes.put(self._failed_candidate(provider, contract_id, error))
        else:
            stop.set()
            _log_ready(connection)
            outcomes.put(
                ServerInfo(
                    connection=connection,
                    cost_per_hour=cost,
                )
            )

    def _failed_candidate(
        self, provider: VastProvider, contract_id: str | None, error: RentError
    ) -> RentError:
        """Destroy a failed pool member and return the failure for the queue."""
        if contract_id is None:
            return error
        try:
            self._destroy_after_failure(provider, contract_id)
        except _TeardownError as teardown:
            return teardown
        return error

    def _destroy_losers(self, provider: VastProvider, winner: ServerInfo | None = None) -> None:
        """Destroy every tracked pool member except the ready *winner*.

        Raises:
            _TeardownError: A loser is still listed after ``destroy_verified``.
                It stays tracked for the final ``destroy()``.
        """
        remaining = self._tracked()
        if winner is not None:
            remaining.discard(winner.connection.contract_id)
        errors: list[str] = []
        for contract_id in sorted(remaining):
            try:
                self._destroy_one(provider, contract_id)
            except RentError as ex:
                errors.append(f"проигравший сервер {contract_id} не уничтожен: {ex}")
                continue
            logger.bind(contract_id=contract_id).info(f"Проигравший сервер {contract_id} уничтожен")
        if errors:
            raise _TeardownError("; ".join(errors))

    def _rent_offer(self, provider: VastProvider, offer: Mapping[str, Any]) -> ServerInfo:
        """Create one instance and wait for its ComfyUI."""
        contract_id = self._create(provider, offer)
        started = self._clock()
        port_timeout = self._settings.server_ready_timeout
        health_timeout = self._settings.comfy_health_timeout
        cost = _float_or_none(offer.get("dph_total"))
        self._set_current(contract_id)
        try:
            connection = self._wait_ready(provider, contract_id, deadline=started + port_timeout)
        except RentError as ex:
            _log_attempt_failure(
                contract_id, port_timeout, health_timeout, self._clock() - started, ex
            )
            self._destroy_after_failure(provider, contract_id)
            raise
        self._mark_ready(contract_id, connection, cost)
        _log_ready(connection)
        return ServerInfo(
            connection=connection,
            cost_per_hour=cost,
        )

    def _create(self, provider: VastProvider, offer: Mapping[str, Any]) -> str:
        """Journal and rent one offer, tracking its contract until teardown.

        The journal row is inserted before ``create_instance``; a failure to
        insert it stops the rental with no paid instance. The contract id is
        written immediately after the provider returns it.

        Raises:
            RentError: The offer has no id or ``create_instance`` failed.
        """
        offer_id = str(offer.get("id") or "").strip()
        if not offer_id:
            raise RentError("Vast.ai вернул оффер без id")
        cost = _float_or_none(offer.get("dph_total"))
        server_id = self._journal_start(offer_id, cost)
        contract_id = provider.create_instance(
            {
                "offer_id": offer_id,
                "image": self._profile.image,
                "disk": self._profile.rent.disk_space,
                "env": {"OPEN_BUTTON_TOKEN": "comfy"},
                "label": _LABEL,
                "onstart_cmd": "bash /start.sh",
            }
        )
        self._track(contract_id, server_id)
        self._journal_call(
            "привязка контракта",
            contract_id,
            lambda: self._journal.bind(server_id, contract_id),
        )
        return contract_id

    def _journal_start(self, offer_id: str, cost: float | None) -> str:
        """Insert the ``renting`` row before the provider creates an instance."""
        try:
            return self._journal.start(
                run_id=self._run_id,
                profile=self._profile.name,
                gpu=self._profile.gpu,
                image=self._profile.image,
                cost_per_hour=cost,
            )
        except JournalError as ex:
            raise RentError(f"журнал серверов недоступен, аренда {offer_id} отменена: {ex}") from ex

    def _journal_call(
        self,
        action: str,
        contract_id: str,
        call: Callable[[], None],
    ) -> None:
        """Run one journal update; a database failure must not leak a server."""
        try:
            call()
        except JournalError as ex:
            logger.bind(
                contract_id=contract_id,
                action=action,
                error=str(ex),
                error_type=type(ex).__name__,
            ).error(f"Журнал серверов: {action} для {contract_id} не записан: {ex}")

    def _mark_ready(
        self,
        contract_id: str,
        connection: ServerConnection,
        cost: float | None,
    ) -> None:
        """Store the ready address of a tracked contract in the journal."""
        server_id = self._server_id(contract_id)
        if server_id is None:
            return
        self._journal_call(
            "адрес сервера",
            contract_id,
            lambda: self._journal.ready(server_id, connection, cost),
        )

    def _mark_destroying(self, contract_id: str) -> None:
        """Mark a tracked contract as being destroyed in the journal."""
        server_id = self._server_id(contract_id)
        if server_id is None:
            return
        self._journal_call(
            "начало уничтожения",
            contract_id,
            lambda: self._journal.destroying(server_id),
        )

    def _mark_destroyed(self, contract_id: str) -> None:
        """Mark a tracked contract destroyed after the verified teardown."""
        server_id = self._server_id(contract_id)
        if server_id is None:
            return
        self._journal_call(
            "уничтожение",
            contract_id,
            lambda: self._journal.destroyed(server_id),
        )

    def _destroy_one(self, provider: VastProvider, contract_id: str) -> None:
        """Destroy one tracked contract and journal its verified teardown."""
        self._mark_destroying(contract_id)
        destroy_verified(provider, contract_id)
        self._mark_destroyed(contract_id)
        self._untrack(contract_id)

    def _track(self, contract_id: str, server_id: str) -> None:
        """Remember a rented contract and its journal row until teardown."""
        with self._lock:
            self._contracts.add(contract_id)
            self._servers[contract_id] = server_id

    def _untrack(self, contract_id: str) -> None:
        """Forget a destroyed contract and clear it as the current one."""
        with self._lock:
            self._contracts.discard(contract_id)
            self._servers.pop(contract_id, None)
            if self._contract_id == contract_id:
                self._contract_id = None

    def _set_current(self, contract_id: str) -> None:
        """Mark the contract the session currently uses."""
        with self._lock:
            self._contract_id = contract_id

    def _tracked(self) -> set[str]:
        """Return a snapshot of the contracts that may still be alive."""
        with self._lock:
            return set(self._contracts)

    def _server_id(self, contract_id: str) -> str | None:
        """Return the journal row of a tracked contract."""
        with self._lock:
            return self._servers.get(contract_id)

    def _destroy_after_failure(self, provider: VastProvider, contract_id: str) -> None:
        """Destroy a failed rental; an unverified teardown stops the rental loop.

        The contract id stays tracked until ``destroy_verified`` succeeds, so
        ``destroy()`` from the service ``finally`` retries the same instance.

        Raises:
            _TeardownError: The instance is still listed after every attempt.
        """
        try:
            self._destroy_one(provider, contract_id)
        except RentError as ex:
            logger.bind(contract_id=contract_id, error=str(ex)).error(
                f"Сервер {contract_id} не уничтожился после сбоя аренды — "
                f"повтор в финальном finally: {ex}"
            )
            raise _TeardownError(
                f"сервер {contract_id} не уничтожен после сбоя аренды: {ex}"
            ) from ex

    def _wait_ready(
        self,
        provider: VastProvider,
        contract_id: str,
        *,
        deadline: float | None = None,
        stop: threading.Event | None = None,
    ) -> ServerConnection:
        """Wait for the public port, then for the ComfyUI health endpoint.

        Args:
            provider: Vast.ai CLI provider.
            contract_id: Rented instance.
            deadline: Deadline of this attempt; defaults to one ready timeout.
            stop: Set when another pool member won; the wait is abandoned and
                the contract stays tracked for the main thread to destroy.

        Raises:
            _VastFatal: The token or the account itself is unusable; retrying
                another offer cannot help.
            RentError: The host did not publish a port in time, or an
                instance already seen in the account was reported missing
                past the gone grace.
            _RentCancelled: Another pool member won first.
        """
        if deadline is None:
            deadline = self._clock() + self._settings.server_ready_timeout
        seen = False
        gone_since: float | None = None
        gone_error: RentError | None = None
        while self._clock() < deadline:
            _raise_if_cancelled(stop)
            try:
                instance = provider.show_instance(contract_id)
            except _VastFatal:
                raise
            except _VastTransient:
                instance = None
            except RentError as ex:
                instance = None
                if _already_gone(str(ex)) and str(contract_id) in str(ex):
                    now = self._clock()
                    gone_error = ex
                    if seen:
                        if gone_since is None:
                            gone_since = now
                        if now - gone_since >= _GONE_GRACE_SECONDS:
                            raise RentError(
                                f"инстанс {contract_id} исчез из аккаунта: {gone_error}"
                            ) from ex
            else:
                seen = True
                gone_since = None
                gone_error = None
            if instance is not None:
                connection = connection_from_instance(contract_id, instance)
                if connection is not None:
                    return self._wait_health(connection, stop=stop)
            _raise_if_cancelled(stop)
            self._sleep(_POLL_SECONDS)
        raise RentError(
            f"хост не выдал IP/порт за {self._settings.server_ready_timeout} с — образ не скачался"
        )

    def _wait_health(
        self, connection: ServerConnection, *, stop: threading.Event | None = None
    ) -> ServerConnection:
        """Wait until ComfyUI answers ``/system_stats``."""
        deadline = self._clock() + self._settings.comfy_health_timeout
        while self._clock() < deadline:
            _raise_if_cancelled(stop)
            if _healthy(connection):
                return connection
            _raise_if_cancelled(stop)
            self._sleep(_POLL_SECONDS)
        raise RentError(
            f"ComfyUI не ожил за {self._settings.comfy_health_timeout} с — сервер дохлый"
        )


def connection_from_instance(
    contract_id: str, instance: Mapping[str, Any]
) -> ServerConnection | None:
    """Build the ComfyUI connection from one instance JSON, if the port is open."""
    ip = instance.get("public_ipaddr")
    ports = instance.get("ports")
    entries = ports.get(f"{_COMFY_PORT}/tcp", []) if isinstance(ports, Mapping) else []
    port = entries[0].get("HostPort") if isinstance(entries, list) and entries else None
    if not ip or not port:
        return None
    try:
        port_number = int(str(port))
    except ValueError:
        return None
    return ServerConnection(
        contract_id=str(contract_id),
        ip=str(ip),
        port=port_number,
        gpu=str(instance.get("gpu_name") or "Unknown"),
        image=str(instance.get("image_uuid") or instance.get("image") or ""),
    )


def reap_stale(
    provider: VastProvider,
    hours: int,
    *,
    now: float | None = None,
) -> list[str]:
    """Destroy own generator instances older than *hours*.

    Instances of other labels are never touched. This runs before every new
    rental, so a process killed with SIGKILL does not leave a paid server.

    Args:
        provider: Vast.ai CLI provider.
        hours: Age threshold in hours.
        now: Epoch seconds used as "now"; tests pass a fixed value.

    Returns:
        Contract ids of destroyed stale instances.
    """
    moment = time.time() if now is None else now
    limit = max(1, hours) * 3600
    killed: list[str] = []
    try:
        instances = provider.list_instances()
    except RentError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
            f"Список инстансов недоступен — stale-чистка пропущена: {ex}"
        )
        return killed
    for instance in instances:
        if str(instance.get("label") or "") != _LABEL:
            continue
        started = _float_or_none(instance.get("start_date"))
        contract_id = str(instance.get("id") or "")
        if not contract_id:
            continue
        if started is None or started <= 0:
            logger.bind(contract_id=contract_id, start_date=started).warning(
                f"Stale-чистка: инстанс {contract_id} пропущен — нет корректного "
                f"start_date ({started}), проверьте вручную"
            )
            continue
        if moment - started < limit:
            continue
        try:
            destroy_verified(provider, contract_id)
        except RentError as ex:
            logger.bind(contract_id=contract_id, error=str(ex)).error(
                f"Stale-инстанс {contract_id} не уничтожился — погасите вручную: {ex}"
            )
            continue
        killed.append(contract_id)
    if killed:
        logger.bind(killed=killed).info(
            f"Stale-инстансы генератора уничтожены: {', '.join(killed)}"
        )
    return killed


def destroy_verified(
    provider: VastProvider,
    contract_id: str,
    *,
    attempts: int = _DESTROY_ATTEMPTS,
    pause: float = _DESTROY_PAUSE,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Destroy an instance and confirm it disappeared from the account list.

    Args:
        provider: Vast.ai CLI provider.
        contract_id: Instance to destroy.
        attempts: Rounds of destroy plus list verification.
        pause: Base pause between rounds, growing with the round number.
        sleep: Pause function; tests replace it to stay fast.

    Returns:
        ``True`` once the instance is absent from the account list.

    Raises:
        RentError: The instance is still listed after every attempt. The
            caller can only destroy it manually then.
    """
    for attempt in range(1, max(1, attempts) + 1):
        try:
            provider.destroy_instance(contract_id)
        except RentError as ex:
            logger.bind(contract_id=contract_id, attempt=attempt).warning(
                f"Уничтожение {contract_id} вернуло ошибку: {ex}"
            )
        try:
            provider.show_instance(contract_id)
        except RentError as ex:
            if str(contract_id) in str(ex) and "not found" in str(ex).lower():
                return True
            logger.bind(contract_id=contract_id, attempt=attempt).warning(
                f"Список инстансов недоступен при уничтожении {contract_id}: {ex}"
            )
        else:
            logger.bind(contract_id=contract_id, attempt=attempt).error(
                f"Сервер {contract_id} всё ещё в списке — повторяем уничтожение"
            )
        if attempt < attempts:
            sleep(pause * attempt)
    raise RentError(
        f"сервер {contract_id} не уничтожен за {attempts} попыток — погасите вручную в кабинете"
    )


def _poll_outcome(
    outcomes: queue.Queue[ServerInfo | RentError],
) -> ServerInfo | RentError | None:
    """Return the next pool outcome without blocking."""
    try:
        return outcomes.get_nowait()
    except queue.Empty:
        return None


def _pool_pause(
    active: int,
    now: float,
    last_search: float,
    refresh: int,
) -> float:
    """Return how long the coordinator naps before its next pool check."""
    if active:
        return _POOL_TICK_SECONDS
    return max(0.0, last_search + refresh - now)


def _log_attempt_failure(
    contract_id: str | None,
    port_timeout: int,
    health_timeout: int,
    elapsed: float,
    error: RentError,
) -> None:
    """Log a failed attempt with its contract, phase budgets and reason."""
    contract = contract_id or "без контракта"
    logger.bind(contract_id=contract_id, error=str(error), error_type=type(error).__name__).warning(
        f"Попытка аренды {contract} не дала сервер за {elapsed:.0f} с: "
        f"ожидание порта до {port_timeout} с, health до {health_timeout} с; {error}"
    )


def _log_ready(connection: ServerConnection) -> None:
    """Log a ready ComfyUI server by contract and address."""
    logger.bind(
        contract_id=connection.contract_id,
        gpu=connection.gpu,
        ip=connection.ip,
    ).info(f"Сервер ComfyUI готов: контракт {connection.contract_id}, {connection.ip}")


def _log_pool(added: int, active: int) -> None:
    """Log new offers of a pool search and the attempts now running."""
    logger.bind(added=added, active=active).info(f"Пул аренды: +{added} офферов, активных {active}")


def _note_pool_failure(outcome: RentError) -> RentError:
    """Log one failed pool candidate and return its error for the pass."""
    logger.bind(error=str(outcome), error_type=type(outcome).__name__).warning(
        f"Кандидат пула не подошёл — ждём остальных: {outcome}"
    )
    return outcome


def _raise_if_cancelled(stop: threading.Event | None) -> None:
    """Abandon a wait once another pool member has won.

    Raises:
        _RentCancelled: The pool already has a ready server.
    """
    if stop is not None and stop.is_set():
        raise _RentCancelled("пул уже получил готовый сервер")


def _offers(
    provider: VastProvider,
    profile: _Profile,
    settings: GeneratorSettings,
    verified: bool | None,
) -> list[dict[str, Any]]:
    """Search offers of one verified pass within the price ceiling."""
    ceiling = profile.max_cost
    if settings.max_cost_per_hour is not None:
        ceiling = min(ceiling, settings.max_cost_per_hour)
    query: dict[str, Any] = {
        "limit": 20,
        "order": [["dph_total", "asc"]],
        "type": "bid",
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        "gpu_name": {"in": [profile.gpu]},
        "gpu_ram": {"gte": profile.rent.gpu_ram},
        "inet_down": {"gte": profile.rent.inet_down},
        "reliability2": {"gte": _RELIABILITY_MIN},
        "disk_space": {"gte": profile.rent.disk_space},
        "inet_up_cost": {"lt": profile.rent.inet_up_cost},
        "inet_down_cost": {"lt": profile.rent.inet_down_cost},
        "dph_total": {"lte": ceiling},
        "cuda_max_good": {"gte": _CUDA_MIN},
    }
    if verified is not None:
        query["verified"] = {"eq": verified}
    try:
        return provider.search_offers(query)
    except _VastTransient as ex:
        logger.bind(error=str(ex)).warning(f"Поиск офферов временно недоступен: {ex}")
        return []


def _healthy(connection: ServerConnection) -> bool:
    """Return whether ComfyUI answers its health endpoint."""
    try:
        response = httpx.get(
            f"{connection.url()}/system_stats",
            timeout=httpx.Timeout(HEALTH_TIMEOUT),
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


def _token(settings: GeneratorSettings) -> str:
    """Return the Vast.ai token value without logging it."""
    return settings.vast_api_token.get_secret_value() if settings.vast_api_token else ""


def _float_or_none(value: object) -> float | None:
    """Parse an optional number."""
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None
