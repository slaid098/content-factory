"""Vast.ai CLI provider: search, create, show, list and destroy instances.

Calls go through the official ``vastai`` package CLI with ``--raw``. The
token travels only in the ``VAST_API_KEY`` environment variable of the child
process; it never appears in argv, logs or error messages.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from content_factory.exceptions.generator import RentError

_VAST_TOKEN_ENV = "VAST_API_TOKEN"
_VAST_CLI_TOKEN_ENV = "VAST_API_KEY"
_CLI_TIMEOUT = 120.0

_OP_TOKENS = {
    "eq": "=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "neq": "!=",
    "in": " in ",
    "notin": " nin ",
}
_AUTH_MARKERS = ("unauthorized", "invalid api key", "invalid user key", "permission denied")
_BALANCE_MARKERS = ("balance", "credit", "insufficient", "not enough funds")
_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "too many requests",
)
_GONE_MARKERS = (
    "not found",
    "notfound",
    "does not exist",
    "no longer exists",
    "already destroyed",
    "already deleted",
    "no such instance",
)
_NOISE_PREFIXES = ("DEPRECATED", "Sent key from", "Unset the VAST_API_KEY")
_FAILED_RE = re.compile(r"failed with error (\d{3})\s*:?\s*(.*)", re.IGNORECASE)


class _VastFatal(RentError):
    """Unrecoverable Vast.ai failure: bad key, no access or API refusal."""


class _VastInsufficientBalance(_VastFatal):
    """The Vast.ai account balance is exhausted."""


class _VastTransient(RentError):
    """Temporary Vast.ai failure worth a retry."""


class VastProvider:
    """CLI wrapper of the official ``vastai`` package."""

    def __init__(
        self,
        api_token: str = "",
        *,
        timeout: float = _CLI_TIMEOUT,
        command: Sequence[str] | None = None,
    ) -> None:
        """Store the token and timeout; no network call happens on import."""
        self._token = (api_token or "").strip()
        self._timeout = timeout
        self._command = list(command) if command is not None else _default_command()

    def search_offers(self, query: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return offers matching the operator-dictionary query."""
        flags, tokens = _cli_query(query or {})
        data = self._run(["search", "offers", "--no-default", *flags, *tokens])
        if data is None:
            return []
        offers = data.get("offers") if isinstance(data, Mapping) else data
        return [dict(item) for item in offers] if isinstance(offers, list) else []

    def create_instance(self, config: Mapping[str, Any]) -> str:
        """Rent one offer and return the Vast.ai contract id.

        Raises:
            _VastFatal: ``offer_id`` or ``image`` is missing.
        """
        offer_id = str(config.get("offer_id") or "").strip()
        image = str(config.get("image") or "").strip()
        if not offer_id or not image:
            raise _VastFatal(f"аренда без offer_id/image невозможна (offer_id={offer_id!r})")
        args = [
            "create",
            "instance",
            offer_id,
            "--image",
            image,
            "--disk",
            _numeric(config.get("disk", 80)),
            "--ssh",
            "--direct",
        ]
        env_line = _env_line(config.get("env"), config.get("docker_options"))
        if env_line:
            args += ["--env", env_line]
        for key, flag in (
            ("label", "--label"),
            ("onstart_cmd", "--onstart-cmd"),
            ("image_login", "--login"),
        ):
            value = config.get(key)
            if value:
                args += [flag, str(value)]
        return _new_contract(self._run(args))

    def destroy_instance(self, contract_id: str) -> bool:
        """Destroy one instance; an already gone id is not an error."""
        try:
            data = self._run(["destroy", "instance", str(contract_id)])
        except _VastTransient:
            raise
        except RentError as ex:
            if not _already_gone(str(ex)):
                raise
            return True
        if isinstance(data, Mapping) and data.get("success") is False:
            message = str(data.get("msg") or data)
            if not _already_gone(message):
                raise RentError(f"Vast.ai не уничтожил инстанс {contract_id}: {message}")
        return True

    def show_instance(self, contract_id: str) -> dict[str, Any]:
        """Return one instance by id.

        Raises:
            RentError: No instance with this id exists in the account list.
        """
        for instance in self.list_instances():
            if str(instance.get("id")) == str(contract_id):
                return instance
        raise RentError(f"Instance {contract_id} not found")

    def list_instances(self) -> list[dict[str, Any]]:
        """Return every instance of the account."""
        data = self._run(["show", "instances"])
        if data is None:
            return []
        items = data.get("instances") if isinstance(data, Mapping) else data
        return [dict(item) for item in items] if isinstance(items, list) else []

    def _run(self, args: Sequence[str]) -> Any:
        """Run the CLI with ``--raw`` and parse its JSON answer."""
        environment = self._env()
        completed = _run_cli([*self._command, *args, "--raw"], environment, self._timeout)
        stdout, stderr, code = completed
        error = _cli_error(stdout, stderr, code)
        if error is not None:
            raise error
        text = stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as ex:
            raise RentError(f"Vast.ai CLI вернул не-JSON: {text[:300]}") from ex

    def _env(self) -> dict[str, str]:
        """Return the child environment with the token only in ``VAST_API_KEY``."""
        token = self._token or (os.environ.get(_VAST_TOKEN_ENV) or "").strip()
        if not token:
            raise _VastFatal(f"Vast.ai недоступен: не задан {_VAST_TOKEN_ENV}")
        return {**os.environ, _VAST_CLI_TOKEN_ENV: token}


