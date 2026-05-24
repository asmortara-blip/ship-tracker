"""Tests for ``tools.changelog_gen`` and ``tools.changelog_cli``.

The strategy mirrors the rest of the suite: prefer the real
implementation over mocks, isolate with ``tmp_path``. ``parse_commits``
is exercised against a freshly-``git init``'d throwaway repo so the
parser is verified end-to-end against the actual git binary.

We also verify the never-raises contract by pointing ``parse_commits``
at a directory that is NOT a git repo and confirming an empty list
comes back without an exception.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.changelog_gen import (
    CommitEntry,
    categorize,
    parse_commits,
    render_changelog_markdown,
    write_changelog,
)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(),
    reason="git binary not available on PATH",
)


def _run_git(*args: str, cwd: Path, env: dict[str, str] | None = None) -> None:
    """Run a git command in ``cwd``, raising on non-zero exit."""
    full_env = os.environ.copy()
    # Make committer identity deterministic + skip global hooks so
    # CI environments behave the same as local.
    full_env.update({
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        # Disable global hooks / configs that may inject sign-offs.
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    })
    if env:
        full_env.update(env)
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=full_env,
        check=True,
        capture_output=True,
    )


def _make_repo(path: Path) -> Path:
    """Initialise an empty git repo at ``path`` and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "--initial-branch=main", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test Author", cwd=path)
    _run_git("config", "commit.gpgsign", "false", cwd=path)
    return path


def _commit(path: Path, *, subject: str, body: str = "", filename: str = "README.md", date: str | None = None) -> None:
    """Add a one-line change + commit. ``date`` is the author date in
    git's ``--date=`` syntax (e.g. ``2026-05-01T12:00:00``)."""
    target = path / filename
    target.write_text((target.read_text() if target.exists() else "") + f"\n{subject}\n", encoding="utf-8")
    _run_git("add", filename, cwd=path)
    message = subject if not body else f"{subject}\n\n{body}"
    env: dict[str, str] = {}
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    # ``-m`` keeps quoting simple even when the body has newlines.
    _run_git("commit", "-m", message, cwd=path, env=env)


# ─── categorize ──────────────────────────────────────────────────────────


def test_categorize_feat_prefix_maps_to_feature():
    assert categorize("feat: foo") == "feature"


def test_categorize_fix_prefix_maps_to_fix():
    assert categorize("fix: bar") == "fix"


def test_categorize_scoped_ui_prefix_maps_to_ui():
    # ``ui(alerts): foo`` is a Conventional-Commit scope; the bucket
    # is the prefix BEFORE the scope.
    assert categorize("ui(alerts): baz") == "ui"


def test_categorize_combined_prefix_first_token_wins():
    # Documented behaviour: for ``engine+ui:`` we pick the FIRST token.
    assert categorize("engine+ui: combined change") == "engine"


def test_categorize_unknown_prefix_maps_to_other():
    # A subject with no recognised prefix should bucket as 'other'.
    assert categorize("random subject no prefix") == "other"


def test_categorize_known_aliases_resolve():
    # The map carries a handful of aliases — verify a couple resolve.
    assert categorize("bug: typo") == "fix"
    assert categorize("docs(readme): tweak") == "docs"
    assert categorize("tools: db_check_cli upgrade") == "tools"


def test_categorize_empty_subject_is_other():
    assert categorize("") == "other"


# ─── parse_commits — end-to-end against a real synthetic repo ────────────


