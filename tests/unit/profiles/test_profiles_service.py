"""Profile service: partial refill updates and broken storage handling."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.common.profile_helpers import add_profile, add_profile_video, set_profile_refill

from content_factory.db.errors import OperationalError
from content_factory.exceptions.errors import (
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.database.port import Connection
from content_factory.schemas.profiles.api import ProfileUpdateRequest
from content_factory.services.profiles import service as profiles_service
from content_factory.services.profiles.registry import get_profile

_GLOBAL = GeneratorConfig(target=20, refill_at=2)
_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _update(
    data_dir: Path,
    payload: ProfileUpdateRequest,
) -> object:
    return profiles_service.update_profile_settings(
        data_dir,
        "dating/profile_1",
        payload,
        output_dir=data_dir / "generated",
        global_config=_GLOBAL,
        now=_MOMENT,
    )


def test_list_profile_views_without_profiles_is_empty(tmp_path: Path) -> None:
    assert (
        profiles_service.list_profile_views(
            tmp_path,
            output_dir=tmp_path / "generated",
            global_config=_GLOBAL,
        )
        == ()
    )


def test_update_rejects_oversized_stored_target(tmp_path: Path) -> None:
    add_profile(tmp_path)
    set_profile_refill(tmp_path, "dating/profile_1", enabled=True, target=2000, refill_at=1)

    with pytest.raises(ProfileValidationError, match="target"):
        _update(tmp_path, ProfileUpdateRequest(refill_enabled=True))


def test_read_error_maps_to_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)

    def _fail(_connection: Connection, _profile_id: str) -> object:
        raise OperationalError("schema is locked")

    monkeypatch.setattr(profiles_service, "get_refill", _fail)

    with pytest.raises(ProfileStorageError, match="прочитать"):
        _update(tmp_path, ProfileUpdateRequest(refill_enabled=True))


def test_write_connect_error_maps_to_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)
    original_connect = profiles_service.connect
    calls = 0

    def _connect(path: Path) -> Connection:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_connect(path)
        raise OperationalError("cannot open")

    monkeypatch.setattr(profiles_service, "connect", _connect)

    with pytest.raises(ProfileStorageError, match="сохранить"):
        _update(tmp_path, ProfileUpdateRequest(refill_enabled=True))


def test_write_error_maps_to_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)

    def _fail(*args: object, **kwargs: object) -> None:
        raise OperationalError("disk is full")

    monkeypatch.setattr(profiles_service, "upsert_refill", _fail)

    with pytest.raises(ProfileStorageError, match="сохранить"):
        _update(tmp_path, ProfileUpdateRequest(refill_enabled=True))


def test_counters_fall_back_when_connect_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)

    def _fail(_path: Path) -> Connection:
        raise OperationalError("cannot open")

    monkeypatch.setattr(profiles_service, "sync_stock", lambda _data_dir: 0)
    monkeypatch.setattr(profiles_service, "connect", _fail)

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert views[0].stock == 0
    assert views[0].target == 20
    assert views[0].refill_enabled is False


def test_counters_fall_back_when_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)

    def _fail(_connection: Connection, _profile_id: str) -> int:
        raise OperationalError("table is locked")

    monkeypatch.setattr(profiles_service, "count_by_profile", _fail)

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert views[0].stock == 0


def test_preview_for_profile_delegates_to_preview_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)
    seen: list[str] = []

    def _preview(_data_dir: Path, profile: object) -> Path:
        seen.append(str(profile))
        return tmp_path / "preview.jpg"

    monkeypatch.setattr(profiles_service, "profile_preview", _preview)

    assert profiles_service.preview_for_profile(tmp_path, "dating/profile_1") == (
        tmp_path / "preview.jpg"
    )
    assert seen == [str(get_profile(tmp_path, "dating/profile_1"))]


def test_get_profile_view_counts_stock_and_effective_settings(tmp_path: Path) -> None:
    add_profile(tmp_path)
    add_profile_video(tmp_path)
    set_profile_refill(tmp_path, "dating/profile_1", enabled=True, target=7, refill_at=3)

    view = profiles_service.get_profile_view(
        tmp_path,
        "dating/profile_1",
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert view.stock == 1
    assert view.target == 7
    assert view.refill_at == 3
    assert view.target_override == 7
    assert view.refill_at_override == 3
    assert view.refill_enabled is True
