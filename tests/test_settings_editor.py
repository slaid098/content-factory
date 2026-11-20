from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from content_factory.config.runtime import load_runtime_config
from content_factory.exceptions import ConfigError, SettingsValidationError, SettingsWriteError
from content_factory.services.settings import apply_settings, preview_settings, read_settings
from content_factory.services.settings import document as document_module
from settings_helpers import config_path, settings_payload, write_config

_BACKUP_RE = re.compile(r"^config\.yaml\.bak-\d{8}-\d{6}(?:-\d+)?$")


def _seed(data_dir: Path) -> Path:
    path = config_path(data_dir)
    load_runtime_config(path)
    return path


def _partial_without_delivery() -> str:
    return """\
# Настройки производства.
run:
  batch: 20
  interval: "6h"
  max_duration: "45m"
  pause_between_languages: "5m"

search:
  max_queries: 30
  pause: "45s"

filter:
  max: 60
  pause: "20s"

download:
  workers: 2
  pause: "10s"

languages:
  ru:
    enabled: true
    stock_target: 150
  en:
    enabled: false
    stock_target: 100
"""


def test_read_settings_seeds_template_and_returns_defaults(tmp_path: Path) -> None:
    result = read_settings(tmp_path)

    path = config_path(tmp_path)
    assert result == settings_payload()
    assert path.is_file()
    assert "#" in path.read_text(encoding="utf-8")


def test_read_settings_keeps_source_duration_strings(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
run:
  interval: "90m"
  max_duration: "45m"
languages:
  ru:
    enabled: true
    stock_target: 150
  en:
    enabled: false
    stock_target: 100
""",
    )

    result = read_settings(tmp_path)

    assert result.run.interval == "90m"
    assert result.run.max_duration == "45m"


def test_read_settings_rejects_broken_yaml(tmp_path: Path) -> None:
    write_config(tmp_path, "run: [\n")

    with pytest.raises(ConfigError, match="YAML"):
        read_settings(tmp_path)


def test_preview_reports_change_without_writing(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    result = preview_settings(tmp_path, settings_payload(run={"batch": 25}))

    assert result.changed is True
    assert result.settings.run.batch == 25
    assert "--- config.yaml\n+++ config.yaml\n" in result.diff
    assert "-  batch: 20" in result.diff
    assert "+  batch: 25" in result.diff
    assert path.read_text(encoding="utf-8") == original


def test_preview_without_changes_is_empty(tmp_path: Path) -> None:
    _seed(tmp_path)

    result = preview_settings(tmp_path, read_settings(tmp_path))

    assert result.changed is False
    assert result.diff == ""


def test_preview_rejects_invalid_max_duration_relation(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
run:
  max_duration: "3h"
  pause_between_languages: "2h"
languages:
  ru:
    enabled: true
    stock_target: 150
  en:
    enabled: false
    stock_target: 100
""",
    )
    original = path.read_text(encoding="utf-8")

    with pytest.raises(SettingsValidationError, match="pause_between_languages"):
        preview_settings(tmp_path, settings_payload())

    assert path.read_text(encoding="utf-8") == original


