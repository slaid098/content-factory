"""Vast.ai session: rental passes, readiness, teardown and stale cleanup."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import RentError
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator import vast
from content_factory.services.generator.vast import (
    VastSession,
    _float_or_none,
    _healthy,
    _offers,
    connection_from_instance,
    destroy_verified,
    reap_stale,
)
from content_factory.services.generator.vast_cli import _VastTransient


def _settings(tmp_path: Path, **kwargs: Any) -> GeneratorSettings:
    values: dict[str, Any] = {
        "output_dir": tmp_path / "generated",
        "vast_api_token": "token",
        "server_ready_timeout": 60,
        "comfy_health_timeout": 10,
        # Existing cases describe the one-server rental; pool tests raise it.
        "rent_pool_size": 1,
    }
    values.update(kwargs)
    return GeneratorSettings(**values)


def _connection() -> ServerConnection:
    return ServerConnection(contract_id="1", ip="1.2.3.4", port=8188, gpu="L40S", image="img")


def _instance(port: str = "40000") -> dict[str, Any]:
    return {
        "id": "c1",
        "public_ipaddr": "1.2.3.4",
        "ports": {"8188/tcp": [{"HostPort": port}]},
        "gpu_name": "L40S",
        "image_uuid": "img",
        "label": "content-factory-generator",
    }


class _Provider:
    """In-memory Vast.ai account with controllable calls."""

    def __init__(
        self,
        offers: list[dict[str, Any]] | None = None,
        instances: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.offers = offers or []
        self.instances = instances or {}
        self.queries: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.destroyed: list[str] = []
        self.fail_list = False
        self.destroy_error: Exception | None = None

    def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(dict(query))
        return [dict(offer) for offer in self.offers]

    def create_instance(self, config: dict[str, Any]) -> str:
        self.created.append(dict(config))
        return f"c{len(self.created)}"

    def show_instance(self, contract_id: str) -> dict[str, Any]:
        instance = self.instances.get(str(contract_id))
        if instance is None:
            raise RentError(f"Instance {contract_id} not found")
        return dict(instance)

    def list_instances(self) -> list[dict[str, Any]]:
        if self.fail_list:
            raise RentError("list is down")
        return [dict(item) for item in self.instances.values()]

    def destroy_instance(self, contract_id: str) -> bool:
        if self.destroy_error is not None:
            raise self.destroy_error
        self.destroyed.append(str(contract_id))
        self.instances.pop(str(contract_id), None)
        return True


def _clock_pair() -> tuple[list[float], Any, Any]:
    state = [0.0]

    def clock() -> float:
        return state[0]

    def sleep(seconds: float) -> None:
        state[0] += seconds

    return state, clock, sleep


class _PoolProvider(_Provider):
    """Rents one contract per offer; only *ready* publishes the ComfyUI port."""

    def __init__(self, offers: list[dict[str, Any]], ready: str, broken: str = "") -> None:
        super().__init__(offers=offers)
        self.ready = ready
        self.broken = broken

    def create_instance(self, config: dict[str, Any]) -> str:
        offer_id = str(config["offer_id"])
        if offer_id == self.broken:
            raise RentError(f"offer {offer_id} is gone")
        self.created.append(dict(config))
        contract_id = f"c{offer_id}"
        ports = {"8188/tcp": [{"HostPort": "40000"}]} if offer_id == self.ready else {}
        self.instances[contract_id] = {
            "id": contract_id,
            "public_ipaddr": f"10.0.0.{offer_id}",
            "ports": ports,
            "gpu_name": "L40S",
            "image_uuid": "img",
        }
        return contract_id


def _fast_poll(monkeypatch) -> None:
    """Poll often in real time so a stopped loser leaves the race quickly."""
    monkeypatch.setattr(vast, "_POLL_SECONDS", 0.001)


class _RefillProvider(_Provider):
    """Publishes prepared offers per search; only *ready* gets a ComfyUI port."""

    def __init__(self, searches: list[list[str]], ready: str = "", broken: str = "") -> None:
        super().__init__()
        self.searches = searches
        self.ready = ready
        self.broken = broken
        self.max_alive = 0

    def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        self.queries.append(dict(query))
        index = min(len(self.queries), len(self.searches)) - 1
        return [{"id": offer_id} for offer_id in self.searches[index]]

    def create_instance(self, config: dict[str, Any]) -> str:
        offer_id = str(config["offer_id"])
        if offer_id == self.broken:
            raise RentError(f"offer {offer_id} is gone")
        self.created.append(dict(config))
        contract_id = f"c{offer_id}"
        ports = {"8188/tcp": [{"HostPort": "40000"}]} if offer_id == self.ready else {}
        self.instances[contract_id] = {
            "id": contract_id,
            "public_ipaddr": f"10.0.0.{offer_id}",
            "ports": ports,
            "gpu_name": "L40S",
            "image_uuid": "img",
        }
        self.max_alive = max(self.max_alive, len(self.instances))
        return contract_id


def _refill_clock(monkeypatch) -> tuple[list[float], Any, Any]:
    """Clock where the pool tick moves a second and a host poll a millisecond."""
    monkeypatch.setattr(vast, "_POLL_SECONDS", 0.001)
    monkeypatch.setattr(vast, "_POOL_TICK_SECONDS", 1.0)
    state = [0.0]
    lock = threading.Lock()
    yield_to_threads = threading.Event()

    def clock() -> float:
        return state[0]

    def sleep(seconds: float) -> None:
        with lock:
            state[0] += seconds
        yield_to_threads.wait(0.001)

    return state, clock, sleep


def _publish_ready_when_all_created(provider: _PoolProvider, ready_offer: str) -> None:
    """Delay the winner's port until every candidate of the batch has rented."""
    create = provider.create_instance

    def delayed_create(config: dict[str, Any]) -> str:
        contract_id = create(config)
        if len(provider.created) == len(provider.offers):
            provider.instances[f"c{ready_offer}"]["ports"] = {"8188/tcp": [{"HostPort": "40000"}]}
        return contract_id

    provider.create_instance = delayed_create  # type: ignore[method-assign]


