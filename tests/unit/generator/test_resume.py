"""Resume of unfinished generator runs from the journal."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from tests.unit.generator.helpers import make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db import generator_runs, generator_servers
from content_factory.db.connection import connect
from content_factory.db.run_journal import RunJournalWriter
from content_factory.exceptions.generator import RentError
from content_factory.schemas.generator.journal import JournalPlanItem
from content_factory.schemas.generator.run import RunReport
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator import comfy, media, resume, service
from content_factory.services.production.card import CardProducer
from content_factory.utils.run_id import profile_run_dir

_RUN_ID = "20260101T000000Z"
_PROFILE = "dating/profile_2"
_SLUG = "dating_profile_2"
_FILE = f"{_SLUG}_{_RUN_ID}_001.mp4"
_PHOTO = f"{_SLUG}_{_RUN_ID}_001.jpg"
_AUDIO = "audio-track.mp3"
_NOW = "2026-01-01T00:00:00+00:00"


def _settings(tmp_path: Path) -> GeneratorSettings:
    return GeneratorSettings(output_dir=tmp_path / "generated", safety_enabled=False)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / DB_DIR / DB_FILENAME


def _journal(tmp_path: Path) -> RunJournalWriter:
    return RunJournalWriter(_db_path(tmp_path))


def _connection(tmp_path: Path):
    return connect(_db_path(tmp_path))


def _item(
    file: str,
    *,
    producer: str = "vast",
    type: str = "video",
    look_id: str | None = "c01",
    seed: int | None = 7,
    frames: int | None = 119,
) -> JournalPlanItem:
    return JournalPlanItem(
        profile=_PROFILE,
        file=file,
        producer=producer,
        type=type,
        look_id=look_id,
        seed=seed,
        frames=frames,
    )


def _start_run(
    tmp_path: Path,
    items: tuple[JournalPlanItem, ...] = (),
    *,
    content_type: str | None = "video",
    count: int = 1,
    profiles: tuple[str, ...] = (_PROFILE,),
) -> None:
    connection = _connection(tmp_path)
    try:
        generator_runs.insert_run(
            connection,
            run_id=_RUN_ID,
            profiles=profiles,
            count=count,
            content_type=content_type,
            now=_NOW,
        )
        if items:
            generator_runs.insert_plan(connection, _RUN_ID, items, _NOW)
        connection.commit()
    finally:
        connection.close()


def _ready_server(tmp_path: Path, *, contract_id: str = "c1") -> str:
    connection = _connection(tmp_path)
    try:
        generator_servers.insert_renting(
            connection,
            server_id="s1",
            run_id=_RUN_ID,
            profile=_PROFILE,
            gpu="L40S",
            image="image",
            cost_per_hour=0.5,
            now=_NOW,
        )
        generator_servers.bind_contract(connection, "s1", contract_id, _NOW)
        generator_servers.mark_ready(
            connection,
            "s1",
            ip="127.0.0.1",
            port=8188,
            gpu="L40S",
            image="image",
            cost_per_hour=0.5,
            now=_NOW,
        )
        connection.commit()
    finally:
        connection.close()
    return "s1"


def _run_row(tmp_path: Path) -> generator_runs.GeneratorRunRow:
    connection = _connection(tmp_path)
    try:
        row = generator_runs.get_run(connection, _RUN_ID)
    finally:
        connection.close()
    assert row is not None
    return row


def _item_rows(tmp_path: Path) -> list[generator_runs.GeneratorItemRow]:
    connection = _connection(tmp_path)
    try:
        return generator_runs.list_items(connection, _RUN_ID)
    finally:
        connection.close()


def _run_dir(tmp_path: Path) -> Path:
    return profile_run_dir(_settings(tmp_path).output_dir, "ru", _SLUG, _RUN_ID)


class _Provider:
    """In-memory Vast.ai account that never rents and records teardowns."""

    def __init__(self, instances: list[str] | None = None) -> None:
        self.instances = {contract: {"id": contract} for contract in instances or []}
        self.queries = 0
        self.destroyed: list[str] = []
        self.created: list[dict[str, Any]] = []

    def list_instances(self) -> list[dict[str, Any]]:
        self.queries += 1
        return [dict(instance) for instance in self.instances.values()]

    def create_instance(self, config: dict[str, Any]) -> str:
        self.created.append(dict(config))
        raise AssertionError("resume must not rent a second server")

    def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        del query
        raise AssertionError("resume must not rent a second server")

    def destroy_instance(self, contract_id: str) -> bool:
        self.destroyed.append(contract_id)
        self.instances.pop(contract_id, None)
        return True

    def show_instance(self, contract_id: str) -> dict[str, Any]:
        instance = self.instances.get(contract_id)
        if instance is None:
            raise RentError(f"Instance {contract_id} not found")
        return dict(instance)


class _Transport:
    """ComfyUI transport with a controllable history and queue."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.history_map: dict[str, dict[str, Any]] = {}
        self.queue_payload: dict[str, Any] = {}
        self.closed = False

    def submit(self, workflow: dict[str, Any]) -> str:
        prompt = f"prompt-{len(self.submitted) + 1}"
        self.submitted.append(prompt)
        prefix = str(workflow.get("15", {}).get("inputs", {}).get("filename_prefix") or prompt)
        self.history_map[prompt] = _history(f"{prefix}.mp4")
        return prompt

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        return self.history_map.get(prompt_id)

    def history_all(self) -> dict[str, Any]:
        return dict(self.history_map)

    def queue(self) -> dict[str, Any]:
        return dict(self.queue_payload)

    def system_stats(self) -> dict[str, Any]:
        return {"system": {}}

    def download(self, output: Any) -> bytes:
        del output
        return b"video"

    def close(self) -> None:
        self.closed = True


