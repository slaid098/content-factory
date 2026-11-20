from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.services.supplier import metadata

FIXTURE = Path(__file__).parent / "fixtures" / "tiktok_video_page.json"


def _page(data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return (
        "<html><body>"
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        f"{payload}"
        "</script></body></html>"
    )


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_video_page() -> None:
    result = metadata.parse_video_page(_page(_fixture()), url="https://tiktok.test/video/1")
    assert result.video_id == "7633805945545477398"
    assert result.url == "https://tiktok.test/video/1"
    assert result.author_id == "nastik55591"
    assert result.author_name == "Nastik555"
    assert result.description == "#кухня #реки #tallinn"
    assert result.views == 209600
    assert result.followers == 673
    assert result.duration == 99
    assert result.is_aigc is False
    assert result.aigc_description == ""
    assert result.aigc_label == ""
    assert result.moderation_aigc_label == ""
    assert result.width == 1080
    assert result.height == 1920
    assert result.create_time is None


def test_parse_aigc_video() -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item["IsAigc"] = True
    item["AIGCDescription"] = "AI generated"
    result = metadata.parse_video_page(_page(data))
    assert result.is_aigc is True
    assert result.aigc_description == "AI generated"


@pytest.mark.parametrize(
    ("field", "attribute"),
    [
        ("aigcLabelType", "aigc_label"),
        ("moderationAigcLabelType", "moderation_aigc_label"),
    ],
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        (" ", ""),
        (0, ""),
        ("0", ""),
        (1, "1"),
        ("1", "1"),
        (" label ", "label"),
    ],
)
def test_parse_aigc_label_coercion(
    field: str,
    attribute: str,
    value: object,
    expected: str,
) -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item[field] = value

    result = metadata.parse_video_page(_page(data))

    assert getattr(result, attribute) == expected


@pytest.mark.parametrize(
    ("field", "attribute"),
    [
        ("aigcLabelType", "aigc_label"),
        ("moderationAigcLabelType", "moderation_aigc_label"),
    ],
)
def test_parse_aigc_label_is_independent(field: str, attribute: str) -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item[field] = "1"

    result = metadata.parse_video_page(_page(data))

    assert getattr(result, attribute) == "1"
    other = "moderation_aigc_label" if attribute == "aigc_label" else "aigc_label"
    assert getattr(result, other) == ""


def test_parse_missing_aigc_labels() -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    del item["aigcLabelType"]
    del item["moderationAigcLabelType"]

    result = metadata.parse_video_page(_page(data))

    assert result.aigc_label == ""
    assert result.moderation_aigc_label == ""


def test_parse_create_time() -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item["createTime"] = 1_700_000_000
    result = metadata.parse_video_page(_page(data))
    assert result.create_time == 1_700_000_000


@pytest.mark.parametrize("value", ["soon", True, None, [], {}])
def test_parse_invalid_create_time(value: object) -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item["createTime"] = value
    result = metadata.parse_video_page(_page(data))
    assert result.create_time is None


def test_parse_missing_universal_data() -> None:
    with pytest.raises(SupplierError, match="universal data"):
        metadata.parse_video_page("<html></html>")


def test_parse_broken_json() -> None:
    html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{not-json}</script>'
    with pytest.raises(SupplierError, match="не является JSON"):
        metadata.parse_video_page(html)


def test_parse_missing_item_struct() -> None:
    data = {"__DEFAULT_SCOPE__": {"webapp.video-detail": {"itemInfo": {}}}}
    with pytest.raises(SupplierError, match="itemStruct"):
        metadata.parse_video_page(_page(data))


def test_fetch_video_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200
        text = _page(_fixture())

    calls: list[dict[str, object]] = []

    def fake_get(url: str, **kwargs: object) -> Response:
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(metadata.requests, "get", fake_get)
    result = metadata.fetch_video_metadata("https://tiktok.test/video/1", proxy="http://proxy:8080")
    assert result.video_id == "7633805945545477398"
    assert result.url == "https://tiktok.test/video/1"
    assert calls[0]["url"] == "https://tiktok.test/video/1"
    assert calls[0]["proxies"] == {"http": "http://proxy:8080", "https": "http://proxy:8080"}
    assert calls[0]["impersonate"] == "chrome"


def test_fetch_video_metadata_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200
        text = _page(_fixture())

    calls: list[dict[str, object]] = []

    def fake_get(url: str, **kwargs: object) -> Response:
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(metadata.requests, "get", fake_get)
    metadata.fetch_video_metadata("https://tiktok.test/video/1")
    assert calls[0]["proxies"] is None


def test_fetch_video_metadata_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(metadata.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(SupplierError, match="HTTP 404"):
        metadata.fetch_video_metadata("https://tiktok.test/video/1")


def test_fetch_video_metadata_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*args: object, **kwargs: object) -> object:
        raise OSError("connection failed")

    monkeypatch.setattr(metadata.requests, "get", fake_get)
    with pytest.raises(SupplierError, match="не удалось скачать"):
        metadata.fetch_video_metadata("https://tiktok.test/video/1")


def test_parse_allows_list_item_struct() -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"] = [item]
    result = metadata.parse_video_page(_page(data))
    assert result.video_id == "7633805945545477398"


def test_parse_rejects_non_object_json() -> None:
    html = '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">[]</script>'

    with pytest.raises(SupplierError, match="не является объектом"):
        metadata.parse_video_page(html)


def test_parse_rejects_item_without_video_id() -> None:
    data = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {
                "itemInfo": {
                    "itemStruct": {
                        "author": {"uniqueId": "alice"},
                        "video": {"duration": 10},
                    }
                }
            }
        }
    }

    with pytest.raises(SupplierError, match="id видео"):
        metadata.parse_video_page(_page(data))


def test_parse_coerces_numeric_fields() -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item["stats"]["playCount"] = 1.9
    item["video"]["duration"] = "12"
    item["authorStats"]["followerCount"] = True

    result = metadata.parse_video_page(_page(data))

    assert result.views == 1
    assert result.duration == 12
    assert result.followers is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("0", False), (1, True), (0, False), (True, True), (None, False)],
)
def test_parse_aigc_flag_coercion(value: object, expected: bool) -> None:
    data = _fixture()
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    item["IsAigc"] = value
    item["AIGCDescription"] = ""

    result = metadata.parse_video_page(_page(data))

    assert result.is_aigc is expected
