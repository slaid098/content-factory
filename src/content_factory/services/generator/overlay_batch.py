"""One batch of overlay clips: plan, rent one server, QA, pack.

The batch is deterministic: prompts give seeds and file names, so a rerun
after a crash regenerates only the missing clips. The rented server goes
through the common :class:`VastSession` and :func:`run_server`, therefore it
is written to the server journal and is destroyed and verified in ``finally``
even when generation fails.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.config.settings import GeneratorSettings, load_generator_settings
from content_factory.constants import DB_DIR, DB_FILENAME, GENERATED_DIR, PROFILES_DIR
from content_factory.db.server_journal import ServerJournal
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.overlay import OverlayPlan, OverlayPlanItem, OverlayRun
from content_factory.services.generator import comfy, media
from content_factory.services.generator.comfy import _ClientFactory, _ServerSession
from content_factory.services.generator.overlay_pack import package_overlays
from content_factory.services.generator.overlay_plan import (
    build_overlay_workflow,
    plan_overlay,
)
from content_factory.services.generator.overlay_profiles import (
    OverlayProfile,
    load_overlay_profile,
)
from content_factory.services.generator.overlay_quality import bright_share, rejection_reason
from content_factory.services.generator.plan import _PlannedItem, attempt_seed
from content_factory.services.generator.profiles import _Look
from content_factory.services.generator.vast import VastSession

_SessionFactory = Callable[[OverlayProfile, GeneratorSettings], _ServerSession]
_QA_ATTEMPTS = 3
_READY_TIMEOUT = 900
_REPORT = "run.json"


def _bounded_settings(settings: GeneratorSettings) -> GeneratorSettings:
    """Cap the readiness wait of one overlay server.

    A broken image can stay in ``loading`` for an hour while Vast.ai already
    charges for it. Overlays need neither a long pull nor a long boot, so the
    wait is capped; the health check is a hard 200 from ``/system_stats``.
    """
    return settings.model_copy(
        update={
            "server_ready_timeout": min(settings.server_ready_timeout, _READY_TIMEOUT),
            "comfy_health_timeout": min(settings.comfy_health_timeout, 300),
        }
    )


def generate_overlays(
    profile_name: str,
    *,
    data_dir: Path,
    dry_run: bool = False,
    force: bool = False,
    settings: GeneratorSettings | None = None,
    session_factory: _SessionFactory | None = None,
    client_factory: _ClientFactory | None = None,
) -> OverlayRun:
    """Generate, check and pack one overlay profile.

    Clips already on disk and passing density QA are reused, so a rerun does
    not pay for them twice. The server is rented only when at least one clip
    is missing. A clip that fails density QA is regenerated on the same
    server with a new seed; after :data:`_QA_ATTEMPTS` failures it is left out
    of the archive.

    Args:
        profile_name: Profile name, for example ``overlay/profile_17``.
        data_dir: Runtime data directory with ``profiles`` and ``generated``.
        dry_run: Build the plan and measure existing clips without any network.
        force: Ignore existing clips and regenerate the whole profile.
        settings: Generator settings; the environment is read when omitted.
        session_factory: Rental factory; tests inject a fake server.
        client_factory: ComfyUI client factory; tests inject a fake transport.

    Returns:
        Plan, accepted and rejected clips, measured densities and the package.

    Raises:
        ProfileError: The profile or its prompts are invalid.
        GeneratorError: The disk is short, no clip passed QA, or packing failed.
    """
    profile = load_overlay_profile(profile_name, data_dir / PROFILES_DIR)
    plan = plan_overlay(profile, _run_id())
    videos_dir = profile.dir / "videos"
    if not dry_run:
        videos_dir.mkdir(parents=True, exist_ok=True)
    existing, _ = _scan_existing(profile, plan, videos_dir, force=force)
    missing = tuple(item for item in plan.items if (videos_dir / item.filename) not in existing)
    if dry_run:
        return OverlayRun(
            plan=plan,
            accepted=existing,
            rejected=_scan_existing(profile, plan, videos_dir, force=False)[1],
            skipped=(),
            densities=_densities(profile, plan),
            dry_run=True,
        )
    failures: list[tuple[Path, str]] = []
    if missing:
        (data_dir / GENERATED_DIR).mkdir(parents=True, exist_ok=True)
        media.ensure_free_space(data_dir / GENERATED_DIR)
        run_settings = _bounded_settings(settings or load_generator_settings())
        session = _session_factory(data_dir, plan, session_factory)(profile, run_settings)
        results = _run_missing(
            profile,
            plan,
            missing,
            videos_dir,
            session,
            run_settings,
            client_factory,
        )
        for item in missing:
            result = _result_of(results, item)
            if result is not None and result.error:
                failures.append((videos_dir / item.filename, result.error))
    accepted, rejected = _scan_existing(profile, plan, videos_dir, force=False)
    rejected = (*rejected, *failures)
    densities = _densities(profile, plan)
    if not accepted:
        reasons = "; ".join(reason for _, reason in rejected[:3])
        raise GeneratorError(f"ни один клип {profile.name} не прошёл плотность: {reasons}")
    package = package_overlays(profile, accepted, data_dir)
    run = OverlayRun(
        plan=plan,
        accepted=accepted,
        rejected=rejected,
        skipped=tuple(path for path in existing if path in accepted),
        densities=densities,
        package=package,
    )
    _write_report(profile, run)
    logger.bind(
        profile=profile.name,
        run_id=plan.run_id,
        accepted=len(accepted),
        rejected=len(rejected),
        archive=str(package.archive),
    ).info("Overlay-партия упакована")
    return run


def _run_missing(
    profile: OverlayProfile,
    plan: OverlayPlan,
    missing: Sequence[OverlayPlanItem],
    videos_dir: Path,
    session: _ServerSession,
    settings: GeneratorSettings,
    client_factory: _ClientFactory | None,
) -> tuple[comfy._JobResult, ...]:
    """Generate *missing* clips on one server and return the job results."""
    items = tuple(_planned(profile, item) for item in missing)
    planned_by_seq = {item.seq: item for item in plan.items}
    run_settings = settings.model_copy(update={"safety_frame_attempts": _QA_ATTEMPTS})
    _, results = comfy.run_server(
        session,
        client_factory or comfy.ComfyClient,
        items,
        lambda item, attempt: build_overlay_workflow(
            profile,
            planned_by_seq[item.seq],
            attempt_seed(item.seed, attempt),
        ),
        videos_dir,
        label=profile.name,
        settings=run_settings,
        store=_store_source,
        verify=lambda item, path, attempt: rejection_reason(
            path,
            profile.width,
            profile.height,
            minimum=profile.min_density,
        ),
        on_submit=_log_submit,
        on_download=_log_download,
    )
    return results


def _planned(profile: OverlayProfile, item: OverlayPlanItem) -> _PlannedItem:
    """Adapt a planned overlay item to the shared ComfyUI queue type."""
    return _PlannedItem(
        profile=profile.name,
        seq=item.seq,
        look=_Look(id=f"prompt_{item.seq:03d}", values={}, prompt=item.prompt),
        seed=item.seed,
        frames=item.frames,
        filename=item.filename,
    )


def _session_factory(
    data_dir: Path,
    plan: OverlayPlan,
    injected: _SessionFactory | None,
) -> _SessionFactory:
    """Return the injected factory or the journal-backed Vast.ai factory."""
    if injected is not None:
        return injected
    journal = ServerJournal(data_dir / DB_DIR / DB_FILENAME)

    def create(profile: OverlayProfile, settings: GeneratorSettings) -> VastSession:
        return VastSession(profile, settings, journal=journal, run_id=plan.run_id)

    return create


def _store_source(path: Path, content: bytes) -> None:
    """Write the downloaded overlay exactly as ComfyUI produced it."""
    path.write_bytes(content)


def _scan_existing(
    profile: OverlayProfile,
    plan: OverlayPlan,
    videos_dir: Path,
    *,
    force: bool,
) -> tuple[tuple[Path, ...], tuple[tuple[Path, str], ...]]:
    """Split the clips already on disk into accepted and rejected ones."""
    accepted: list[Path] = []
    rejected: list[tuple[Path, str]] = []
    for item in plan.items:
        path = videos_dir / item.filename
        if force or not path.is_file():
            continue
        reason = rejection_reason(
            path,
            profile.width,
            profile.height,
            minimum=profile.min_density,
        )
        if reason is None:
            accepted.append(path)
        else:
            rejected.append((path, reason))
    return tuple(accepted), tuple(rejected)


def _densities(
    profile: OverlayProfile,
    plan: OverlayPlan,
) -> dict[str, float]:
    """Measure every clip of the plan that is still on disk."""
    values: dict[str, float] = {}
    for item in plan.items:
        path = profile.dir / "videos" / item.filename
        if path.is_file():
            values[item.filename] = bright_share(path, profile.width, profile.height)
    return values


def _result_of(
    results: Sequence[comfy._JobResult],
    item: OverlayPlanItem,
) -> comfy._JobResult | None:
    """Return the job result of one planned item."""
    for result in results:
        if result.item.seq == item.seq:
            return result
    return None


def _log_submit(item: _PlannedItem, attempt: int, prompt_id: str, contract_id: str) -> None:
    """Log a queued overlay prompt without its text."""
    logger.bind(
        file=item.filename,
        attempt=attempt,
        prompt_id=prompt_id,
        contract_id=contract_id,
    ).info("Overlay-клип отправлен в ComfyUI")


def _log_download(item: _PlannedItem, path: Path) -> None:
    """Log a downloaded overlay that passed density QA."""
    logger.bind(file=item.filename, path=str(path), seed=item.seed).info("Overlay-клип сохранён")


def _write_report(profile: OverlayProfile, run: OverlayRun) -> None:
    """Write the human-readable outcome next to the profile videos."""
    payload = {
        "profile": profile.name,
        "run_id": run.plan.run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "format": {
            "width": run.plan.width,
            "height": run.plan.height,
            "fps": run.plan.fps,
            "seconds": round(run.plan.seconds, 3),
        },
        "plan": [asdict(item) for item in run.plan.items],
        "accepted": [path.name for path in run.accepted],
        "rejected": [{"file": path.name, "reason": reason} for path, reason in run.rejected],
        "skipped": [path.name for path in run.skipped],
        "densities": {name: round(value, 2) for name, value in run.densities.items()},
        "package": None
        if run.package is None
        else {
            "directory": str(run.package.directory),
            "archive": str(run.package.archive),
            "preview": str(run.package.preview),
        },
    }
    path = profile.dir / _REPORT
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_id() -> str:
    """Return the UTC timestamp identifier of this run."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


__all__ = ["generate_overlays"]