def test_open_rents_ready_server_and_reports_cost(tmp_path: Path, monkeypatch) -> None:
    provider = _Provider(
        offers=[{"id": "1", "public_ipaddr": "1.2.3.4", "dph_total": 0.4}],
        instances={"c1": _instance()},
    )
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    profile = fixture_profile(tmp_path / "profiles")
    session = VastSession(profile, _settings(tmp_path), provider=provider)

    info = session.open()

    assert info.connection.url() == "http://1.2.3.4:40000"
    assert info.cost_per_hour == 0.4
    assert provider.created[0]["label"] == "content-factory-generator"
    assert provider.created[0]["onstart_cmd"] == "bash /start.sh"
    assert provider.queries[0]["verified"] == {"eq": True}


def test_open_without_offers_reports_no_server(tmp_path: Path, monkeypatch) -> None:
    provider = _Provider(offers=[])
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"), _settings(tmp_path), provider=provider
    )

    with pytest.raises(RentError, match="годного сервера"):
        session.open()


def test_rent_skips_duplicate_offers_and_ips(tmp_path: Path) -> None:
    provider = _Provider(
        offers=[
            {"id": "1", "public_ipaddr": "1.2.3.4"},
            {"id": "1", "public_ipaddr": "5.6.7.8"},
            {"id": "2", "public_ipaddr": "1.2.3.4"},
            {"id": "3"},
        ]
    )
    session = VastSession(
        fixture_profile(tmp_path / "profiles"), _settings(tmp_path), provider=provider
    )
    calls: list[str] = []

    def rent_offer(provider: _Provider, offer: dict[str, Any]) -> ServerInfo:
        calls.append(str(offer["id"]))
        if offer["id"] == "3":
            return ServerInfo(connection=_connection(), cost_per_hour=None)
        raise RentError("bad offer")

    session._rent_offer = rent_offer  # type: ignore[method-assign]

    info = session._rent(provider)

    assert calls == ["1", "3"]
    assert info.connection.contract_id == "1"


def test_rent_offer_without_id_is_an_error(tmp_path: Path) -> None:
    session = VastSession(fixture_profile(tmp_path / "profiles"), _settings(tmp_path))

    with pytest.raises(RentError, match="без id"):
        session._rent_offer(_Provider(), {})


def test_wait_ready_timeout_destroys_the_failed_server(tmp_path: Path, monkeypatch) -> None:
    provider = _Provider(offers=[{"id": "1"}], instances={})
    profile = fixture_profile(tmp_path / "profiles")
    _, clock, sleep = _clock_pair()
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        profile,
        _settings(tmp_path),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="годного сервера"):
        session.open()

    assert provider.destroyed == ["c1"]
    assert session._contract_id is None


