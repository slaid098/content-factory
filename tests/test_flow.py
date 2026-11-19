from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.config.defaults import default_config
from content_factory.config.settings import Settings, SupplierSettings
from content_factory.db.connection import connect
from content_factory.db.runtime_state import load_state, save_state
from content_factory.exceptions.errors import ConfigError, SupplierError
from content_factory.flows.stock_topup import flow as stock_flow
from content_factory.flows.stock_topup.flow import stock_topup
from content_factory.schemas.config.runtime import LanguageConfig, RuntimeConfig
from content_factory.schemas.notify.alert import AlertKind
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.supplier.results import ProduceResult, StopReason

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")


def _runtime(**overrides: object) -> RuntimeConfig:
    return replace(default_config(), **overrides)


def _empty_state(**overrides: object) -> RuntimeState:
    return replace(RuntimeState.empty(), **overrides)


class _Produce:
    def __init__(self, outcome: ProduceResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, int]] = []

    def __call__(self, plan: dict[str, int], **kwargs: object) -> ProduceResult:
        del kwargs
        self.calls.append(dict(plan))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _Alerts:
    def __init__(self) -> None:
        self.kinds: list[AlertKind] = []

    def __call__(
        self,
        kind: AlertKind,
        text: str,
        *,
        settings: Settings,
        last_sent: dict[str, datetime],
        cooldown: float,
        now: datetime | None = None,
    ) -> datetime:
        del text, settings, last_sent, cooldown
        self.kinds.append(kind)
        return now or datetime.now(UTC)


class _SuppressedAlerts:
    def __call__(
        self,
        kind: AlertKind,
        text: str,
        *,
        settings: Settings,
        last_sent: dict[str, datetime],
        cooldown: float,
        now: datetime | None = None,
    ) -> None:
        del kind, text, settings, last_sent, cooldown, now
        return None


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    outcome: ProduceResult | Exception,
) -> tuple[_Produce, _Alerts]:
    produce = _Produce(outcome)
    alerts = _Alerts()
    monkeypatch.setattr(stock_flow, "produce", produce)
    monkeypatch.setattr(
        stock_flow,
        "load_supplier_settings",
        lambda: SupplierSettings(profile_id="profile"),
    )
    monkeypatch.setattr(stock_flow, "send_critical", alerts)
    return produce, alerts


def _run(
    tmp_path: Path,
    *,
    state: RuntimeState | None = None,
    runtime: RuntimeConfig | None = None,
) -> tuple[str, RuntimeState]:
    connection = connect(tmp_path / "app_data" / "db" / "db.sqlite3")
    try:
        if state is not None:
            save_state(connection, state)
        code = stock_flow._tick(
            connection,
            Settings(data_dir=tmp_path / "app_data"),
            runtime or _runtime(),
            now=_NOW,
        )
        return code, load_state(connection)
    finally:
        connection.close()


def _content(tmp_path: Path, language: str, count: int) -> None:
    for index in range(count):
        _touch(tmp_path / "app_data" / "content" / language / f"{index}.mp4")


def _count_rows(tmp_path: Path) -> int:
    connection: sqlite3.Connection = connect(tmp_path / "app_data" / "db" / "db.sqlite3")
    try:
        return connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0]
    finally:
        connection.close()


def test_idle_tick_does_not_produce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.ENOUGH_STOCK))
    state = _empty_state(last_production_at=_NOW - timedelta(hours=1))

    code, loaded = _run(tmp_path, state=state)

    assert code == "idle"
    assert produce.calls == []
    assert alerts.kinds == []
    assert loaded.last_production_at == state.last_production_at


def test_first_tick_produces_when_stock_is_low(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, alerts = _patch(
        monkeypatch,
        ProduceResult({"ru": 5}, 5, StopReason.TARGET_REACHED),
    )

    code, loaded = _run(tmp_path)

    assert code == "produced"
    assert produce.calls == [{"ru": 20}]
    assert alerts.kinds == []
    assert loaded.last_production_at == _NOW
    assert loaded.captcha_streak == 0
    assert loaded.zero_runs == 0


def test_elapsed_interval_produces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, _ = _patch(monkeypatch, ProduceResult({"ru": 1}, 1, StopReason.TARGET_REACHED))
    state = _empty_state(last_production_at=_NOW - timedelta(hours=7))

    code, _ = _run(tmp_path, state=state)

    assert code == "produced"
    assert len(produce.calls) == 1


def test_enough_stock_skips_production_and_keeps_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.ENOUGH_STOCK))
    _content(tmp_path, "ru", 1)
    runtime = _runtime(languages={"ru": LanguageConfig(enabled=True, stock_target=1)})

    code, loaded = _run(tmp_path, runtime=runtime)

    assert code == "enough_stock"
    assert produce.calls == []
    assert alerts.kinds == []
    assert loaded.last_production_at is None
    assert _count_rows(tmp_path) == 0


