"""Server rental, readiness checks and verified teardown on Vast.ai."""

from __future__ import annotations

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
        if not self._contracts:
            return
        provider = self._provider
        if provider is None:
            raise RentError("нет провайдера Vast.ai для уничтожения сервера")
        errors: list[str] = []
        for contract_id in sorted(self._contracts):
            try:
                destroy_verified(provider, contract_id)
            except RentError as ex:
                errors.append(str(ex))
                continue
            self._contracts.discard(contract_id)
            if self._contract_id == contract_id:
                self._contract_id = None
            logger.bind(contract_id=contract_id).info("Сервер уничтожен и проверен")
        if errors:
            raise RentError("; ".join(errors))

    def _rent(self, provider: VastProvider) -> ServerInfo:
        """Try offers of every verified pass until one server is ready."""
        used_offers: set[str] = set()
        used_ips: set[str] = set()
        last: Exception | None = None
        for verified in _VERIFIED_PASSES:
            for offer in _offers(provider, self._profile, self._settings, verified):
                offer_id = str(offer.get("id", ""))
                ip = str(offer.get("public_ipaddr") or "")
                if not offer_id or offer_id in used_offers or (ip and ip in used_ips):
                    continue
                used_offers.add(offer_id)
                if ip:
                    used_ips.add(ip)
                try:
                    return self._rent_offer(provider, offer)
                except _TeardownError:
                    raise
                except RentError as ex:
                    last = ex
                    logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                        "Оффер не подошёл — следующий"
                    )
        raise RentError(f"годного сервера Vast.ai нет: {last}")

    def _rent_offer(self, provider: VastProvider, offer: Mapping[str, Any]) -> ServerInfo:
        """Create one instance and wait for its ComfyUI."""
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
        self._contract_id = contract_id
        self._contracts.add(contract_id)
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
        self._contracts.discard(contract_id)
        if self._contract_id == contract_id:
            self._contract_id = None

    def _wait_ready(self, provider: VastProvider, contract_id: str) -> ServerConnection:
        """Wait for the public port, then for the ComfyUI health endpoint."""
        deadline = self._clock() + self._settings.server_ready_timeout
        while self._clock() < deadline:
            try:
                instance = provider.show_instance(contract_id)
            except RentError:
                instance = None
            if instance is not None:
                connection = connection_from_instance(contract_id, instance)
                if connection is not None:
                    return self._wait_health(connection)
            self._sleep(_POLL_SECONDS)
        raise RentError(
            f"хост не выдал IP/порт за {self._settings.server_ready_timeout} с — образ не скачался"
        )

    def _wait_health(self, connection: ServerConnection) -> ServerConnection:
        """Wait until ComfyUI answers ``/system_stats``."""
        deadline = self._clock() + self._settings.comfy_health_timeout
        while self._clock() < deadline:
            if _healthy(connection):
                return connection
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