def _history(filename: str) -> dict[str, Any]:
    return {
        "outputs": {"15": {"gifs": [{"filename": filename, "subfolder": "", "type": "output"}]}}
    }


class _Session:
    """Rental fake that opens one ready server and records its teardown."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.opened = 0
        self.destroyed = 0

    def open(self) -> ServerInfo:
        self.opened += 1
        return ServerInfo(
            connection=ServerConnection(
                contract_id="c-rent",
                ip="127.0.0.1",
                port=8188,
                gpu="L40S",
                image="image",
            ),
            cost_per_hour=0.5,
        )

    def destroy(self) -> None:
        self.destroyed += 1


class _Writer:
    """Text model fake that counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    def write(self, system_prompt: str) -> str:
        del system_prompt
        self.calls += 1
        return "<result><block>Текст карточки</block><block>напиши мне</block></result>"


def _patch_media(monkeypatch: pytest.MonkeyPatch, audios: list[Path | None]) -> None:
    """Replace ffmpeg and ffprobe with fast local stand-ins."""

    def mix(profile: Any, card: Any, source: Path, output: Path, audio: Path | None) -> None:
        del profile, card, source
        audios.append(audio)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def check(path: Path, expected: float, expect_audio: bool = False) -> media._MediaInfo:
        del expected, expect_audio
        return media._MediaInfo(
            path=path,
            duration=4.96,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=path.stat().st_size,
        )

    monkeypatch.setattr(service.overlay, "mix", mix)
    monkeypatch.setattr(service.qa, "check_video", check)
    monkeypatch.setattr(comfy, "_store_video", lambda path, content: path.write_bytes(content))


def _resume(
    tmp_path: Path,
    *,
    provider: _Provider,
    client_factory: Any,
    writer: _Writer | None = None,
) -> list[resume.RunOutcome]:
    connection = _connection(tmp_path)
    try:
        return resume.resume_unfinished(
            connection,
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            provider=provider,  # type: ignore[arg-type]
            journal=_journal(tmp_path),
            client_factory=client_factory,
            text_writer=writer or _Writer(),
            rng=random.Random(1),
        )
    finally:
        connection.close()


def test_plan_is_rebuilt_and_the_live_server_finishes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path)
    _ready_server(tmp_path)
    transport = _Transport()
    provider = _Provider(["c1"])
    audios: list[Path | None] = []
    _patch_media(monkeypatch, audios)

    outcomes = _resume(
        tmp_path,
        provider=provider,
        client_factory=lambda connection: transport,
    )

    assert [(item.run_id, item.status, item.generated) for item in outcomes] == [
        (_RUN_ID, "done", 1)
    ]
    rows = _item_rows(tmp_path)
    assert [(row.file, row.status) for row in rows] == [(_FILE, "done")]
    assert rows[0].prompt_id == "prompt-1"
    assert rows[0].source_path == str(_run_dir(tmp_path) / "source" / _FILE)
    assert rows[0].final_path == str(_run_dir(tmp_path) / "final" / _FILE)
    assert transport.submitted == ["prompt-1"]
    assert provider.created == []
    assert provider.destroyed == ["c1"]
    assert _run_row(tmp_path).status == "done"


def test_prompt_found_in_history_is_downloaded_without_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    _ready_server(tmp_path)
    transport = _Transport()
    transport.history_map["p1"] = _history(_FILE)
    provider = _Provider(["c1"])
    _patch_media(monkeypatch, [])

    outcomes = _resume(
        tmp_path,
        provider=provider,
        client_factory=lambda connection: transport,
    )

    assert outcomes[0].status == "done"
    assert transport.submitted == []
    rows = _item_rows(tmp_path)
    assert rows[0].prompt_id == "p1"
    assert rows[0].status == "done"
    assert Path(rows[0].source_path or "").is_file()