def test_wait_health_retries_until_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(vast, "_healthy", lambda connection: False)
    _, clock, sleep = _clock_pair()
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path),
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="ComfyUI не ожил"):
        session._wait_health(_connection())


def test_wait_health_accepts_a_live_server(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(fixture_profile(tmp_path / "profiles"), _settings(tmp_path))

    assert session._wait_health(_connection()).port == 8188


def test_destroy_is_verified_once(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    provider = _Provider(offers=[{"id": "1"}], instances={"c1": _instance()})
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(profile, _settings(tmp_path), provider=provider)
    session.open()
    verified: list[str] = []
    monkeypatch.setattr(vast, "destroy_verified", lambda provider, cid: verified.append(cid))

    session.destroy()
    session.destroy()

    assert verified == ["c1"]
    assert session._contract_id is None
    assert session._contracts == set()


def test_destroy_verifies_every_created_contract(tmp_path: Path, monkeypatch) -> None:
    session = VastSession(fixture_profile(tmp_path / "profiles"), _settings(tmp_path))
    session._provider = _Provider()
    session._contracts = {"a", "b"}
    session._contract_id = "b"
    verified: list[str] = []
    monkeypatch.setattr(vast, "destroy_verified", lambda provider, cid: verified.append(cid))

    session.destroy()

    assert verified == ["a", "b"]
    assert session._contracts == set()
    assert session._contract_id is None


def test_destroy_without_provider_reports_a_tracked_contract(tmp_path: Path) -> None:
    session = VastSession(fixture_profile(tmp_path / "profiles"), _settings(tmp_path))
    session._contracts.add("c1")

    with pytest.raises(RentError, match="нет провайдера"):
        session.destroy()


def test_failed_rental_teardown_keeps_the_contract_for_the_final_destroy(
    tmp_path: Path, monkeypatch
) -> None:
    session = VastSession(fixture_profile(tmp_path / "profiles"), _settings(tmp_path))
    provider = _Provider()
    session._provider = provider
    session._contract_id = "c1"
    session._contracts.add("c1")
    calls: list[str] = []

    def fake_destroy(provider: _Provider, contract_id: str) -> bool:
        calls.append(contract_id)
        if len(calls) == 1:
            raise RentError("still there")
        return True

    monkeypatch.setattr(vast, "destroy_verified", fake_destroy)

    with pytest.raises(RentError, match="не уничтожен после сбоя аренды"):
        session._destroy_after_failure(provider, "c1")

    assert session._contract_id == "c1"
    assert session._contracts == {"c1"}

    session.destroy()

    assert calls == ["c1", "c1"]
    assert session._contract_id is None
    assert session._contracts == set()


def test_teardown_failure_stops_the_rental_loop(tmp_path: Path, monkeypatch) -> None:
    provider = _Provider(offers=[{"id": "1"}, {"id": "2"}], instances={})
    _, clock, sleep = _clock_pair()
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    monkeypatch.setattr(
        vast,
        "destroy_verified",
        lambda provider, contract_id: (_ for _ in ()).throw(RentError("still there")),
    )
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="не уничтожен после сбоя аренды"):
        session.open()

    assert len(provider.queries) == 1
    assert session._contract_id == "c1"
    assert session._contracts == {"c1"}


def test_pool_keeps_the_ready_offer_and_destroys_losers(tmp_path: Path, monkeypatch) -> None:
    provider = _PoolProvider(
        offers=[
            {"id": "1", "public_ipaddr": "10.0.0.1", "dph_total": 0.3},
            {"id": "2", "public_ipaddr": "10.0.0.2", "dph_total": 0.4},
            {"id": "3", "public_ipaddr": "10.0.0.3", "dph_total": 0.5},
        ],
        ready="",
    )
    _publish_ready_when_all_created(provider, "2")
    _fast_poll(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3),
        provider=provider,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c2"
    assert info.cost_per_hour == 0.4
    assert len(provider.created) == 3
    assert sorted(provider.destroyed) == ["c1", "c3"]
    assert set(provider.instances) == {"c2"}
    assert session._contracts == {"c2"}
    assert session._contract_id == "c2"


def test_pool_without_a_ready_server_destroys_every_candidate(tmp_path: Path, monkeypatch) -> None:
    provider = _PoolProvider(
        offers=[
            {"id": "1", "public_ipaddr": "10.0.0.1"},
            {"id": "2", "public_ipaddr": "10.0.0.2"},
            {"id": "3", "public_ipaddr": "10.0.0.3"},
        ],
        ready="",
    )
    _, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="годного сервера"):
        session._rent(provider)

    assert len(provider.created) == 3
    assert sorted(provider.destroyed) == ["c1", "c2", "c3"]
    assert provider.instances == {}
    assert session._contracts == set()