def test_active_captcha_pause_skips_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, _ = _patch(monkeypatch, ProduceResult({}, 0, StopReason.ENOUGH_STOCK))
    state = _empty_state(captcha_blocked_until=_NOW + timedelta(hours=1))

    code, loaded = _run(tmp_path, state=state)

    assert code == "captcha_pause"
    assert produce.calls == []
    assert loaded.last_production_at is None


def test_captcha_retry_bypasses_interval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, _ = _patch(monkeypatch, ProduceResult({"ru": 2}, 2, StopReason.TARGET_REACHED))
    state = _empty_state(
        captcha_streak=1,
        last_production_at=_NOW - timedelta(minutes=10),
    )

    code, loaded = _run(tmp_path, state=state)

    assert code == "captcha_retry"
    assert produce.calls == [{"ru": 20}]
    assert loaded.captcha_streak == 0
    assert loaded.captcha_blocked_until is None


def test_captcha_result_pauses_and_counts_streak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    blocked = _NOW + timedelta(hours=2)
    _patch(
        monkeypatch,
        ProduceResult({}, 0, StopReason.CAPTCHA, blocked_until=blocked.isoformat()),
    )

    code, loaded = _run(tmp_path)

    assert code == "captcha"
    assert loaded.captcha_streak == 1
    assert loaded.captcha_blocked_until == blocked
    assert loaded.last_production_at == _NOW


def test_captcha_without_supplier_cooldown_uses_config_pause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(monkeypatch, ProduceResult({}, 0, StopReason.CAPTCHA))

    _, loaded = _run(tmp_path)

    assert loaded.captcha_blocked_until == _NOW + timedelta(hours=2)


def test_malformed_captcha_cooldown_uses_config_pause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(
        monkeypatch,
        ProduceResult({}, 0, StopReason.CAPTCHA, blocked_until="not-a-date"),
    )

    _, loaded = _run(tmp_path)

    assert loaded.captcha_blocked_until == _NOW + timedelta(hours=2)


def test_naive_captcha_cooldown_is_treated_as_utc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(
        monkeypatch,
        ProduceResult({}, 0, StopReason.CAPTCHA, blocked_until="2026-05-01T13:00:00"),
    )

    _, loaded = _run(tmp_path)

    assert loaded.captcha_blocked_until == _NOW + timedelta(hours=1)
    assert loaded.captcha_blocked_until is not None
    assert loaded.captcha_blocked_until.utcoffset() == timedelta(0)


def test_third_captcha_uses_long_pause_and_alerts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.CAPTCHA))
    state = _empty_state(captcha_streak=2)

    code, loaded = _run(tmp_path, state=state)

    assert code == "captcha"
    assert alerts.kinds == [AlertKind.CAPTCHA_STREAK]
    assert loaded.captcha_streak == 3
    assert loaded.captcha_blocked_until == _NOW + timedelta(hours=6)
    assert loaded.last_alert_kind == "captcha_streak"
    assert loaded.last_alert_at == _NOW


def test_second_captcha_does_not_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.CAPTCHA))
    state = _empty_state(captcha_streak=1)

    _, loaded = _run(tmp_path, state=state)

    assert alerts.kinds == []
    assert loaded.captcha_streak == 2


def test_zero_production_increments_counter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(monkeypatch, ProduceResult({}, 0, StopReason.QUERIES_EXHAUSTED))

    code, loaded = _run(tmp_path)

    assert code == "no_production"
    assert loaded.zero_runs == 1
    assert loaded.last_production_at == _NOW


def test_third_zero_production_alerts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.QUERIES_EXHAUSTED))
    state = _empty_state(zero_runs=2)

    code, loaded = _run(tmp_path, state=state)

    assert code == "no_production"
    assert alerts.kinds == [AlertKind.ZERO_PRODUCTION]
    assert loaded.zero_runs == 3
    assert loaded.last_alert_kind == "zero_production"


