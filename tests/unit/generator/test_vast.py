"""Vast.ai adapter: offers dialect, connections, reap and verified teardown."""

from __future__ import annotations

from typing import Any

import pytest

from content_factory.exceptions.generator import RentError
from content_factory.services.generator.vast import (
    connection_from_instance,
    destroy_verified,
    reap_stale,
)
from content_factory.services.generator.vast_cli import VastProvider, _cli_query

_TOKEN_ENV = "VAST_API_TOKEN"


class _Provider:
    """In-memory Vast.ai account."""

    def __init__(self, instances: list[dict[str, Any]] | None = None) -> None:
        self.instances = {str(item["id"]): dict(item) for item in instances or []}
        self.destroyed: list[str] = []
        self.fail_destroy = False

    def destroy_instance(self, contract_id: str) -> bool:
        if self.fail_destroy:
            raise RentError("сбой API")
        self.destroyed.append(str(contract_id))
        self.instances.pop(str(contract_id), None)
        return True

    def show_instance(self, contract_id: str) -> dict[str, Any]:
        instance = self.instances.get(str(contract_id))
        if instance is None:
            raise RentError(f"Instance {contract_id} not found")
        return instance

    def list_instances(self) -> list[dict[str, Any]]:
        return list(self.instances.values())


def test_connection_uses_public_port_of_comfy() -> None:
    instance = {
        "public_ipaddr": "1.2.3.4",
        "ports": {"8188/tcp": [{"HostPort": "40123"}]},
        "gpu_name": "L40S",
        "image_uuid": "image",
    }

    connection = connection_from_instance("7", instance)

    assert connection is not None
    assert connection.url() == "http://1.2.3.4:40123"
    assert connection.gpu == "L40S"


def test_connection_is_none_until_port_is_open() -> None:
    assert connection_from_instance("7", {"public_ipaddr": "1.2.3.4"}) is None


def test_destroy_is_verified_against_instance_list() -> None:
    provider = _Provider([{"id": 42, "label": "content-factory-generator"}])

    assert destroy_verified(provider, "42", sleep=lambda _: None) is True
    assert provider.destroyed == ["42"]
    assert provider.instances == {}


def test_destroy_raises_when_instance_stays_listed() -> None:
    provider = _Provider([{"id": 42, "label": "content-factory-generator"}])
    provider.destroy_instance = lambda _: True  # type: ignore[method-assign]

    with pytest.raises(RentError, match="не уничтожен"):
        destroy_verified(provider, "42", attempts=2, pause=0, sleep=lambda _: None)


def test_stale_reap_touches_only_own_old_instances() -> None:
    now = 1_000_000.0
    instances = [
        {"id": 1, "label": "content-factory-generator", "start_date": now - 7 * 3600},
        {"id": 2, "label": "content-factory-generator", "start_date": now - 3600},
        {"id": 3, "label": "someone-else", "start_date": now - 30 * 3600},
        {"id": 4, "label": "content-factory-generator", "start_date": "broken"},
    ]
    provider = _Provider(instances)

    killed = reap_stale(provider, 6, now=now)

    assert killed == ["1"]
    assert provider.destroyed == ["1"]
    assert set(provider.instances) == {"2", "3", "4"}


def test_cli_query_uses_vast_operator_dialect() -> None:
    flags, tokens = _cli_query(
        {
            "limit": 20,
            "order": [["dph_total", "asc"]],
            "rentable": {"eq": True},
            "gpu_ram": {"gte": 30},
            "gpu_name": {"in": ["L40S"]},
        }
    )

    assert flags == ["--limit", "20", "--order", "dph_total"]
    assert tokens == [
        "rentable=true",
        "gpu_ram>=30",
        "gpu_name in L40S",
    ]


def test_missing_token_is_reported_without_running_the_cli(monkeypatch) -> None:
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    provider = VastProvider(command=["/bin/false"], timeout=1)

    with pytest.raises(RentError, match=_TOKEN_ENV):
        provider.list_instances()
