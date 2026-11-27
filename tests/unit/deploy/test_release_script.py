"""Release notes and idempotency of the GitHub Release script."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "release.py"
_SHA = "b185893" + "0" * 33


def _load_release() -> ModuleType:
    """Load ``scripts/release.py`` without making scripts a package."""
    spec = importlib.util.spec_from_file_location("release", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def release() -> ModuleType:
    return _load_release()


def test_release_tag_uses_date_and_seven_char_sha(release: ModuleType) -> None:
    assert release.release_tag(date(2026, 11, 27), _SHA) == "release-2026.11.27-b185893"


@pytest.mark.parametrize("sha", ["abc", "A" * 40, "g" * 40, _SHA + "0"])
def test_release_tag_rejects_a_non_lowercase_full_sha(release: ModuleType, sha: str) -> None:
    with pytest.raises(ValueError):
        release.release_tag(date(2026, 11, 27), sha)


@pytest.mark.parametrize(
    ("subject", "group"),
    [
        ("feat(api): add version", "feat"),
        ("feat!: break api", "feat"),
        ("fix: repair deploy", "fix"),
        ("chore: bump deps", "chore"),
        ("docs: update runbook", "chore"),
    ],
)
def test_commit_group_follows_conventional_prefix(
    release: ModuleType,
    subject: str,
    group: str,
) -> None:
    assert release.commit_group(subject) == group


@pytest.mark.parametrize(
    ("subject", "number"),
    [
        ("feat(api): add version (#123)", 123),
        ("Merge pull request #45 from slaid098/run/issue-45", 45),
        ("chore: no pull request", None),
    ],
)
def test_pr_number_reads_squash_and_merge_subjects(
    release: ModuleType,
    subject: str,
    number: int | None,
) -> None:
    assert release.pr_number(subject) == number


def test_notes_group_titles_and_link_prs(release: ModuleType) -> None:
    entries = [
        release.ReleaseEntry("feat", "Add version", 12, "a" * 40),
        release.ReleaseEntry("chore", "Bump deps", 13, "b" * 40),
        release.ReleaseEntry("fix", "Repair deploy", 14, "c" * 40),
    ]

    notes = release.render_notes(entries, "slaid098/content-factory")

    assert notes == (
        "## feat\n"
        "- [Add version](https://github.com/slaid098/content-factory/pull/12) (#12)\n"
        "\n"
        "## fix\n"
        "- [Repair deploy](https://github.com/slaid098/content-factory/pull/14) (#14)\n"
        "\n"
        "## chore\n"
        "- [Bump deps](https://github.com/slaid098/content-factory/pull/13) (#13)\n"
    )
    assert release.body_has_pr_link(notes)


def test_direct_commit_gets_a_commit_link_and_no_pr_number(release: ModuleType) -> None:
    entry = release.ReleaseEntry("chore", "Direct commit", None, "d" * 40)

    notes = release.render_notes([entry], "slaid098/content-factory")

    assert "https://github.com/slaid098/content-factory/commit/" in notes
    assert "(ddddddd)" in notes
    assert "(#" not in notes


def test_existing_release_skips_creation(
    release: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(release, "_fetch_repository", lambda _sha: None)
    monkeypatch.setattr(
        release,
        "_released_at",
        lambda _repo, _sha: "release-2026.11.27-b185893",
    )
    monkeypatch.setattr(
        release,
        "_ensure_tag",
        lambda *_args: pytest.fail("tag must not be created again"),
    )

    assert release.main(["--sha", _SHA, "--repo", "slaid098/content-factory"]) == 0
    assert "создание пропущено" in capsys.readouterr().out


def test_empty_range_fails_before_creating_a_release(
    release: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(release, "_fetch_repository", lambda _sha: None)
    monkeypatch.setattr(release, "_released_at", lambda _repo, _sha: None)
    monkeypatch.setattr(release, "_release_tag_at", lambda _sha: None)
    monkeypatch.setattr(
        release,
        "_previous_tag",
        lambda _sha: "release-2026.11.26-aaaaaaa",
    )
    monkeypatch.setattr(release, "_commits", lambda _previous, _sha: [])
    monkeypatch.setattr(
        release,
        "_ensure_tag",
        lambda *_args: pytest.fail("tag must not be created for an empty range"),
    )

    assert release.main(["--sha", _SHA, "--repo", "slaid098/content-factory"]) == 1
    assert "нет коммитов" in capsys.readouterr().err


def test_existing_tag_without_release_gets_the_release(
    release: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = "release-2026.11.26-aaaaaaa"
    entry = release.ReleaseEntry("feat", "Add version", 12, "a" * 40)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(release, "_fetch_repository", lambda _sha: None)
    monkeypatch.setattr(release, "_released_at", lambda _repo, _sha: None)
    monkeypatch.setattr(release, "_release_tag_at", lambda _sha: tag)
    monkeypatch.setattr(release, "_previous_tag", lambda _sha: None)
    monkeypatch.setattr(release, "_commits", lambda _previous, _sha: [entry.sha])
    monkeypatch.setattr(release, "_entry", lambda _repo, _commit: entry)
    monkeypatch.setattr(
        release,
        "_ensure_tag",
        lambda tag_name, sha: calls.append(("tag", tag_name, sha)),
    )
    monkeypatch.setattr(
        release,
        "_ensure_release",
        lambda repo, tag_name, _notes: calls.append(("release", repo, tag_name)) or True,
    )
    monkeypatch.setattr(
        release,
        "_verify_release",
        lambda repo, tag_name, sha: calls.append(("verify", repo, tag_name, sha)),
    )

    assert release.main(["--sha", _SHA, "--repo", "slaid098/content-factory"]) == 0
    assert calls == [
        ("tag", tag, _SHA),
        ("release", "slaid098/content-factory", tag),
        ("verify", "slaid098/content-factory", tag, _SHA),
    ]


def test_verify_release_rejects_notes_without_a_pr_marker(
    release: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = "release-2026.11.27-b185893"
    monkeypatch.setattr(release, "_git", lambda *_args: "")
    monkeypatch.setattr(release, "_tag_commit", lambda _tag: _SHA)
    monkeypatch.setattr(
        release,
        "_gh",
        lambda *_args: f'{{"tagName": "{tag}", "body": "## feat\\n- Add version"}}',
    )

    with pytest.raises(RuntimeError, match=r"\(#N\)"):
        release._verify_release("slaid098/content-factory", tag, _SHA)


def test_invalid_sha_fails_before_git(
    release: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release,
        "_fetch_repository",
        lambda _sha: pytest.fail("git must not run for an invalid sha"),
    )

    assert release.main(["--sha", "test", "--repo", "slaid098/content-factory"]) == 1
