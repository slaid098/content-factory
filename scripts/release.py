#!/usr/bin/env python3
"""Publish the GitHub Release for the SHA that is live in production.

The workflow calls this script only after ``deploy`` succeeded. It builds
release notes from the previous ``release-*`` tag and creates the tag and
GitHub Release for the actual deployed SHA. A tag or Release that already
points at the same SHA is left alone, so a repeated deploy of one SHA never
creates a second Release.

Notes contain only grouped PR titles and links: no PR bodies, diffs, hosts, IP
addresses or secrets. The body is built from the commit range, so the same
range always gives the same list.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PR_SUFFIX_RE = re.compile(r"\s*\(#(?P<number>\d+)\)\s*$")
_MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #(?P<number>\d+)")
_GROUP_RE = re.compile(r"^(?P<group>feat|fix)(?:\([^)]*\))?!?:")
_BODY_PR_RE = re.compile(r"\(#\d+\)")
_GROUP_ORDER = ("feat", "fix", "chore")
_FIRST_RELEASE_COMMITS = 50


class ReleaseEntry(NamedTuple):
    """One release-notes line: a PR title or a direct commit subject."""

    group: str
    title: str
    pr: int | None
    sha: str


def release_tag(day: date, sha: str) -> str:
    """Return ``release-YYYY.MM.DD-<short-sha>`` for one deployed commit."""
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("SHA должен быть 40-символьным в нижнем регистре")
    return f"release-{day:%Y.%m.%d}-{sha[:7]}"


def commit_group(subject: str) -> str:
    """Map a conventional-commit subject to feat, fix or chore."""
    match = _GROUP_RE.match(subject)
    if match is None:
        return "chore"
    return match.group("group")


def pr_number(subject: str) -> int | None:
    """Return the PR number carried by a squash or merge subject."""
    match = _PR_SUFFIX_RE.search(subject) or _MERGE_SUBJECT_RE.match(subject)
    if match is None:
        return None
    return int(match.group("number"))


def render_notes(entries: Sequence[ReleaseEntry], repo: str) -> str:
    """Render deterministic notes grouped by feat, fix and chore."""
    lines: list[str] = []
    for group in _GROUP_ORDER:
        group_entries = [entry for entry in entries if entry.group == group]
        if not group_entries:
            continue
        if lines:
            lines.append("")
        lines.append(f"## {group}")
        lines.extend(_entry_line(entry, repo) for entry in group_entries)
    return "\n".join(lines) + "\n"


def body_has_pr_link(body: str) -> bool:
    """Return True when notes contain the required ``(#N)`` marker."""
    return bool(_BODY_PR_RE.search(body))


def _entry_line(entry: ReleaseEntry, repo: str) -> str:
    """Render one entry; the title links to its PR or direct commit."""
    if entry.pr is not None:
        url = f"https://github.com/{repo}/pull/{entry.pr}"
        return f"- [{entry.title}]({url}) (#{entry.pr})"
    url = f"https://github.com/{repo}/commit/{entry.sha}"
    return f"- [{entry.title}]({url}) ({entry.sha[:7]})"


def _run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and return its completed process."""
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"команда {command[0]} завершилась с кодом {result.returncode}: {message}"
        )
    return result


def _git(*args: str) -> str:
    """Run git and return stdout."""
    return _run(["git", *args]).stdout


def _gh(*args: str, check: bool = True) -> str:
    """Run gh and return stdout."""
    return _run(["gh", *args], check=check).stdout


def _fetch_repository(sha: str) -> None:
    """Fetch tags and the deployed commit when checkout did not bring it."""
    _git("fetch", "--tags", "--force")
    if _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], check=False).returncode != 0:
        _git("fetch", "origin", sha)
        _git("fetch", "--tags", "--force")


def _tags_at(sha: str) -> list[str]:
    """Return every local tag pointing at ``sha``."""
    output = _git("tag", "--points-at", sha)
    return sorted(tag.strip() for tag in output.splitlines() if tag.strip())


def _release_exists(repo: str, tag: str) -> bool:
    """Return True when a GitHub Release already uses ``tag``."""
    return _run(["gh", "release", "view", tag, "--repo", repo], check=False).returncode == 0


def _released_at(repo: str, sha: str) -> str | None:
    """Return a tag at ``sha`` that already has a GitHub Release."""
    for tag in _tags_at(sha):
        if _release_exists(repo, tag):
            return tag
    return None


def _release_tag_at(sha: str) -> str | None:
    """Return an existing ``release-*`` tag at ``sha``, if any."""
    for tag in _tags_at(sha):
        if tag.startswith("release-"):
            return tag
    return None


def _tag_commit(tag: str) -> str | None:
    """Return the commit a local tag points at, or None when it is missing."""
    result = _run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _previous_tag(sha: str) -> str | None:
    """Return the nearest ``release-*`` tag reachable from the parent of ``sha``."""
    result = _run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "release-*", f"{sha}^"],
        check=False,
    )
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def _commits(previous: str | None, sha: str) -> list[str]:
    """Return first-parent commits of the release range, newest first."""
    if previous is None:
        args = ["rev-list", "--first-parent", "--max-count", str(_FIRST_RELEASE_COMMITS), sha]
    else:
        args = ["log", "--first-parent", "--format=%H", f"{previous}..{sha}"]
    return [line.strip() for line in _git(*args).splitlines() if line.strip()]


def _commit_subject(commit: str) -> str:
    """Return the subject line of one commit."""
    return _git("log", "-1", "--format=%s", commit).strip()


def _lookup_pr(repo: str, commit: str) -> int | None:
    """Return the PR associated with a commit, if the API knows one."""
    output = _gh(
        "api",
        f"repos/{repo}/commits/{commit}/pulls",
        "--jq",
        ".[0].number // empty",
        check=False,
    ).strip()
    return int(output) if output.isdigit() else None


def _lookup_pr_title(repo: str, pr: int) -> str | None:
    """Return a PR title; the caller falls back to the commit subject."""
    output = _gh("api", f"repos/{repo}/pulls/{pr}", "--jq", ".title", check=False).strip()
    return output or None


def _clean_subject(subject: str, pr: int | None) -> str:
    """Strip a trailing ``(#N)`` from a fallback commit subject."""
    if pr is None:
        return subject
    cleaned = _PR_SUFFIX_RE.sub("", subject).strip()
    return cleaned or subject


def _entry(repo: str, commit: str) -> ReleaseEntry:
    """Build one release-notes entry from a commit and its PR title."""
    subject = _commit_subject(commit)
    pr = pr_number(subject)
    if pr is None:
        pr = _lookup_pr(repo, commit)
    title = _lookup_pr_title(repo, pr) if pr is not None else None
    return ReleaseEntry(
        group=commit_group(subject),
        title=title or _clean_subject(subject, pr),
        pr=pr,
        sha=commit,
    )


def _ensure_tag(tag: str, sha: str) -> None:
    """Create and push the tag, tolerating a concurrent identical push."""
    current = _tag_commit(tag)
    if current is not None:
        if current != sha:
            raise RuntimeError(f"тег {tag} уже указывает на {current}, а не на {sha}")
        return
    _git("tag", tag, sha)
    result = _run(["git", "push", "origin", f"refs/tags/{tag}"], check=False)
    if result.returncode == 0:
        return
    _git("fetch", "--tags", "--force")
    current = _tag_commit(tag)
    if current != sha:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"не удалось опубликовать тег {tag}: {message}")


def _ensure_release(repo: str, tag: str, notes: Path) -> bool:
    """Create the release; return False when another run created it first."""
    command = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--title",
        tag,
        "--notes-file",
        str(notes),
        "--verify-tag",
    ]
    result = _run(command, check=False)
    if result.returncode == 0:
        return True
    if _release_exists(repo, tag):
        return False
    message = result.stderr.strip() or result.stdout.strip()
    raise RuntimeError(f"gh release create не смог создать {tag}: {message}")