def test_pool_of_one_rents_offers_one_by_one(tmp_path: Path, monkeypatch) -> None:
    provider = _PoolProvider(
        offers=[
            {"id": "1", "public_ipaddr": "10.0.0.1"},
            {"id": "2", "public_ipaddr": "10.0.0.2"},
        ],
        ready="2",
    )
    _, clock, sleep = _clock_pair()
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=1),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    info = session._rent(provider)

    assert [config["offer_id"] for config in provider.created] == ["1", "2"]
    assert provider.destroyed == ["c1"]
    assert info.connection.contract_id == "c2"
    assert session._contracts == {"c2"}
    assert len(provider.queries) == 1


def test_loser_teardown_failure_stops_the_pool(tmp_path: Path, monkeypatch) -> None:
    provider = _PoolProvider(
        offers=[
            {"id": "1", "public_ipaddr": "10.0.0.1"},
            {"id": "2", "public_ipaddr": "10.0.0.2"},
            {"id": "3", "public_ipaddr": "10.0.0.3"},
        ],
        ready="",
    )
    _publish_ready_when_all_created(provider, "2")
    calls: list[str] = []

    def fake_destroy(provider: _Provider, contract_id: str) -> bool:
        calls.append(contract_id)
        if contract_id == "c1":
            raise RentError("still there")
        provider.instances.pop(contract_id, None)
        return True

    _fast_poll(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    monkeypatch.setattr(vast, "destroy_verified", fake_destroy)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3),
        provider=provider,
    )

    with pytest.raises(RentError, match="проигравший сервер c1 не уничтожен"):
        session._rent(provider)

    assert calls == ["c1", "c3"]
    assert len(provider.queries) == 1
    assert session._contracts == {"c1", "c2"}


def test_pool_survives_a_candidate_that_fails_to_start(tmp_path: Path, monkeypatch) -> None:
    provider = _PoolProvider(
        offers=[
            {"id": "1", "public_ipaddr": "10.0.0.1"},
            {"id": "2", "public_ipaddr": "10.0.0.2"},
        ],
        ready="2",
        broken="1",
    )
    _fast_poll(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3),
        provider=provider,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c2"
    assert provider.destroyed == []
    assert session._contracts == {"c2"}


def test_pool_rents_the_offers_left_when_there_are_fewer_than_the_size(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _Provider(
        offers=[{"id": "only", "public_ipaddr": "1.2.3.4"}],
        instances={"c1": _instance()},
    )
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3),
        provider=provider,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c1"
    assert len(provider.created) == 1


