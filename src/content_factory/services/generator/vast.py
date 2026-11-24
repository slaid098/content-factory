"""Server rental, readiness checks and verified teardown on Vast.ai."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import RentError
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator.profiles import _Profile
from content_factory.services.generator.vast_cli import VastProvider, _VastTransient

_LABEL = "content-factory-generator"
_COMFY_PORT = 8188
_RELIABILITY_MIN = 0.80
_CUDA_MIN = 12.6
_DESTROY_ATTEMPTS = 3
_DESTROY_PAUSE = 5.0
_POLL_SECONDS = 5.0
HEALTH_TIMEOUT = 10.0
_VERIFIED_PASSES: tuple[bool | None, ...] = (True, False, None)


class _TeardownError(RentError):
    """A failed rental could not be destroyed; no further server may be rented."""


class _RentCancelled(RentError):
    """Another pool member won; the server is left for the main thread to destroy."""


class VastSession:
    """One rented, ready ComfyUI server with a guaranteed teardown."""

    def __init__(
        self,
        profile: _Profile,
        settings: GeneratorSettings,
        *,
        provider: VastProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the profile and timings; no network call happens here."""
        self._profile = profile
        self._settings = settings
        self._provider = provider
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._contract_id: str | None = None
        self._contracts: set[str] = set()
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
                destroy_verified(provider, contract_id)
            except RentError as ex:
                errors.append(str(ex))
                continue
            self._untrack(contract_id)
            logger.bind(contract_id=contract_id).info("Сервер уничтожен и проверен")
        if errors:
            raise RentError("; ".join(errors))

    def _rent(self, provider: VastProvider) -> ServerInfo:
        """Race offers of every verified pass until one server is ready."""
        used_offers: set[str] = set()
        used_ips: set[str] = set()
        last: Exception | None = None
        for verified in _VERIFIED_PASSES:
            candidates = self._candidates(provider, verified, used_offers, used_ips)
            for batch in _batches(candidates, self._settings.rent_pool_size):
                try:
                    return self._rent_batch(provider, batch)
                except _TeardownError:
                    raise
                except RentError as ex:
                    last = ex
                    logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                        "Пул офферов не подошёл — следующие"
                    )
        raise RentError(f"годного сервера Vast.ai нет: {last}")

    def _candidates(
        self,
        provider: VastProvider,
        verified: bool | None,
        used_offers: set[str],
        used_ips: set[str],
    ) -> list[Mapping[str, Any]]:
        """Return the offers of one pass, cheapest first and never repeated."""
        candidates: list[Mapping[str, Any]] = []
        for offer in _offers(provider, self._profile, self._settings, verified):
            offer_id = str(offer.get("id", ""))
            ip = str(offer.get("public_ipaddr") or "")
            if not offer_id or offer_id in used_offers or (ip and ip in used_ips):
                continue
            used_offers.add(offer_id)
            if ip:
                used_ips.add(ip)
            candidates.append(offer)
        return candidates

    def _rent_batch(self, provider: VastProvider, offers: list[Mapping[str, Any]]) -> ServerInfo:
        """Rent one pool; an offer without company keeps the sequential path."""
        if len(offers) == 1:
            return self._rent_offer(provider, offers[0])
        return self._race(provider, offers)

    def _race(self, provider: VastProvider, offers: list[Mapping[str, Any]]) -> ServerInfo:
        """Rent the whole pool and return the first server that answers.

        Every member shares one ``server_ready_timeout`` deadline. Losers are
        destroyed only after their threads stop, so the winner stays the sole
        tracked contract when the rental returns.

        Raises:
            _TeardownError: A loser is still listed after ``destroy_verified``.
                It stays tracked for the final ``destroy()`` and the rental
                stops, so no new paid server appears next to an orphan.
        """
        deadline = self._clock() + self._settings.server_ready_timeout
        stop = threading.Event()
        outcomes: queue.Queue[ServerInfo | RentError] = queue.Queue()
        workers = [
            threading.Thread(
                target=self._attempt,
                args=(provider, offer, deadline, stop, outcomes),
                name=f"vast-rent-{offer.get('id')}",
                daemon=True,
            )
            for offer in offers
        ]
        for worker in workers:
            worker.start()
        try:
            winner, failure = self._collect(outcomes, len(workers))
        finally:
            stop.set()
            for worker in workers:
                worker.join()
        if winner is None:
            raise failure or RentError("пул аренды не дал готового сервера")
        self._destroy_losers(provider, winner)
        self._set_current(winner.connection.contract_id)
        return winner

    def _collect(
        self, outcomes: queue.Queue[ServerInfo | RentError], size: int
    ) -> tuple[ServerInfo | None, RentError | None]:
        """Wait for the first ready server or for every candidate to fail."""
        last: RentError | None = None
        for _ in range(size):
            outcome = outcomes.get()
            if isinstance(outcome, ServerInfo):
                return outcome, None
            last = outcome
            if isinstance(outcome, _TeardownError):
                return None, outcome
            logger.bind(error=str(outcome), error_type=type(outcome).__name__).warning(
                "Кандидат пула не подошёл — ждём остальных"
            )
        return None, last

    def _attempt(
        self,
        provider: VastProvider,
        offer: Mapping[str, Any],
        deadline: float,
        stop: threading.Event,
        outcomes: queue.Queue[ServerInfo | RentError],
    ) -> None:
        """Rent one pool candidate and report its ready server or failure."""
        contract_id: str | None = None
        try:
            contract_id = self._create(provider, offer)
            connection = self._wait_ready(provider, contract_id, deadline=deadline, stop=stop)
        except _RentCancelled:
            return
        except RentError as ex:
            outcomes.put(self._failed_candidate(provider, contract_id, ex))
        except Exception as ex:  # noqa: BLE001 — a provider bug must not stall the pool
            outcomes.put(
                self._failed_candidate(
                    provider,
                    contract_id,
                    RentError(f"{type(ex).__name__}: {ex}"),
                )
            )
        else:
            logger.bind(contract_id=contract_id, gpu=connection.gpu, ip=connection.ip).info(
                "Сервер ComfyUI готов"
            )
            outcomes.put(
                ServerInfo(
                    connection=connection,
                    cost_per_hour=_float_or_none(offer.get("dph_total")),
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

    def _destroy_losers(self, provider: VastProvider, winner: ServerInfo) -> None:
        """Destroy every tracked pool member except the winner.

        Raises:
            _TeardownError: A loser is still listed after ``destroy_verified``.
                It stays tracked for the final ``destroy()``.
        """
        keep = winner.connection.contract_id
        errors: list[str] = []
        for contract_id in sorted(self._tracked() - {keep}):
            try:
                destroy_verified(provider, contract_id)
            except RentError as ex:
                errors.append(f"проигравший сервер {contract_id} не уничтожен: {ex}")
                continue
            self._untrack(contract_id)
            logger.bind(contract_id=contract_id).info("Проигравший сервер уничтожен")
        if errors:
            raise _TeardownError("; ".join(errors))

    def _rent_offer(self, provider: VastProvider, offer: Mapping[str, Any]) -> ServerInfo:
        """Create one instance and wait for its ComfyUI."""
        contract_id = self._create(provider, offer)
        self._set_current(contract_id)
        try:
            connection = self._wait_ready(provider, contract_id)
        except RentError:
            self._destroy_after_failure(provider, contract_id)
            raise
        logger.bind(contract_id=contract_id, gpu=connection.gpu, ip=connection.ip).info(
            "Сервер ComfyUI готов"
        )
        return ServerInfo(
            connection=connection,
            cost_per_hour=_float_or_none(offer.get("dph_total")),
        )

    def _create(self, provider: VastProvider, offer: Mapping[str, Any]) -> str:
        """Rent one offer and track its contract until a verified teardown."""
        offer_id = str(offer.get("id") or "").strip()
        if not offer_id:
            raise RentError("Vast.ai вернул оффер без id")
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
        self._track(contract_id)
        return contract_id

    def _track(self, contract_id: str) -> None:
        """Remember a rented contract until its verified teardown."""
        with self._lock:
            self._contracts.add(contract_id)

    def _untrack(self, contract_id: str) -> None:
        """Forget a destroyed contract and clear it as the current one."""
        with self._lock:
            self._contracts.discard(contract_id)
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

    def _destroy_after_failure(self, provider: VastProvider, contract_id: str) -> None:
        """Destroy a failed rental; an unverified teardown stops the rental loop.

        The contract id stays tracked until ``destroy_verified`` succeeds, so
        ``destroy()`` from the service ``finally`` retries the same instance.

        Raises:
            _TeardownError: The instance is still listed after every attempt.
        """
        try:
            destroy_verified(provider, contract_id)
        except RentError as ex:
            logger.bind(contract_id=contract_id, error=str(ex)).error(
                "Сервер не уничтожился после сбоя аренды — повтор в финальном finally"
            )
            raise _TeardownError(
                f"сервер {contract_id} не уничтожен после сбоя аренды: {ex}"
            ) from ex
        self._untrack(contract_id)

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
            deadline: Shared pool deadline; defaults to one ready timeout.
            stop: Set when another pool member won; the wait is abandoned and
                the contract stays tracked for the main thread to destroy.

        Raises:
            RentError: The host did not publish a port in time.
            _RentCancelled: Another pool member won first.
        """
        if deadline is None:
            deadline = self._clock() + self._settings.server_ready_timeout
        while self._clock() < deadline:
            _raise_if_cancelled(stop)
            try:
                instance = provider.show_instance(contract_id)
            except RentError:
                instance = None
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
            "Список инстансов недоступен — stale-чистка пропущена"
        )
        return killed
    for instance in instances:
        if str(instance.get("label") or "") != _LABEL:
            continue
        started = _float_or_none(instance.get("start_date"))
        contract_id = str(instance.get("id") or "")
        if not contract_id or started is None or moment - started < limit:
            continue
        try:
            destroy_verified(provider, contract_id)
        except RentError as ex:
            logger.bind(contract_id=contract_id, error=str(ex)).error(
                "Stale-инстанс не уничтожился — погасите вручную"
            )
            continue
        killed.append(contract_id)
    if killed:
        logger.bind(killed=killed).info("Stale-инстансы генератора уничтожены")
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
                f"Уничтожение вернуло ошибку: {ex}"
            )
        try:
            provider.show_instance(contract_id)
        except RentError as ex:
            if str(contract_id) in str(ex) and "not found" in str(ex).lower():
                return True
            logger.bind(contract_id=contract_id, attempt=attempt).warning(
                f"Список инстансов недоступен: {ex}"
            )
        else:
            logger.bind(contract_id=contract_id, attempt=attempt).error(
                "Сервер всё ещё в списке — повторяем уничтожение"
            )
        if attempt < attempts:
            sleep(pause * attempt)
    raise RentError(
        f"сервер {contract_id} не уничтожен за {attempts} попыток — погасите вручную в кабинете"
    )


def _batches(offers: list[Mapping[str, Any]], size: int) -> list[list[Mapping[str, Any]]]:
    """Split offers into pools of at most *size* candidates, cheapest first."""
    step = max(1, size)
    return [offers[start : start + step] for start in range(0, len(offers), step)]


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
        logger.bind(error=str(ex)).warning("Поиск офферов временно недоступен")
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