def test_lost_prompt_is_resubmitted_with_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    _ready_server(tmp_path)
    _journal(tmp_path).submitted(_RUN_ID, _PROFILE, _FILE, "gone", 2, "c1")
    transport = _Transport()
    provider = _Provider(["c1"])
    _patch_media(monkeypatch, [])

    outcomes = _resume(
        tmp_path,
        provider=provider,
        client_factory=lambda connection: transport,
    )

    assert outcomes[0].status == "done"
    assert transport.submitted == ["prompt-1"]
    row = _item_rows(tmp_path)[0]
    assert row.status == "done"
    assert row.attempt == 3


def test_missing_downloaded_source_is_requeued_and_found_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    source = _run_dir(tmp_path) / "source" / _FILE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _journal(tmp_path).downloaded(_RUN_ID, _PROFILE, _FILE, str(source))
    source.unlink()
    _ready_server(tmp_path)
    transport = _Transport()
    transport.history_map["p1"] = _history(_FILE)
    _patch_media(monkeypatch, [])

    outcomes = _resume(
        tmp_path,
        provider=_Provider(["c1"]),
        client_factory=lambda connection: transport,
    )

    assert outcomes[0].status == "done"
    assert transport.submitted == []
    row = _item_rows(tmp_path)[0]
    assert row.status == "done"
    assert row.source_path == str(source)
    assert source.is_file()


def test_run_without_a_server_rents_one_and_generates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    transport = _Transport()
    session = _Session()
    _patch_media(monkeypatch, [])

    def session_factory(*args: Any, **kwargs: Any) -> _Session:
        del args, kwargs
        return session

    monkeypatch.setattr(resume, "VastSession", session_factory)

    outcomes = _resume(
        tmp_path,
        provider=_Provider(),
        client_factory=lambda connection: transport,
    )

    assert outcomes[0].status == "done"
    assert transport.submitted == ["prompt-1"]
    assert (session.opened, session.destroyed) == (1, 1)
    assert _item_rows(tmp_path)[0].status == "done"


def test_downloaded_source_skips_the_server_and_finishes_postprocessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    source = _run_dir(tmp_path) / "source" / _FILE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _journal(tmp_path).downloaded(_RUN_ID, _PROFILE, _FILE, str(source))
    factory_calls: list[Any] = []
    _patch_media(monkeypatch, [])

    def factory(connection: Any) -> _Transport:
        factory_calls.append(connection)
        raise AssertionError("a downloaded source needs no ComfyUI call")

    outcomes = _resume(tmp_path, provider=_Provider(), client_factory=factory)

    assert outcomes[0].status == "done", _item_rows(tmp_path)[0].error
    assert factory_calls == []
    assert source.read_bytes() == b"source"
    assert (_run_dir(tmp_path) / "final" / _FILE).is_file()
    assert _item_rows(tmp_path)[0].status == "done"


def test_published_item_is_reconciled_and_the_cursor_does_not_move_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE, look_id="c01"),))
    run_dir = _run_dir(tmp_path)
    source = run_dir / "source" / _FILE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    _journal(tmp_path).downloaded(_RUN_ID, _PROFILE, _FILE, str(source))
    final = run_dir / "final" / _FILE
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final")
    _journal(tmp_path).postprocessing(_RUN_ID, _PROFILE, _FILE, str(final))
    text = run_dir / "text" / f"{Path(_FILE).stem}.json"
    text.parent.mkdir(parents=True)
    text.write_text(
        json.dumps({"blocks": [{"lines": ["Текст"]}, {"lines": ["напиши мне"]}]}),
        encoding="utf-8",
    )
    audio_dir = tmp_path / "profiles" / _PROFILE / "audio"
    audio = audio_dir / _AUDIO
    audio.write_bytes(b"music")
    (run_dir / "text" / f"{Path(_FILE).stem}.audio").write_text(f"{audio}\n", encoding="utf-8")
    stock = tmp_path / "content" / "dating" / "ru" / _FILE
    stock.parent.mkdir(parents=True)
    stock.write_bytes(b"final")
    cursor = _settings(tmp_path).output_dir / "ru" / _SLUG / "rotation.json"
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text(
        json.dumps({"last_look_id": "c02", "run_id": _RUN_ID, "updated_at": _NOW}),
        encoding="utf-8",
    )
    writer = _Writer()
    audios: list[Path | None] = []
    _patch_media(monkeypatch, audios)

    outcomes = _resume(
        tmp_path,
        provider=_Provider(),
        client_factory=lambda connection: (_ for _ in ()).throw(AssertionError("no network")),
        writer=writer,
    )

    assert outcomes[0].status == "done"
    assert writer.calls == 0
    assert audios == [audio]
    assert stock.read_bytes() == b"final"
    assert json.loads(cursor.read_text(encoding="utf-8"))["last_look_id"] == "c02"
    assert _item_rows(tmp_path)[0].status == "done"