def test_pool_rents_a_new_offer_found_while_it_waits(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2"]], ready="2")
    state, clock, sleep = _refill_clock(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=10,
            server_ready_timeout=60,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c2"
    assert [config["offer_id"] for config in provider.created] == ["1", "2"]
    assert len(provider.queries) >= 2
    assert provider.queries[0]["verified"] == {"eq": True}
    assert provider.destroyed == ["c1"]
    assert session._contracts == {"c2"}
    assert session._contract_id == "c2"
    assert state[0] < 30


def test_pool_never_rents_more_than_the_size(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2", "3", "4"], []])
    state, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=2, offer_refresh_seconds=10),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="годного сервера"):
        session._rent(provider)

    assert sorted(config["offer_id"] for config in provider.created) == ["1", "2"]
    assert provider.max_alive == 2
    assert sorted(provider.destroyed) == ["c1", "c2"]
    assert session._contracts == set()
    assert state[0] >= 60


def test_pool_stops_searching_after_the_winner(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2"]], ready="1")
    state, clock, sleep = _refill_clock(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=60,
            server_ready_timeout=600,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c1"
    assert [config["offer_id"] for config in provider.created] == ["1"]
    assert len(provider.queries) == 1
    assert provider.destroyed == []
    assert state[0] < 60


def test_pool_does_not_rent_when_a_winner_appears_during_a_blocking_search(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2"]])
    state, clock, sleep = _refill_clock(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=10,
            server_ready_timeout=600,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )
    finished = threading.Event()
    attempt = session._attempt

    def record_attempt(*args: Any, **kwargs: Any) -> None:
        try:
            attempt(*args, **kwargs)
        finally:
            finished.set()

    session._attempt = record_attempt  # type: ignore[method-assign]
    search = provider.search_offers

    def blocking_search(query: dict[str, Any]) -> list[dict[str, Any]]:
        offers = search(query)
        if len(provider.queries) == 2:
            provider.instances["c1"]["ports"] = {"8188/tcp": [{"HostPort": "40000"}]}
            assert finished.wait(5)
        return offers

    provider.search_offers = blocking_search  # type: ignore[method-assign]

    info = session._rent(provider)

    assert info.connection.contract_id == "c1"
    assert [config["offer_id"] for config in provider.created] == ["1"]
    assert len(provider.queries) == 2
    assert state[0] < 60


def test_pool_does_not_rent_when_the_search_crosses_the_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2"]])
    state, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=10,
            server_ready_timeout=60,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )
    search = provider.search_offers

    def slow_search(query: dict[str, Any]) -> list[dict[str, Any]]:
        offers = search(query)
        if len(provider.queries) == 2:
            state[0] = 65.0
        return offers

    provider.search_offers = slow_search  # type: ignore[method-assign]

    with pytest.raises(RentError, match="годного сервера"):
        session._rent(provider)

    assert [config["offer_id"] for config in provider.created] == ["1"]
    assert state[0] >= 60


def test_pool_stops_renting_when_a_ready_worker_is_not_yet_reported(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RefillProvider(searches=[["1"], ["1", "2"]], ready="1")
    state, clock, sleep = _refill_clock(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=10,
            server_ready_timeout=600,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )
    release = threading.Event()
    winner_blocked = threading.Event()
    attempt = session._attempt
    started = clock()

    def delayed_attempt(*args: Any, **kwargs: Any) -> None:
        outcomes = args[-1]
        put = outcomes.put

        def blocking_put(item: Any) -> None:
            if isinstance(item, ServerInfo):
                winner_blocked.set()
                release.wait(5)
            put(item)

        outcomes.put = blocking_put  # type: ignore[method-assign]
        try:
            attempt(*args, **kwargs)
        finally:
            outcomes.put = put

    session._attempt = delayed_attempt  # type: ignore[method-assign]
    pool_sleep = session._pool_sleep

    def releasing_sleep(pool_state: Any, *args: Any, **kwargs: Any) -> Any:
        assert winner_blocked.wait(5)
        if pool_state.stop.is_set() and clock() - started >= 10:
            release.set()
        return pool_sleep(pool_state, *args, **kwargs)

    session._pool_sleep = releasing_sleep  # type: ignore[method-assign]

    info = session._rent(provider)

    assert info.connection.contract_id == "c1"
    assert [config["offer_id"] for config in provider.created] == ["1"]
    assert len(provider.queries) == 1
    assert state[0] < 60


def test_pool_keeps_a_candidate_that_failed_to_tear_down(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[["1"]], ready="")
    state, clock, sleep = _refill_clock(monkeypatch)
    destroyed: list[str] = []

    def failing_destroy(provider: Any, contract_id: str, **kwargs: Any) -> bool:
        destroyed.append(contract_id)
        raise RentError("destroy is down")

    monkeypatch.setattr(vast, "destroy_verified", failing_destroy)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=10,
            server_ready_timeout=60,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    def failing_wait(*args: Any, **kwargs: Any) -> ServerConnection:
        raise RentError("хост не выдал порт")

    session._wait_ready = failing_wait  # type: ignore[method-assign]

    with pytest.raises(vast._TeardownError, match="не уничтожен после сбоя аренды"):
        session._rent(provider)

    assert destroyed == ["c1"]
    assert [config["offer_id"] for config in provider.created] == ["1"]
    assert len(provider.queries) == 1
    assert session._contracts == {"c1"}
    assert state[0] < 60


def test_pool_does_not_rent_after_stop(tmp_path: Path) -> None:
    provider = _RefillProvider(searches=[["1"]])
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=2),
        provider=provider,
    )
    pool_state = vast._PoolState(deadline=session._clock() + 60)
    pool_state.stop.set()
    offers = [{"id": "1"}]

    started = session._start_attempts(provider, offers, pool_state)
    session._attempt(provider, offers[0], pool_state.deadline, pool_state.stop, pool_state.outcomes)

    assert started == 0
    assert provider.created == []
    assert provider.queries == []
    assert pool_state.outcomes.empty()


@pytest.mark.parametrize(
    ("searches", "refresh", "inject_at"),
    [([["1"]], 600, 2), ([["1"], []], 10, 3)],
    ids=["deadline", "empty_refill"],
)
def test_pool_reads_a_winner_that_arrives_after_the_outcome_drain(
    tmp_path: Path,
    monkeypatch,
    searches: list[list[str]],
    refresh: int,
    inject_at: int,
) -> None:
    provider = _RefillProvider(searches=searches)
    _, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=refresh,
            server_ready_timeout=60,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )
    winner = ServerInfo(connection=_connection(), cost_per_hour=None)
    contract_id = winner.connection.contract_id
    session._contracts = {contract_id}
    provider.instances[contract_id] = _instance()
    monkeypatch.setattr(session, "_start_attempts", lambda *args: 0)
    calls = 0
    active = session._active

    def racing_active(pool_state: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == inject_at:
            pool_state.outcomes.put(winner)
            return 0
        return active(pool_state)

    session._active = racing_active  # type: ignore[method-assign]

    info = session._rent_pool(provider, True, set(), set())

    assert info is winner
    assert provider.destroyed == []


def test_pool_returns_a_queued_winner_without_waiting_for_the_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RefillProvider(searches=[["1"]])
    state, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(
            tmp_path,
            rent_pool_size=2,
            offer_refresh_seconds=600,
            server_ready_timeout=600,
        ),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )
    winner = ServerInfo(connection=_connection(), cost_per_hour=None)
    contract_id = winner.connection.contract_id
    session._contracts = {contract_id}
    provider.instances[contract_id] = _instance()
    monkeypatch.setattr(session, "_start_attempts", lambda *args: 0)

    def racing_active(pool_state: Any) -> int:
        pool_state.outcomes.put(winner)
        return 0

    session._active = racing_active  # type: ignore[method-assign]

    info = session._rent_pool(provider, True, set(), set())

    assert info is winner
    assert provider.destroyed == []
    assert state[0] < 60


def test_pool_refills_a_slot_freed_by_a_failed_attempt(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[["1", "2"], ["1", "2", "3"]], ready="3", broken="1")
    state, clock, sleep = _refill_clock(monkeypatch)
    monkeypatch.setattr(vast, "_healthy", lambda connection: True)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=2, offer_refresh_seconds=10),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    info = session._rent(provider)

    assert info.connection.contract_id == "c3"
    assert sorted(config["offer_id"] for config in provider.created) == ["2", "3"]
    assert provider.max_alive == 2
    assert provider.destroyed == ["c2"]
    assert len(provider.queries) == 2
    assert session._contracts == {"c3"}
    assert state[0] < 30


