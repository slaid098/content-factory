"""Profile service: partial refill updates and broken storage handling."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.unit.common.profile_helpers import (
    add_profile,
    add_profile_photo,
    add_profile_video,
    set_profile_refill,
)

from content_factory.constants import LANGUAGE_LABELS
from content_factory.db.errors import OperationalError
from content_factory.exceptions.errors import (
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.database.port import Connection
from content_factory.schemas.profiles.api import (
    LanguageOption,
    ProfileResponse,
    ProfileUpdateRequest,
)
from content_factory.schemas.profiles.preview import ProfilePreview
from content_factory.services.profiles import service as profiles_service
from content_factory.services.profiles.registry import get_profile

_GLOBAL = GeneratorConfig(target=20, refill_at=2)
_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _update(
    data_dir: Path,
    payload: ProfileUpdateRequest,
) -> ProfileResponse:
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


def test_list_profile_views_filters_by_language(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1", language="de")
    add_profile(tmp_path, "dating", "profile_2", language="en")
    add_profile(tmp_path, "auto", "profile_3", language="de", niche="auto")

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        language="de",
    )

    assert [view.id for view in views] == ["auto/profile_3", "dating/profile_1"]
    assert all(view.language_label == "Немецкий" for view in views)


def test_list_profile_views_filters_by_niche(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1", niche="dating")
    add_profile(tmp_path, "dating", "profile_2", niche="overlay")
    add_profile(tmp_path, "auto", "profile_3", niche="dating")

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        niche="dating",
    )

    assert [view.id for view in views] == ["auto/profile_3", "dating/profile_1"]


def test_list_profile_views_combines_language_and_niche(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1", language="de", niche="dating")
    add_profile(tmp_path, "dating", "profile_2", language="de", niche="overlay")
    add_profile(tmp_path, "auto", "profile_3", language="en", niche="dating")

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        language="de",
        niche="dating",
    )

    assert [view.id for view in views] == ["dating/profile_1"]


def test_list_profile_views_returns_empty_for_an_unknown_language(tmp_path: Path) -> None:
    add_profile(tmp_path, language="de")

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        language="ka",
    )

    assert views == ()


def test_list_profile_views_counts_stock_only_for_matching_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path, "dating", "profile_1", language="de")
    add_profile(tmp_path, "dating", "profile_2", language="en")
    counted: list[str] = []

    def _count(
        _connection: Connection,
        profile_id: str,
        **_kwargs: object,
    ) -> int:
        counted.append(profile_id)
        return 0

    monkeypatch.setattr(profiles_service, "count_by_profile", _count)

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        language="de",
    )

    assert [view.id for view in views] == ["dating/profile_1"]
    assert counted and set(counted) == {"dating/profile_1"}


def test_list_profiles_response_counts_before_filter(tmp_path: Path) -> None:
    add_profile(tmp_path, "dating", "profile_1", language="de", niche="dating")
    add_profile(tmp_path, "dating", "profile_2", language="en", niche="overlay")
    add_profile(tmp_path, "auto", "profile_3", language="en", niche="dating")

    response = profiles_service.list_profiles_response(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
        language="de",
        niche="dating",
    )

    assert [profile.id for profile in response.profiles] == ["dating/profile_1"]
    assert response.total == 3
    assert response.niches == ("dating", "overlay")
    assert len(response.languages) == len(LANGUAGE_LABELS)


def test_list_profiles_response_adds_an_unknown_profile_language(tmp_path: Path) -> None:
    add_profile(tmp_path, language="kaz")

    response = profiles_service.list_profiles_response(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert response.profiles[0].language_label == "KAZ"
    assert LanguageOption(code="kaz", label="KAZ") in response.languages
    codes = [option.code for option in response.languages]
    assert len(set(codes)) == len(codes)


def test_list_profiles_response_without_profiles_returns_the_catalog(
    tmp_path: Path,
) -> None:
    response = profiles_service.list_profiles_response(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert response.profiles == ()
    assert response.total == 0
    assert response.niches == ()
    assert len(response.languages) == len(LANGUAGE_LABELS)


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
    assert views[0].photo_stock == 0
    assert views[0].target == 20
    assert views[0].refill_enabled is False


def test_counters_fall_back_when_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)

    def _fail(_connection: Connection, _profile_id: str, **_kwargs: object) -> int:
        raise OperationalError("table is locked")

    monkeypatch.setattr(profiles_service, "count_by_profile", _fail)

    views = profiles_service.list_profile_views(
        tmp_path,
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert views[0].stock == 0
    assert views[0].photo_stock == 0


def test_preview_for_profile_delegates_to_preview_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(tmp_path)
    seen: list[str] = []
    expected = ProfilePreview(tmp_path / "preview.jpg", "image/jpeg")

    def _preview(_data_dir: Path, profile: object) -> ProfilePreview:
        seen.append(str(profile))
        return expected

    monkeypatch.setattr(profiles_service, "profile_preview", _preview)

    assert profiles_service.preview_for_profile(tmp_path, "dating/profile_1") == expected
    assert seen == [str(get_profile(tmp_path, "dating/profile_1"))]


def test_get_profile_view_counts_video_and_photo_stock(tmp_path: Path) -> None:
    add_profile(tmp_path)
    add_profile_video(tmp_path, filename="dating_profile_1_001.mp4")
    add_profile_video(tmp_path, filename="dating_profile_1_002.mp4")
    add_profile_photo(tmp_path)
    set_profile_refill(
        tmp_path,
        "dating/profile_1",
        enabled=True,
        target=7,
        refill_at=3,
        photo_target=9,
        photo_refill_at=4,
    )

    view = profiles_service.get_profile_view(
        tmp_path,
        "dating/profile_1",
        output_dir=tmp_path / "generated",
        global_config=_GLOBAL,
    )

    assert view.stock == 2
    assert view.photo_stock == 1
    assert view.target == 7
    assert view.refill_at == 3
    assert view.target_override == 7
    assert view.refill_at_override == 3
    assert view.photo_target == 9
    assert view.photo_refill_at == 4
    assert view.refill_enabled is True


def test_update_stores_the_photo_pair(tmp_path: Path) -> None:
    add_profile(tmp_path)

    view = _update(tmp_path, ProfileUpdateRequest(photo_target=10, photo_refill_at=4))

    assert view.photo_target == 10
    assert view.photo_refill_at == 4
    assert view.target == 20
    assert view.refill_at == 2


@pytest.mark.parametrize(
    "payload",
    [
        ProfileUpdateRequest(photo_target=0, photo_refill_at=1),
        ProfileUpdateRequest(photo_target=5, photo_refill_at=5),
        ProfileUpdateRequest(photo_target=5, photo_refill_at=6),
    ],
)
def test_update_rejects_an_invalid_photo_pair(
    tmp_path: Path,
    payload: ProfileUpdateRequest,
) -> None:
    add_profile(tmp_path)

    with pytest.raises(ProfileValidationError, match="photo_"):
        _update(tmp_path, payload)