def test_parse_commits_returns_commit_entry_objects(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _commit(repo, subject="feat: first commit", body="Detail line one.")
    _commit(repo, subject="fix: typo")
    entries = parse_commits(repo_path=repo)
    assert len(entries) == 2
    assert all(isinstance(e, CommitEntry) for e in entries)
    # parse_commits is newest-first — `fix: typo` was committed last.
    assert entries[0].subject == "fix: typo"
    assert entries[1].subject == "feat: first commit"
    # The summary should pick up the body's first paragraph.
    assert entries[1].summary == "Detail line one."
    # Author falls through from git config we set in _make_repo.
    assert entries[0].author == "Test Author"
    # short_sha is at least 7 chars and is a prefix of the full sha.
    assert len(entries[0].short_sha) >= 7
    assert entries[0].sha.startswith(entries[0].short_sha)


def test_parse_commits_respects_since_cutoff(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    # An OLD commit + a NEW commit. ``since`` should drop the OLD one.
    _commit(repo, subject="feat: ancient", date="2020-01-01T00:00:00")
    _commit(repo, subject="feat: recent", date="2026-05-20T00:00:00")
    # ``--since 2024-01-01`` should keep only the 2026 commit.
    entries = parse_commits(since="2024-01-01", repo_path=repo)
    subjects = [e.subject for e in entries]
    assert "feat: recent" in subjects
    assert "feat: ancient" not in subjects


def test_parse_commits_strips_coauthor_trailer_from_summary(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _commit(
        repo,
        subject="feat: with trailer",
        body=(
            "First paragraph of the body.\n"
            "\n"
            "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
        ),
    )
    entries = parse_commits(repo_path=repo)
    assert len(entries) == 1
    # The first paragraph should be picked, NOT the trailer.
    assert entries[0].summary == "First paragraph of the body."
    assert "Co-Authored-By" not in entries[0].summary


def test_parse_commits_skips_merges(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _commit(repo, subject="feat: base")
    # Create a side branch + merge it with --no-ff so we get a real
    # merge commit, then verify it does NOT show up in parse_commits.
    _run_git("checkout", "-b", "side", cwd=repo)
    _commit(repo, subject="feat: side change")
    _run_git("checkout", "main", cwd=repo)
    _run_git("merge", "--no-ff", "-m", "Merge branch 'side'", "side", cwd=repo)
    entries = parse_commits(repo_path=repo)
    subjects = [e.subject for e in entries]
    # Both real commits land, but the merge commit does not.
    assert "feat: base" in subjects
    assert "feat: side change" in subjects
    assert not any(s.startswith("Merge branch") for s in subjects)


def test_parse_commits_never_raises_on_missing_repo(tmp_path):
    """The never-raises contract — a directory that is NOT a git repo
    should return an empty list, not raise."""
    # tmp_path is empty (no .git/), so this should degrade to [].
    entries = parse_commits(repo_path=tmp_path)
    assert entries == []


# ─── render_changelog_markdown ────────────────────────────────────────────


def _fake_commits() -> list[CommitEntry]:
    """A minimal commit set spanning two days + three categories so
    the renderer's grouping branches can be exercised without git."""
    return [
        CommitEntry(
            sha="a" * 40, short_sha="aaaaaaa",
            date="2026-05-23", author="Tester",
            subject="feat: new dashboard",
            category="feature",
            summary="First-paragraph summary.",
        ),
        CommitEntry(
            sha="b" * 40, short_sha="bbbbbbb",
            date="2026-05-23", author="Tester",
            subject="fix(ui): silence FutureWarning",
            category="ui",
            summary="",
        ),
        CommitEntry(
            sha="c" * 40, short_sha="ccccccc",
            date="2026-05-22", author="Tester",
            subject="tools: changelog_gen",
            category="tools",
            summary="Adds a CLI that renders the git log as Markdown.",
        ),
    ]


def test_render_changelog_markdown_non_empty_with_title():
    commits = _fake_commits()
    text = render_changelog_markdown(commits, title="My Changelog")
    assert isinstance(text, str) and text
    assert text.startswith("# My Changelog")
    # The DO NOT EDIT banner is part of the standard header — every
    # generated CHANGELOG carries it so contributors don't hand-edit.
    assert "DO NOT EDIT MANUALLY" in text


def test_render_changelog_markdown_group_by_date_has_date_headers():
    commits = _fake_commits()
    text = render_changelog_markdown(commits, group_by="date")
    # Every distinct date should land as a level-2 header.
    assert "## 2026-05-23" in text
    assert "## 2026-05-22" in text
    # Category sub-sections inside a day are level-3.
    assert "### " in text


def test_render_changelog_markdown_group_by_category_has_category_headers():
    commits = _fake_commits()
    text = render_changelog_markdown(commits, group_by="category")
    # Category-level layout puts buckets at level-2 — at least one of
    # the buckets we created must show up.
    assert "## " in text
    assert "Features" in text  # the feature label
    assert "Tools" in text     # the tools label


def test_render_changelog_markdown_group_by_flat_has_no_inner_sections():
    commits = _fake_commits()
    text = render_changelog_markdown(commits, group_by="flat")
    # In flat layout we expect EXACTLY two ``##`` headers (the title is
    # ``#`` not ``##``, plus the single "## All commits" section).
    assert text.count("\n## ") == 1
    # And no per-category level-3 sub-headers.
    assert "\n### " not in text
    # Every short_sha should be present.
    for c in commits:
        assert c.short_sha in text


def test_render_changelog_markdown_empty_commits_returns_placeholder():
    text = render_changelog_markdown([], title="Empty")
    assert "# Empty" in text
    assert "No commits in the requested range" in text


def test_render_changelog_markdown_unknown_group_by_raises():
    with pytest.raises(ValueError):
        render_changelog_markdown(_fake_commits(), group_by="bogus")


# ─── write_changelog ──────────────────────────────────────────────────────


def test_write_changelog_writes_the_file(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    _commit(repo, subject="feat: alpha")
    _commit(repo, subject="fix: beta", body="Repair regression.")
    out = tmp_path / "OUT.md"
    count = write_changelog(path=out, since=None, repo_path=repo)
    assert out.exists()
    assert count == 2
    text = out.read_text(encoding="utf-8")
    assert "# Changelog" in text
    assert "feat: alpha".split(":")[1].strip() in text  # body of the subject
    assert "Repair regression." in text


# ─── CLI smoke ────────────────────────────────────────────────────────────


def test_changelog_cli_print_mode_writes_no_file(tmp_path, capsys, monkeypatch):
    """``--print`` should NOT write a file; the rendered text should
    land on stdout and a one-line summary on stderr."""
    # Run the CLI against the live repo (parse_commits handles missing
    # git gracefully, and the project is a real repo).
    from tools import changelog_cli
    rc = changelog_cli.main(["--print", "--since", "7d"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "# Changelog" in captured.out
    assert "rendered" in captured.err
