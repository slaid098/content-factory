"""Command line entry point of the overlay batch.

Usage inside the deployed container::

    python -m content_factory.services.generator.overlay_cli next
    python -m content_factory.services.generator.overlay_cli check --prompts-file /tmp/p.txt
    python -m content_factory.services.generator.overlay_cli create --prompts-file /tmp/p.txt
    python -m content_factory.services.generator.overlay_cli plan --profile overlay/profile_17
    python -m content_factory.services.generator.overlay_cli run --profile overlay/profile_17

``plan`` never touches the network. ``run`` takes the shared ``generator``
lock, rents one Vast.ai server, regenerates weak clips up to three times,
packs the archive and the preview, and releases the lock.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, PROFILES_DIR
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError, GeneratorError
from content_factory.schemas.generator.overlay import OverlayRun
from content_factory.services.generator.overlay_batch import generate_overlays
from content_factory.services.generator.overlay_profiles import (
    create_overlay_profile,
    load_overlay_profile,
    next_profile_name,
)
from content_factory.services.generator.overlay_prompts import (
    blocking,
    check_prompts,
    load_existing,
    read_prompts,
)
from content_factory.services.generator.overlay_quality import (
    bright_share,
    classify,
    minimum_density,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, dispatch one command and return the exit code."""
    args = _parser().parse_args(argv)
    root = args.data_dir / PROFILES_DIR
    try:
        return _dispatch(args, root)
    except (GeneratorError, GeneratorBusyError) as ex:
        print(f"ошибка: {ex}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace, root: Path) -> int:
    """Run the selected subcommand."""
    if args.command == "next":
        print(next_profile_name(root))
        return 0
    if args.command == "check":
        return _check(args.prompts_file, root)
    if args.command == "create":
        return _create(args.prompts_file, args.name, root)
    if args.command == "plan":
        return _plan(args.profile, args.data_dir, args.verbose)
    if args.command == "run":
        return _run(args.profile, args.data_dir, args.dry_run, args.force)
    if args.command == "density":
        return _density(args.profile, root)
    raise AssertionError(f"неизвестная команда {args.command!r}")


def _check(prompts_file: Path, root: Path) -> int:
    """Print near-duplicate conflicts; a blocking pair gives exit code 1."""
    prompts = read_prompts(prompts_file)
    if not prompts:
        print(f"ошибка: в {prompts_file} нет промптов", file=sys.stderr)
        return 1
    existing = load_existing(root / "overlay")
    conflicts = check_prompts(prompts, existing)
    blocks = blocking(conflicts)
    for item in conflicts:
        mark = "БЛОК" if item.blocking else "предупреждение"
        print(f"{mark} {item.ratio:.3f}: {item.other_profile}:{item.other_line}")
    if blocks:
        print(f"блокирующих совпадений: {len(blocks)} — замените эти промпты", file=sys.stderr)
        return 1
    print(f"промптов: {len(prompts)}, блокирующих совпадений нет")
    return 0


def _create(prompts_file: Path, name: str | None, root: Path) -> int:
    """Validate prompts, create the next overlay profile and print its name."""
    prompts = read_prompts(prompts_file)
    if not prompts:
        print(f"ошибка: в {prompts_file} нет промптов", file=sys.stderr)
        return 1
    conflicts = check_prompts(prompts, load_existing(root / "overlay"))
    blocks = blocking(conflicts)
    for item in conflicts:
        if not item.blocking:
            print(f"предупреждение {item.ratio:.3f}: {item.other_profile}:{item.other_line}")
    if blocks:
        print(f"ошибка: {len(blocks)} промптов слишком похожи на существующие", file=sys.stderr)
        return 1
    target = name or next_profile_name(root)
    directory = create_overlay_profile(root, target, prompts)
    print(f"профиль создан: {directory.relative_to(root).as_posix()}")
    print(f"промптов: {len(prompts)}")
    return 0


def _plan(profile_name: str, data_dir: Path, verbose: bool) -> int:
    """Build and print a dry-run of an overlay profile; no network is used."""
    run = generate_overlays(profile_name, data_dir=data_dir, dry_run=True)
    _print_run(run, verbose=verbose, dry_run=True)
    return 0


def _run(profile_name: str, data_dir: Path, dry_run: bool, force: bool) -> int:
    """Generate the profile under the shared generator lock and pack it."""
    if dry_run:
        return _plan(profile_name, data_dir, verbose=False)
    lock = try_acquire(lock_path(data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        raise GeneratorBusyError("генератор уже запущен: дождитесь конца текущей партии")
    try:
        run = generate_overlays(profile_name, data_dir=data_dir, force=force)
    finally:
        lock.release()
    _print_run(run, verbose=False, dry_run=False)
    return 0


def _density(profile_name: str, root: Path) -> int:
    """Print density of every clip already on disk."""
    profile = load_overlay_profile(profile_name, root)
    minimum = minimum_density(profile.min_density)
    videos = sorted((profile.dir / "videos").glob("*.mp4"))
    if not videos:
        print("клипов нет")
        return 0
    bad = 0
    for path in videos:
        share = bright_share(path, profile.width, profile.height)
        kind = classify(share)
        if share < minimum:
            bad += 1
        print(f"{share:6.2f}%  {kind.kind.value:6}  {path.name}")
    print(f"клипов: {len(videos)}, ниже нормы {minimum:g}%: {bad}")
    return 1 if bad else 0


def _print_run(run: OverlayRun, *, verbose: bool, dry_run: bool) -> None:
    """Print the short batch report shared by ``plan`` and ``run``."""
    plan = run.plan
    print(f"профиль: {plan.profile}")
    print(
        f"формат: {plan.width}x{plan.height}, {plan.fps} fps, "
        f"{plan.seconds:.2f} с, без звука, чёрный фон"
    )
    print(f"клипов в плане: {len(plan.items)}")
    print(f"уже принято: {len(run.accepted)}, отклонено: {len(run.rejected)}")
    if dry_run:
        minutes = 10 + len(plan.items) * 2
        print(f"оценка: до {minutes} мин, бюджет до ${_estimate(run):.2f}")
        print("режим: dry-run, сервер не арендуется")
        if verbose:
            for item in plan.items:
                print(f"  {item.seq:02d}. {item.prompt}")
        return
    if run.package is not None:
        print(f"архив: {run.package.archive}")
        print(f"превью: {run.package.preview}")
    if run.densities:
        values = sorted(run.densities.values())
        print(f"плотность: мин {values[0]:.2f}%, медиана {values[len(values) // 2]:.2f}%")
    for path, reason in run.rejected:
        print(f"отклонён: {path.name} — {reason}")


def _estimate(run: OverlayRun) -> float:
    """Return a conservative dollar estimate: half an hour at the rate cap."""
    return run.plan.max_cost * 0.5


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser of the overlay CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.services.generator.overlay_cli"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=get_settings().data_dir,
        help="каталог app_data (по умолчанию CONTENT_FACTORY_DATA_DIR)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("next", help="напечатать имя следующего свободного профиля")

    check = commands.add_parser("check", help="проверить промпты на повторы")
    check.add_argument("--prompts-file", type=Path, required=True)

    create = commands.add_parser("create", help="создать профиль из файла промптов")
    create.add_argument("--prompts-file", type=Path, required=True)
    create.add_argument("--name", help="overlay/profile_N; по умолчанию следующий свободный")

    plan = commands.add_parser("plan", help="dry-run профиля без сети")
    plan.add_argument("--profile", required=True)
    plan.add_argument("--verbose", action="store_true", help="показать промпты")

    run = commands.add_parser("run", help="сгенерировать, проверить и упаковать профиль")
    run.add_argument("--profile", required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force", action="store_true", help="перегенерировать все клипы")

    density = commands.add_parser("density", help="проверить плотность готовых клипов")
    density.add_argument("--profile", required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