def _cli_query(query: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Split the operator-dictionary query into CLI flags and query tokens."""
    flags: list[str] = []
    tokens: list[str] = []
    for key, value in query.items():
        if key == "limit":
            flags += ["--limit", _numeric(value)]
        elif key == "order":
            order = _cli_order(value)
            if order:
                flags += ["--order", order]
        elif key == "type":
            flags += ["--type", str(value)]
        elif isinstance(value, Mapping):
            tokens += _field_tokens(str(key), value)
    return flags, tokens


def _cli_order(order: Any) -> str:
    """Render ``[["dph_total", "asc"]]`` as the CLI order string."""
    parts: list[str] = []
    for entry in order or ():
        if isinstance(entry, list | tuple) and entry:
            field = str(entry[0])
            direction = str(entry[1]) if len(entry) > 1 else "asc"
            parts.append(f"-{field}" if direction == "desc" else field)
        elif entry:
            parts.append(str(entry))
    return ",".join(parts)


def _field_tokens(field: str, ops: Mapping[str, Any]) -> list[str]:
    """Render operator dictionaries such as ``{"gte": 30}`` as query tokens."""
    tokens: list[str] = []
    for op, value in ops.items():
        token = _OP_TOKENS.get(str(op))
        if token is None or value is None:
            continue
        items = value if isinstance(value, list | tuple) else [value]
        rendered = ",".join(_cli_value(item) for item in items if item is not None)
        if rendered:
            tokens.append(f"{field}{token}{rendered}")
    return tokens


def _cli_value(value: Any) -> str:
    """Render one query value: booleans as true/false, spaces as ``_``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(int(value)) if float(value).is_integer() else f"{value:g}"
    return str(value).replace(" ", "_")


def _numeric(value: Any) -> str:
    """Render a CLI flag number without a trailing ``.0``."""
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _env_line(env: Any, docker_options: Any) -> str:
    """Build the ``--env`` value: container variables and port forwarding."""
    options = (
        ("-p 8188:8188", "--dns=8.8.8.8", "--dns=8.8.4.4", "--network=bridge")
        if docker_options is None
        else tuple(docker_options)
    )
    parts = [f"-e {key}={value}" for key, value in env.items()] if isinstance(env, Mapping) else []
    parts += [str(item) for item in options if item]
    return " ".join(parts)


def _new_contract(data: Any) -> str:
    """Extract ``new_contract`` from the CLI answer."""
    if isinstance(data, Mapping):
        contract = str(data.get("new_contract") or "").strip()
        if contract:
            return contract
    raise RentError(f"Vast.ai не вернул new_contract: {data!r}")


def _default_command() -> list[str]:
    """Return the argv that starts the pinned 0.2.x CLI package."""
    try:
        spec = importlib.util.find_spec("vastai.vast")
    except (ImportError, ModuleNotFoundError):
        spec = None
    if spec is None or spec.origin is None:
        raise _VastFatal("пакет vastai не установлен — аренда Vast.ai недоступна")
    return [sys.executable, spec.origin]


def _run_cli(
    command: Sequence[str],
    environment: Mapping[str, str],
    timeout: float,
) -> tuple[str, str, int]:
    """Run the CLI and return stdout, stderr and the exit code."""
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=dict(environment),
        )
    except subprocess.TimeoutExpired as ex:
        raise _VastTransient(f"Vast.ai CLI не ответил за {timeout:g} с") from ex
    except OSError as ex:
        raise _VastFatal(f"не удалось запустить Vast.ai CLI: {ex}") from ex
    return completed.stdout, completed.stderr, completed.returncode


def _cli_error(stdout: str, stderr: str, code: int) -> RentError | None:
    """Return a classified error for an unsuccessful CLI call."""
    payload = _json_error(stderr)
    if payload is not None:
        return _classify(*payload)
    failed = _FAILED_RE.search(stdout)
    if failed is not None:
        return _classify(int(failed.group(1)), failed.group(2).strip())
    if code == 0:
        return None
    text = _stderr_noise(stderr) or stdout.strip() or f"exit code {code}"
    return _classify(None, text)


def _json_error(stderr: str) -> tuple[int | None, str] | None:
    """Read a JSON error object from stderr."""
    for line in reversed(_stderr_noise(stderr).splitlines()):
        data = _json_object(line)
        if data is not None and data.get("error"):
            status = data.get("status_code")
            code = int(status) if isinstance(status, int | str) and str(status).isdigit() else None
            return code, str(data.get("msg") or data)
    return None


def _json_object(line: str) -> dict[str, Any] | None:
    """Parse one line as a JSON object, if possible."""
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _stderr_noise(stderr: str) -> str:
    """Drop informational CLI lines from stderr."""
    return "\n".join(
        line
        for line in stderr.splitlines()
        if line.strip() and not line.lstrip().startswith(_NOISE_PREFIXES)
    ).strip()


def _classify(status: int | None, message: str) -> RentError:
    """Classify a CLI/API failure into a domain error."""
    text = f"Vast.ai: {message}".strip()
    low = message.lower()
    if status in (401, 403) or any(marker in low for marker in _AUTH_MARKERS):
        return _VastFatal(f"{text} — проверьте {_VAST_TOKEN_ENV}")
    if any(marker in low for marker in _BALANCE_MARKERS):
        return _VastInsufficientBalance(f"{text} — пополните баланс Vast.ai")
    if (status is not None and (status == 429 or status >= 500)) or (
        status is None and any(marker in low for marker in _TRANSIENT_MARKERS)
    ):
        return _VastTransient(text)
    return RentError(text)


def _already_gone(text: str) -> bool:
    """Return whether the error means the instance no longer exists."""
    low = text.lower()
    return any(marker in low for marker in _GONE_MARKERS)
