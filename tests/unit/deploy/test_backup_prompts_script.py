"""Snapshot of profile text data into the private prompts repository."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "backup_prompts.sh"


def _run(
    app_data: Path,
    backup: Path,
    remote: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APP_DATA_DIR": str(app_data),
        "BACKUP_DIR": str(backup),
        "BACKUP_REMOTE": str(remote),
    }
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commits(repo: Path) -> int:
    return int(_git("rev-list", "--count", "main", cwd=repo))


def _prepare(tmp_path: Path) -> tuple[Path, Path, Path]:
    app_data = tmp_path / "app_data"
    profile = app_data / "profiles" / "dating" / "profile_1"
    profile.mkdir(parents=True)
    (profile / "profile.yaml").write_text("language: ru\n", encoding="utf-8")
    (profile / "text_prompt.txt").write_text("prompt\n", encoding="utf-8")
    (profile / "voice.mp3").write_bytes(b"audio")
    (profile / "clip.mp4").write_bytes(b"video")
    (app_data / "config").mkdir()
    (app_data / "config" / "config.yaml").write_text("run: {}\n", encoding="utf-8")
    (app_data / "queries").mkdir()
    (app_data / "queries" / "ru.txt").write_text("query\n", encoding="utf-8")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(remote)],
        check=True,
    )
    return app_data, remote, tmp_path / "backup"


def test_snapshot_keeps_text_and_skips_media(tmp_path: Path) -> None:
    app_data, remote, backup = _prepare(tmp_path)

    _run(app_data, backup, remote)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    profile = clone / "profiles" / "dating" / "profile_1"
    assert (profile / "profile.yaml").is_file()
    assert (profile / "text_prompt.txt").is_file()
    assert not (profile / "voice.mp3").exists()
    assert not (profile / "clip.mp4").exists()
    assert (clone / "config" / "config.yaml").is_file()
    assert (clone / "queries" / "ru.txt").is_file()
    assert _commits(clone) == 1


def test_second_run_without_changes_adds_no_commit(tmp_path: Path) -> None:
    app_data, remote, backup = _prepare(tmp_path)

    _run(app_data, backup, remote)
    first = _commits(remote)
    _run(app_data, backup, remote)
    second = _commits(remote)

    assert first == second == 1


def test_symlink_lock_is_rejected(tmp_path: Path) -> None:
    app_data, remote, backup = _prepare(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep\n", encoding="utf-8")
    Path(f"{backup}.lock").symlink_to(victim)

    result = _run(app_data, backup, remote, check=False)

    assert result.returncode == 1
    assert victim.read_text(encoding="utf-8") == "keep\n"