def test_apply_writes_backup_and_keeps_comments(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    result = apply_settings(
        tmp_path,
        settings_payload(run={"batch": 25}, delivery={"max_count": 120}),
    )

    assert result.changed is True
    assert result.backup is not None
    assert _BACKUP_RE.fullmatch(result.backup)
    backup = path.with_name(result.backup)
    assert backup.read_text(encoding="utf-8") == original
    updated = path.read_text(encoding="utf-8")
    assert "  batch: 25" in updated
    assert "  max_count: 120" in updated
    assert "# Максимальный объём производства за один тик, видео." in updated
    assert load_runtime_config(path).run.batch == 25
    assert load_runtime_config(path).delivery.max_count == 120


def test_apply_without_changes_creates_no_backup(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    result = apply_settings(tmp_path, read_settings(tmp_path))

    assert result.changed is False
    assert result.backup is None
    assert result.diff == ""
    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("config.yaml.bak-*")) == []


def test_apply_keeps_quote_style_when_changing_duration(tmp_path: Path) -> None:
    path = _seed(tmp_path)

    apply_settings(tmp_path, settings_payload(run={"interval": "4h"}))

    assert '  interval: "4h"' in path.read_text(encoding="utf-8")


def test_apply_adds_missing_delivery_section(tmp_path: Path) -> None:
    path = write_config(tmp_path, _partial_without_delivery())

    result = apply_settings(tmp_path, settings_payload())

    assert result.changed is True
    assert result.backup is not None
    text = path.read_text(encoding="utf-8")
    assert "delivery:" in text
    assert "max_count: 100" in text
    assert load_runtime_config(path).delivery.max_count == 100


def test_apply_keeps_unedited_sections_and_comments(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    original = load_runtime_config(path)

    apply_settings(tmp_path, settings_payload(download={"workers": 1}))

    updated = load_runtime_config(path)
    assert updated.download.workers == 1
    assert updated.search == original.search
    assert updated.captcha == original.captcha
    assert updated.alerts == original.alerts
    assert "# Сколько видео скачивать параллельно." in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "languages",
    [
        [
            {"code": "ru", "enabled": True, "stock_target": 150},
            {"code": "de", "enabled": True, "stock_target": 100},
        ],
        [{"code": "ru", "enabled": True, "stock_target": 150}],
        [
            {"code": "ru", "enabled": True, "stock_target": 150},
            {"code": "ru", "enabled": False, "stock_target": 100},
        ],
    ],
)
def test_apply_rejects_language_changes(
    tmp_path: Path,
    languages: list[dict[str, object]],
) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    with pytest.raises(SettingsValidationError):
        apply_settings(tmp_path, settings_payload(languages=languages))

    assert path.read_text(encoding="utf-8") == original


def test_apply_rejects_unknown_key_in_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "unknown: 1\n")

    with pytest.raises(SettingsValidationError, match="unknown"):
        apply_settings(tmp_path, settings_payload())

    assert path.read_text(encoding="utf-8") == "unknown: 1\n"


def test_apply_rejects_broken_yaml(tmp_path: Path) -> None:
    path = write_config(tmp_path, "run: [\n")

    with pytest.raises(SettingsValidationError, match="YAML"):
        apply_settings(tmp_path, settings_payload())

    assert path.read_text(encoding="utf-8") == "run: [\n"


def test_apply_replace_failure_keeps_old_file_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    def fail(self: Path, target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(SettingsWriteError, match="не удалось записать"):
        apply_settings(tmp_path, settings_payload(run={"batch": 25}))

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("*.tmp")) == []
    assert list(path.parent.glob("config.yaml.bak-*")) == []


def test_apply_temp_failure_removes_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> Path:
        raise SettingsWriteError("temp failed")

    monkeypatch.setattr(document_module, "_write_temporary", fail)

    with pytest.raises(SettingsWriteError, match="temp failed"):
        apply_settings(tmp_path, settings_payload(run={"batch": 25}))

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("config.yaml.bak-*")) == []


def test_apply_backup_failure_keeps_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> Path:
        raise SettingsWriteError("backup failed")

    monkeypatch.setattr(document_module, "_create_backup", fail)

    with pytest.raises(SettingsWriteError, match="backup failed"):
        apply_settings(tmp_path, settings_payload(run={"batch": 25}))

    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("*.tmp")) == []


def test_apply_backup_name_collision_uses_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _seed(tmp_path)

    class FrozenDatetime:
        @classmethod
        def now(cls) -> object:
            class Moment:
                @staticmethod
                def strftime(format: str) -> str:
                    return "20260501-120000"

            return Moment()

    monkeypatch.setattr(document_module, "datetime", FrozenDatetime)
    collision = path.with_name("config.yaml.bak-20260501-120000")
    collision.write_text("old", encoding="utf-8")

    result = apply_settings(tmp_path, settings_payload(run={"batch": 25}))

    assert result.backup == "config.yaml.bak-20260501-120000-1"
    assert collision.read_text(encoding="utf-8") == "old"


def test_apply_empty_file_writes_every_editable_key(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")

    result = apply_settings(tmp_path, settings_payload())

    assert result.changed is True
    text = path.read_text(encoding="utf-8")
    for key in ("batch", "max_queries", "max_count", "stock_target", "workers"):
        assert key in text


def test_concurrent_applies_back_up_each_version(tmp_path: Path) -> None:
    path = _seed(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda batch: apply_settings(tmp_path, settings_payload(run={"batch": batch})),
                (30, 40),
            )
        )

    assert all(result.changed for result in results)
    assert load_runtime_config(path).run.batch in {30, 40}
    assert len(list(path.parent.glob("config.yaml.bak-*"))) == 2