def test_success_resets_captcha_and_zero_counters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(monkeypatch, ProduceResult({"ru": 1}, 1, StopReason.TARGET_REACHED))
    state = _empty_state(
        captcha_streak=1,
        captcha_blocked_until=_NOW - timedelta(hours=1),
        zero_runs=2,
    )

    _, loaded = _run(tmp_path, state=state)

    assert loaded.captcha_streak == 0
    assert loaded.captcha_blocked_until is None
    assert loaded.zero_runs == 0


def test_no_queries_result_alerts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.NO_QUERIES))

    code, _ = _run(tmp_path)

    assert code == "no_production"
    assert alerts.kinds == [AlertKind.NO_QUERIES]


def test_no_progress_result_alerts_all_downloads_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.NO_PROGRESS))

    code, _ = _run(tmp_path)

    assert code == "no_production"
    assert alerts.kinds == [AlertKind.ALL_DOWNLOADS_FAILED]


def test_no_queries_takes_priority_over_zero_production_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.NO_QUERIES))
    state = _empty_state(zero_runs=2)

    _, loaded = _run(tmp_path, state=state)

    assert alerts.kinds == [AlertKind.NO_QUERIES]
    assert loaded.zero_runs == 3
    assert loaded.last_alert_kind == "no_queries"


def test_no_progress_takes_priority_over_zero_production_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, ProduceResult({}, 0, StopReason.NO_PROGRESS))
    state = _empty_state(zero_runs=2)

    _, loaded = _run(tmp_path, state=state)

    assert alerts.kinds == [AlertKind.ALL_DOWNLOADS_FAILED]
    assert loaded.zero_runs == 3
    assert loaded.last_alert_kind == "all_downloads_failed"


def test_supplier_exception_alerts_and_moves_last_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(monkeypatch, SupplierError("BitBrowser недоступен"))

    code, loaded = _run(tmp_path)

    assert code == "supplier_error"
    assert loaded.last_production_at == _NOW


def test_supplier_exception_sends_bitbrowser_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, SupplierError("MCP call failed: open_profile"))

    _, loaded = _run(tmp_path)

    assert alerts.kinds == [AlertKind.BITBROWSER_UNAVAILABLE]
    assert loaded.last_alert_kind == "bitbrowser_unavailable"


def test_storage_exception_sends_storage_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, SupplierError("не удалось создать каталог /data"))

    _run(tmp_path)

    assert alerts.kinds == [AlertKind.STORAGE_ERROR]


def test_supplier_settings_failure_does_not_move_last_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    produce, _ = _patch(monkeypatch, ProduceResult({}, 0, StopReason.ENOUGH_STOCK))

    def broken_settings() -> object:
        raise ConfigError("SUPPLIER_PROXY не задан")

    monkeypatch.setattr(stock_flow, "load_supplier_settings", broken_settings)
    alerts = _Alerts()
    monkeypatch.setattr(stock_flow, "send_critical", alerts)

    code, loaded = _run(tmp_path)

    assert code == "supplier_error"
    assert produce.calls == []
    assert alerts.kinds == [AlertKind.SUPPLIER_CRASH]
    assert loaded.last_production_at is None


def test_generic_exception_sends_only_crash_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, alerts = _patch(monkeypatch, RuntimeError("boom"))

    code, loaded = _run(tmp_path)

    assert code == "supplier_error"
    assert alerts.kinds == [AlertKind.SUPPLIER_CRASH]
    assert loaded.last_production_at == _NOW


def test_suppressed_alert_keeps_state_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch(monkeypatch, ProduceResult({}, 0, StopReason.CAPTCHA))
    monkeypatch.setattr(stock_flow, "send_critical", _SuppressedAlerts())
    state = _empty_state(captcha_streak=2)

    _, loaded = _run(tmp_path, state=state)

    assert loaded.captcha_streak == 3
    assert loaded.last_alert_kind is None
    assert loaded.last_alert_at is None


def test_flow_entrypoint_is_callable() -> None:
    assert callable(stock_topup)


def test_flow_entrypoint_returns_enough_stock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    config = tmp_path / "config" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """
languages:
  ru:
    enabled: true
    stock_target: 0
  en:
    enabled: false
    stock_target: 0
""",
        encoding="utf-8",
    )

    assert stock_flow.stock_topup.fn() == "enough_stock"
