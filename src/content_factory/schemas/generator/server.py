"""Connection details and price of one rented generator server."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerConnection:
    """Address and identity of a ready ComfyUI server."""

    contract_id: str
    ip: str
    port: int
    gpu: str
    image: str

    def url(self) -> str:
        """Return the base URL of the ComfyUI HTTP API."""
        return f"http://{self.ip}:{self.port}"


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """A ready server plus the hourly price of its Vast.ai offer."""

    connection: ServerConnection
    cost_per_hour: float | None = None
