#!/usr/bin/env python3
"""Trusted-device access CLI: issue one-time links, list devices, revoke them.

Run inside the api container:

    docker compose exec -T api python scripts/device_access.py issue --label phone

``issue`` prints exactly one line — the absolute ``/join/<token>`` URL — to
stdout, and ``device_id``, ``link_expires_at`` and ``expires_at`` to stderr.
The link is one-time: it turns the first browser that opens it into a trusted
device and never works again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from content_factory.config.settings import Settings, get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.errors import DatabaseError
from content_factory.schemas.device_trust.models import IssuedDeviceLink, TrustedDeviceInfo
from content_factory.services.device_trust import issue_link, list_devices, revoke_device

_HEADERS = (
    "id",
    "label",
    "status",
    "created_at",
    "activated_at",
    "last_seen_at",
    "expires_at",
)


def db_path(settings: Settings) -> Path:
    """Return the operational database path of the service."""
    return settings.data_dir / DB_DIR / DB_FILENAME


def resolve_base_url(cli_value: str | None, settings_value: str) -> str:
    """Pick the origin used to build the join link.

    Args:
        cli_value: ``--base-url`` (wins when non-empty).
        settings_value: ``CONTENT_FACTORY_PUBLIC_BASE_URL`` from settings.

    Returns:
        The origin without a trailing slash.

    Raises:
        ValueError: Neither source carries a value.
    """
    base = (cli_value or settings_value).strip().rstrip("/")
    if not base:
        raise ValueError("public base URL is not set; pass --base-url")
    return base


def build_join_url(base_url: str, token: str) -> str:
    """Build the absolute one-time link for a raw token."""
    return f"{base_url.rstrip('/')}/join/{token}"


def _format_moment(value: datetime | None) -> str:
    """Render an aware UTC moment for the table, or ``-`` when unset."""
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def render_table(devices: list[TrustedDeviceInfo]) -> str:
    """Render the ``list`` table with stable column widths."""
    rows = [
        (
            device.id,
            device.label,
            device.status,
            _format_moment(device.created_at),
            _format_moment(device.activated_at),
            _format_moment(device.last_seen_at),
            _format_moment(device.expires_at),
        )
        for device in devices
    ]
    widths = [
        max([len(header), *(len(row[index]) for row in rows)])
        for index, header in enumerate(_HEADERS)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(_HEADERS)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows
    )
    return "\n".join(lines)


def _issue(args: argparse.Namespace) -> int:
    """Create a pending device row, print its URL and expiry."""
    settings = get_settings()
    try:
        base_url = resolve_base_url(args.base_url, settings.public_base_url)
        ttl_hours = settings.device_link_ttl_hours if args.ttl_hours is None else args.ttl_hours
        device_days = settings.device_ttl_days if args.device_days is None else args.device_days
        issued = issue_link(
            db_path(settings),
            args.label,
            ttl_hours,
            device_days,
        )
    except ValueError as ex:
        print(f"invalid arguments: {ex}", file=sys.stderr)
        return 1
    print(build_join_url(base_url, issued.token))
    _print_issued(issued)
    return 0


def _print_issued(issued: IssuedDeviceLink) -> None:
    """Write the machine-readable issue details to stderr."""
    print(f"device_id={issued.device_id}", file=sys.stderr)
    print(f"link_expires_at={issued.link_expires_at.isoformat()}", file=sys.stderr)
    print(f"expires_at={issued.expires_at.isoformat()}", file=sys.stderr)


def _list() -> int:
    """Print every trusted device with its lifecycle status."""
    settings = get_settings()
    print(render_table(list_devices(db_path(settings))))
    return 0


def _revoke(args: argparse.Namespace) -> int:
    """Revoke one device; a repeated call is a successful no-op."""
    settings = get_settings()
    if not revoke_device(db_path(settings), args.id):
        print(f"device {args.id} not found", file=sys.stderr)
        return 1
    print(f"revoked: {args.id}")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    """Route one parsed subcommand to its handler."""
    if args.command == "issue":
        return _issue(args)
    if args.command == "list":
        return _list()
    return _revoke(args)


def _parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Issue one-time trusted-device links, list and revoke devices.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue = subparsers.add_parser("issue", help="issue a one-time /join link")
    issue.add_argument("--label", required=True, help="device name (1..150 characters)")
    issue.add_argument(
        "--ttl-hours",
        type=int,
        default=None,
        help="link lifetime in hours (default: CONTENT_FACTORY_DEVICE_LINK_TTL_HOURS)",
    )
    issue.add_argument(
        "--device-days",
        type=int,
        default=None,
        help="device lifetime in days (default: CONTENT_FACTORY_DEVICE_TTL_DAYS)",
    )
    issue.add_argument(
        "--base-url",
        default=None,
        help="absolute origin for the link (default: CONTENT_FACTORY_PUBLIC_BASE_URL)",
    )

    subparsers.add_parser("list", help="list trusted devices")

    revoke = subparsers.add_parser("revoke", help="revoke a trusted device")
    revoke.add_argument("--id", required=True, help="trusted_devices.id")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (DatabaseError, ValidationError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