def test_lost_server_fails_the_run_without_renting_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _start_run(tmp_path, (_item(_FILE),))
    _ready_server(tmp_path)
    provider = _Provider()

    outcomes = _resume(
        tmp_path,
        provider=provider,
        client_factory=lambda connection: (_ for _ in ()).throw(AssertionError("no network")),
    )

    assert [(item.status, item.failed) for item in outcomes] == [("failed", 1)]
    assert provider.created == []
    row = _item_rows(tmp_path)[0]
    assert row.status == "failed"
    assert "исчез" in (row.error or "")
    assert _run_row(tmp_path).status == "failed"
    connection = _connection(tmp_path)
    try:
        server = generator_servers.get_row(connection, "s1")
    finally:
        connection.close()
    assert server is not None
    assert server.status == generator_servers.LOST


def test_photo_pair_is_completed_after_a_restart_between_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(
        tmp_path / "profiles",
        width=544,
        height=960,
        extra={"production": [{"producer": "card", "type": "photo"}]},
    )
    _start_run(
        tmp_path,
        (_item(_PHOTO, producer="card", type="photo", look_id=None, seed=None, frames=None),),
        content_type="photo",
    )
    run_dir = _run_dir(tmp_path)
    final = run_dir / "final" / _PHOTO
    final.parent.mkdir(parents=True)
    Image.new("RGB", (544, 960), "white").save(final, format="JPEG")
    sidecar = final.with_suffix(".json")
    caption = json.dumps({"text": "Текст поста"}, ensure_ascii=False, indent=2) + "\n"
    sidecar.write_text(caption, encoding="utf-8")
    saved = run_dir / "text" / f"{Path(_PHOTO).stem}.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(
        json.dumps({"blocks": [{"lines": ["Текст поста"]}]}),
        encoding="utf-8",
    )
    published_text = tmp_path / "content" / "dating" / "ru" / f"{Path(_PHOTO).stem}.json"
    published_text.parent.mkdir(parents=True)
    published_text.write_text(caption, encoding="utf-8")
    _journal(tmp_path).postprocessing(_RUN_ID, _PROFILE, _PHOTO, str(final))

    def factory(*, writer: Any = None) -> CardProducer:
        return CardProducer(
            writer=writer or _Writer(),
            renderer=lambda profile, overlay: Image.new(
                "RGBA", (profile.width, profile.height), (0, 0, 0, 0)
            ),
        )

    monkeypatch.setattr(resume, "CardProducer", factory)
    provider = _Provider()

    outcomes = _resume(
        tmp_path,
        provider=provider,
        client_factory=lambda connection: (_ for _ in ()).throw(AssertionError("no network")),
    )

    assert outcomes[0].status == "done"
    published = tmp_path / "content" / "dating" / "ru" / _PHOTO
    assert published.is_file()
    assert published_text.read_text(encoding="utf-8") == caption
    assert sorted(path.name for path in published.parent.iterdir()) == [
        _PHOTO,
        f"{Path(_PHOTO).stem}.json",
    ]
    assert _item_rows(tmp_path)[0].status == "done"
    assert provider.queries == 0


def test_rebuilt_report_keeps_every_item_and_the_usual_run_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    failed_file = f"{_SLUG}_{_RUN_ID}_002.mp4"
    _start_run(tmp_path, (_item(_FILE), _item(failed_file, look_id="c02")))
    run_dir = _run_dir(tmp_path)
    final = run_dir / "final" / _FILE
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final")
    journal = _journal(tmp_path)
    journal.postprocessing(_RUN_ID, _PROFILE, _FILE, str(final))
    journal.published(_RUN_ID, _PROFILE, _FILE)
    journal.failed(_RUN_ID, _PROFILE, failed_file, "нет результата")
    _patch_media(monkeypatch, [])

    outcomes = _resume(
        tmp_path,
        provider=_Provider(),
        client_factory=lambda connection: (_ for _ in ()).throw(AssertionError("no network")),
    )

    assert outcomes[0].status == "failed"
    report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert set(report) == set(RunReport.model_fields)
    assert report["run_id"] == _RUN_ID
    assert report["count"] == 1
    assert [item["file"] for item in report["items"]] == [_FILE, failed_file]
    assert [item["status"] for item in report["items"]] == ["done", "failed"]
    assert report["totals"] == {"generated": 1, "failed": 1, "bytes": 5}
    assert report["finished_at"] is not None
