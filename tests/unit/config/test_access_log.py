"""Join-token redaction in uvicorn access records."""

from __future__ import annotations

import logging

import pytest

from content_factory.config.access_log import (
    JoinTokenFilter,
    install_access_log_filter,
    redact_join_path,
)

_TOKEN = "P4Q1nmCWJBp_LP7bPMUrbk97m5RIRy7pNXsXj5aVYbo"
_FORMAT = '%s - "%s %s HTTP/%s" %d'


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/join/{_TOKEN}", "/join/[redacted]"),
        (f"/join/{_TOKEN}?x=1", "/join/[redacted]"),
        ("/join/", "/join/[redacted]"),
        ("/join", "/join"),
        ("/api/v1/content/archives", "/api/v1/content/archives"),
        ("/api/v1/stock", "/api/v1/stock"),
        ("/", "/"),
    ],
)
def test_redact_join_path_only_touches_the_join_token(path: str, expected: str) -> None:
    assert redact_join_path(path) == expected


def _record(*args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=_FORMAT,
        args=args,
        exc_info=None,
    )


def test_filter_redacts_the_token_and_keeps_the_record_shape() -> None:
    record = _record("1.2.3.4:5", "GET", f"/join/{_TOKEN}?next=1", "1.1", 303)

    assert JoinTokenFilter().filter(record) is True

    assert record.args == ("1.2.3.4:5", "GET", "/join/[redacted]", "1.1", 303)
    assert _TOKEN not in record.getMessage()
    assert "/join/[redacted]" in record.getMessage()


def test_filter_leaves_ordinary_records_untouched() -> None:
    record = _record("1.2.3.4:5", "GET", "/api/v1/content/archives", "1.1", 200)

    assert JoinTokenFilter().filter(record) is True

    assert record.args == ("1.2.3.4:5", "GET", "/api/v1/content/archives", "1.1", 200)


@pytest.mark.parametrize(
    "args",
    [
        "a plain string",
        {"path": f"/join/{_TOKEN}", "method": "GET"},
        (),
        ("1.2.3.4:5", "GET"),
        ("1.2.3.4:5", "GET", None, "1.1", 200),
        ("1.2.3.4:5", 42, f"/join/{_TOKEN}", "1.1", 200),
    ],
)
def test_filter_tolerates_unusual_args(args: object) -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request",
        args=args,
        exc_info=None,
    )

    assert JoinTokenFilter().filter(record) is True


def test_install_is_idempotent_and_attaches_one_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access_logger, "filters", [])

    install_access_log_filter()
    install_access_log_filter()

    installed = [item for item in access_logger.filters if isinstance(item, JoinTokenFilter)]
    assert len(installed) == 1


def test_installed_filter_redacts_a_join_record_but_not_an_api_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    install_access_log_filter()
    access_logger = logging.getLogger("uvicorn.access")

    access_logger.info(_FORMAT, "1.2.3.4:5", "GET", f"/join/{_TOKEN}", "1.1", 303)
    access_logger.info(_FORMAT, "1.2.3.4:5", "GET", "/api/v1/content/archives", "1.1", 200)

    assert _TOKEN not in caplog.text
    assert "/join/[redacted]" in caplog.text
    assert "/api/v1/content/archives" in caplog.text