def _verify_release(repo: str, tag: str, sha: str) -> None:
    """Read the tag and Release back and check the release contract."""
    _git("fetch", "--tags", "--force")
    actual = _tag_commit(tag)
    if actual != sha:
        raise RuntimeError(f"тег {tag} указывает на {actual}, а не на выкаченный {sha}")
    output = _gh("release", "view", tag, "--repo", repo, "--json", "tagName,body")
    payload = json.loads(output)
    tag_name = payload.get("tagName")
    body = payload.get("body") or ""
    if tag_name != tag:
        raise RuntimeError(f"Release вернул тег {tag_name}, ожидался {tag}")
    if not body.strip():
        raise RuntimeError(f"Release {tag} создан с пустым телом")
    if not body_has_pr_link(body):
        raise RuntimeError(f"Release {tag} не содержит ссылок на PR в формате (#N)")


def _error(message: str) -> int:
    """Print a GitHub annotation and return a failing exit code."""
    print(f"::error::{message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Create and verify the release for one deployed SHA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True, help="фактический SHA с прод-хоста")
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/name (по умолчанию GITHUB_REPOSITORY)",
    )
    args = parser.parse_args(argv)
    repo = args.repo.strip()
    sha = args.sha.strip()
    if not _SHA_RE.fullmatch(sha):
        return _error(f"ожидался 40-символьный SHA в нижнем регистре, получено {sha!r}")
    if "/" not in repo:
        return _error("не задан репозиторий owner/name (--repo или GITHUB_REPOSITORY)")
    try:
        _fetch_repository(sha)
        released = _released_at(repo, sha)
        if released is not None:
            print(f"Release {released} уже есть для {sha}; создание пропущено.")
            return 0
        tag = _release_tag_at(sha) or release_tag(datetime.now(UTC).date(), sha)
        previous = _previous_tag(sha)
        commits = _commits(previous, sha)
        if not commits:
            start = previous or f"последние {_FIRST_RELEASE_COMMITS} коммитов"
            return _error(f"в диапазоне {start}..{sha} нет коммитов; пустой Release не создаётся")
        entries = [_entry(repo, commit) for commit in commits]
        notes = render_notes(entries, repo)
        if not body_has_pr_link(notes):
            return _error("в диапазоне нет ни одного PR; Release без (#N) не создаётся")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".md",
            delete=False,
        ) as handle:
            handle.write(notes)
            notes_path = Path(handle.name)
        try:
            _ensure_tag(tag, sha)
            created = _ensure_release(repo, tag, notes_path)
            if created:
                print(f"Создан Release {tag} для {sha}.")
                _verify_release(repo, tag, sha)
            else:
                print(f"Release {tag} уже создан параллельно; создание пропущено.")
        finally:
            notes_path.unlink(missing_ok=True)
    except (OSError, RuntimeError) as ex:
        return _error(str(ex))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