def test_pool_ends_the_pass_when_failed_attempts_find_no_fresh_offers(
    tmp_path: Path, monkeypatch
) -> None:
    provider = _RefillProvider(searches=[["1"], []], broken="1")
    state, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=2, offer_refresh_seconds=10),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="годного сервера"):
        session._rent(provider)

    assert provider.created == []
    assert state[0] == 10.0
    assert session._contracts == set()


def test_pool_empty_market_fails_without_waiting(tmp_path: Path, monkeypatch) -> None:
    provider = _RefillProvider(searches=[[]])
    state, clock, sleep = _refill_clock(monkeypatch)
    session = VastSession(
        fixture_profile(tmp_path / "profiles"),
        _settings(tmp_path, rent_pool_size=3, offer_refresh_seconds=600),
        provider=provider,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(RentError, match="годного сервера"):
        session._rent(provider)

    assert state[0] == 0.0
    assert len(provider.queries) == len(vast._VERIFIED_PASSES)
    assert provider.created == []


def test_connection_is_none_for_broken_ports_and_falls_back_for_metadata() -> None:
    assert connection_from_instance("7", {"public_ipaddr": "1.2.3.4", "ports": []}) is None
    assert (
        connection_from_instance(
            "7",
            {"public_ipaddr": "1.2.3.4", "ports": {"8188/tcp": [{"HostPort": "x"}]}},
        )
        is None
    )

    connection = connection_from_instance(
        "7",
        {"public_ipaddr": "1.2.3.4", "ports": {"8188/tcp": [{"HostPort": 40000}]}},
    )

    assert connection is not None
    assert connection.gpu == "Unknown"
    assert connection.image == ""


def test_stale_reap_skips_unavailable_list(tmp_path: Path) -> None:
    provider = _Provider()
    provider.fail_list = True

    assert reap_stale(provider, 6, now=1_000_000.0) == []


def test_stale_reap_keeps_going_when_one_destroy_fails(tmp_path: Path, monkeypatch) -> None:
    now = 1_000_000.0
    instances = {
        "1": {"id": "1", "label": "content-factory-generator", "start_date": now - 7 * 3600},
        "2": {"id": "2", "label": "content-factory-generator", "start_date": now - 8 * 3600},
        "3": {"id": "3", "label": "someone-else", "start_date": now - 9 * 3600},
        "4": {"id": "4", "label": "content-factory-generator", "start_date": "broken"},
    }
    provider = _Provider(instances=instances)
    calls: list[str] = []

    def fake_destroy(provider: _Provider, cid: str) -> bool:
        calls.append(cid)
        if cid == "2":
            raise RentError("cannot destroy")
        return True

    monkeypatch.setattr(vast, "destroy_verified", fake_destroy)

    assert reap_stale(provider, 6, now=now) == ["1"]
    assert calls == ["1", "2"]


def test_destroy_verified_accepts_gone_instance() -> None:
    provider = _Provider()
    provider.destroy_error = RentError("boom")

    assert destroy_verified(provider, "7") is True


def test_destroy_verified_retries_until_the_instance_disappears() -> None:
    provider = _Provider(instances={"7": {"id": "7"}})
    provider.destroy_instance = lambda cid: True  # type: ignore[method-assign]
    remaining = [2]

    def show(cid: str) -> dict[str, Any]:
        if remaining[0] > 0:
            remaining[0] -= 1
            return {"id": cid}
        raise RentError(f"Instance {cid} not found")

    provider.show_instance = show  # type: ignore[method-assign]

    assert destroy_verified(provider, "7", attempts=3, pause=0, sleep=lambda _: None) is True
    assert remaining[0] == 0


def test_destroy_verified_fails_when_list_stays_broken() -> None:
    provider = _Provider(instances={"7": {"id": "7"}})

    def show(cid: str) -> dict[str, Any]:
        raise RentError("api is down")

    provider.show_instance = show  # type: ignore[method-assign]

    with pytest.raises(RentError, match="не уничтожен"):
        destroy_verified(provider, "7", attempts=2, pause=0, sleep=lambda _: None)


def test_offers_respect_profile_and_global_cost_ceiling(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    provider = _Provider()

    _offers(provider, profile, _settings(tmp_path), None)
    _offers(provider, profile, _settings(tmp_path, max_cost_per_hour=0.5), True)

    assert provider.queries[0]["dph_total"] == {"lte": profile.max_cost}
    assert "verified" not in provider.queries[0]
    assert provider.queries[1]["dph_total"] == {"lte": 0.5}
    assert provider.queries[1]["verified"] == {"eq": True}


def test_offers_treat_transient_search_as_empty(tmp_path: Path) -> None:
    provider = _Provider()
    provider.search_offers = lambda query: (_ for _ in ()).throw(_VastTransient("timeout"))  # type: ignore[method-assign]

    assert (
        _offers(provider, fixture_profile(tmp_path / "profiles"), _settings(tmp_path), None) == []
    )


def test_healthy_checks_comfy_system_stats(monkeypatch) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(vast.httpx, "get", lambda url, timeout: _Response())
    assert _healthy(_connection()) is True

    def broken(url: str, timeout: httpx.Timeout) -> Any:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(vast.httpx, "get", broken)
    assert _healthy(_connection()) is False


def test_float_or_none_parses_numbers() -> None:
    assert _float_or_none(None) is None
    assert _float_or_none("1.5") == 1.5
    assert _float_or_none("broken") is None
