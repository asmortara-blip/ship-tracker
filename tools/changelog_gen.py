"""``tools.changelog_gen`` — auto-generate a Markdown CHANGELOG from git.

Before this module existed, the only place to learn what shipped on a
given day was to run ``git log`` and squint. This module turns the
git log into a stable, scannable Markdown document grouped by date or
by Conventional-Commit category.

Public surface
--------------
``parse_commits(*, since=None, until=None, limit=None, repo_path=None)``
    Shell out to ``git log --no-merges`` with a parseable format and
    return a list of ``CommitEntry`` objects sorted newest-first. The
    function never raises — if ``git`` is not on PATH or the directory
    is not a repo, the return value is an empty list.

``categorize(subject)``
    Heuristic that maps a commit subject's prefix to one of the known
    category buckets: ``feature``, ``fix``, ``ui``, ``engine``, ``api``,
    ``ops``, ``tools``, ``docs``, ``test``, or ``other``. Handles plain
    prefixes (``feat:``), prefixes with parenthetical scopes
    (``ui(alerts):``), and combined prefixes (``engine+ui:``). For
    combined prefixes the FIRST token wins.

``render_changelog_markdown(commits, *, title='Changelog', group_by='date')``
    Render a list of ``CommitEntry`` as Markdown. Three layouts:
    ``'date'`` (one section per day), ``'category'`` (one section per
    bucket), or ``'flat'`` (no sub-sections). All three layouts share
    the same header / per-commit row format.

``write_changelog(path='CHANGELOG.md', *, since=None, ...)``
    Convenience wrapper — calls ``parse_commits`` + ``render_changelog_markdown``
    + writes the result to ``path``. Returns the number of commits
    written.

Design notes
------------
* The git format string uses a NUL byte (``\\x1f``) as the inter-field
  separator and a different NUL byte (``\\x1e``) as the inter-record
  separator. Both are control characters that should never appear in a
  commit subject or body. This sidesteps the usual headache of trying
  to parse a multi-line format with newline-based splitting.
* The ``Co-Authored-By:`` trailer is stripped from the body before we
  pick the first-paragraph summary — the trailer is bookkeeping, not
  changelog content.
* The generated CHANGELOG.md carries a ``DO NOT EDIT MANUALLY`` banner
  in the header so contributors do not try to hand-edit something that
  will get regenerated on the next cron run.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ─── Module-level constants ────────────────────────────────────────────────

_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Field + record separators for the git log format. ASCII control
# characters that never appear in a commit subject / body so we can
# split unambiguously even when the body itself contains newlines.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"

# The git format string — sha, short_sha, author-date in ISO 8601,
# author name, subject, body, then the record terminator.
_GIT_FORMAT = _FIELD_SEP.join(["%H", "%h", "%aI", "%an", "%s", "%b"]) + _RECORD_SEP

# Subject prefix → category bucket. The first token of a combined
# prefix wins (``engine+ui: foo`` → ``engine``). Both ``feat`` and
# ``feature`` map to ``feature`` so old commits + new commits land in
# the same bucket.
_CATEGORY_MAP: dict[str, str] = {
    "feat": "feature",
    "feature": "feature",
    "fix": "fix",
    "bug": "fix",
    "bugfix": "fix",
    "ui": "ui",
    "engine": "engine",
    "api": "api",
    "ingress": "api",
    "ops": "ops",
    "scheduler": "ops",
    "worker": "ops",
    "auth": "ops",
    "tools": "tools",
    "docs": "docs",
    "test": "test",
    "tests": "test",
    "chore": "other",
    "refactor": "other",
    "perf": "other",
    "style": "other",
    "ci": "other",
    "build": "other",
    "revert": "other",
}

# Display labels (with emoji) for each category. Order matters — the
# category section render walks this dict in declaration order so a
# rendered changelog reads ``Features → Fixes → UI → …``.
_CATEGORY_LABELS: dict[str, str] = {
    "feature":  "Features",
    "fix":      "Fixes",
    "ui":       "UI",
    "engine":   "Engine",
    "api":      "API",
    "ops":      "Ops",
    "tools":    "Tools",
    "docs":     "Docs",
    "test":     "Tests",
    "other":    "Other",
}

# Per-category emoji rendered before the label in section headers
# (`### ✨ Features`). The emoji-prefixed labels make the CHANGELOG
# scannable at a glance — the eye lands on the icon, not the word.
_CATEGORY_EMOJI: dict[str, str] = {
    "feature":  "✨",
    "fix":      "🐛",
    "ui":       "🎨",
    "engine":   "⚙️",
    "api":      "🔌",
    "ops":      "🔧",
    "tools":    "🛠",
    "docs":     "📚",
    "test":     "✅",
    "other":    "📦",
}

# Match a conventional-commit prefix at the START of a subject line.
# Captures up to the first colon. Examples it must catch:
#   feat: ...
#   feat(scope): ...
#   ui(alerts): ...
#   engine+ui: ...
#   fix(ui): silence pandas FutureWarning
_PREFIX_RE = re.compile(r"^([A-Za-z][\w+\-]*)(?:\([^)]*\))?\s*:")


# ─── Public data model ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommitEntry:
    """One commit as the changelog renderer needs it.

    Frozen so a rendered list can be passed around safely. ``date`` is
    the ISO date (YYYY-MM-DD) extracted from the author-date, in the
    author's local timezone — that matches what the operator sees in
    ``git log``."""

    sha: str          # full 40-char sha
    short_sha: str    # 7+ char abbreviation as git produced it
    date: str         # YYYY-MM-DD
    author: str       # author name
    subject: str      # first line of the commit message
    category: str     # bucket from _CATEGORY_MAP, or 'other'
    summary: str      # first paragraph of body, Co-Authored-By stripped


# ─── Categorisation ────────────────────────────────────────────────────────


def categorize(subject: str) -> str:
    """Map a commit subject to a category bucket.

    Recognises three prefix shapes:
      * plain — ``feat: foo`` → ``feature``
      * scoped — ``ui(alerts): foo`` → ``ui``
      * combined — ``engine+ui: foo`` → ``engine`` (first token wins)

    Anything that does not match returns ``other``."""
    if not subject:
        return "other"
    match = _PREFIX_RE.match(subject)
    if not match:
        return "other"
    raw = match.group(1).lower()
    # Combined prefix — ``engine+ui`` → first token. Use ``+`` and ``-``
    # as splitters so ``feat-fix`` and ``engine+ui`` both work.
    first_token = re.split(r"[+\-]", raw, maxsplit=1)[0]
    return _CATEGORY_MAP.get(first_token, "other")


# ─── git log driver ────────────────────────────────────────────────────────


def _normalize_since(since: Optional[str]) -> Optional[str]:
    """Accept either ``'30d'`` shorthand or any value git understands.

    ``30d`` → ``30 days ago`` so git's relative-date parser takes it.
    Absolute dates (``'2026-01-01'``) and other git-understood strings
    (``'2 weeks ago'``) pass through unchanged.
    """
    if since is None:
        return None
    s = since.strip()
    # ``30d`` / ``12w`` / ``6m`` / ``1y`` → expand to git-understood form.
    match = re.fullmatch(r"(\d+)\s*([dwmy])", s, flags=re.IGNORECASE)
    if match:
        n, unit = match.group(1), match.group(2).lower()
        word = {"d": "days", "w": "weeks", "m": "months", "y": "years"}[unit]
        return f"{n} {word} ago"
    return s


def _strip_coauthor_trailers(body: str) -> str:
    """Remove ``Co-Authored-By:`` (and friends) trailer lines from a
    commit body. Trailers are conventionally bookkeeping — they don't
    belong in a human-readable changelog summary."""
    if not body:
        return ""
    trailer_re = re.compile(
        r"^(Co-Authored-By|Signed-off-by|Reviewed-by|Acked-by|Tested-by|Reported-by|"
        r"Refs|Fixes|Closes|Resolves|See-also|Cc):\s",
        re.IGNORECASE,
    )
    kept = [line for line in body.splitlines() if not trailer_re.match(line)]
    return "\n".join(kept).strip()


def _first_paragraph(body: str) -> str:
    """Pick the first paragraph of a commit body — everything up to
    the first blank line — and collapse runs of whitespace so the
    rendered summary stays one logical line per commit."""
    if not body:
        return ""
    cleaned = _strip_coauthor_trailers(body)
    if not cleaned:
        return ""
    paragraphs = re.split(r"\n\s*\n", cleaned, maxsplit=1)
    first = paragraphs[0].strip()
    # Collapse internal whitespace so the rendered bullet is compact.
    return re.sub(r"\s+", " ", first)


def _parse_record(record: str) -> Optional[CommitEntry]:
    """Parse one git-log record (separator-delimited) into a CommitEntry.

    Returns None for an empty or malformed record so callers can filter
    them out without raising."""
    if not record or not record.strip():
        return None
    fields = record.split(_FIELD_SEP)
    if len(fields) < 6:
        return None
    sha, short_sha, iso_date, author, subject, body = (f.strip("\n") for f in fields[:6])
    # The author-date is ISO 8601 with a timezone offset
    # (``2026-05-24T17:14:22-05:00``). We only need the date portion.
    date_only = iso_date[:10] if iso_date else ""
    return CommitEntry(
        sha=sha,
        short_sha=short_sha,
        date=date_only,
        author=author,
        subject=subject,
        category=categorize(subject),
        summary=_first_paragraph(body),
    )


def parse_commits(
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: Optional[int] = None,
    repo_path: Optional[Path] = None,
) -> list[CommitEntry]:
    """Shell out to ``git log`` and return a newest-first list.

    Parameters
    ----------
    since
        ``'30d'`` shorthand, an absolute date (``'2026-01-01'``), or any
        relative string git accepts. None → no lower bound.
    until
        Same shape as ``since`` but bounds the upper end. None → up to
        the current HEAD.
    limit
        Cap on number of commits returned. None → no cap.
    repo_path
        Directory to invoke git in. None → the project root.

    Notes
    -----
    Never raises. If git is missing, the dir is not a repo, or the
    invocation fails for any other reason, the return is ``[]`` and
    the failure is silent. Callers wanting diagnostics should call
    ``git`` themselves.
    """
    # If git is not available, return empty — this is a tool, not a
    # critical-path module, so a missing dependency should not raise.
    if shutil.which("git") is None:
        return []

    cwd = Path(repo_path) if repo_path is not None else _PROJECT_ROOT
    cmd = [
        "git",
        "-C",
        str(cwd),
        "log",
        "--no-merges",
        f"--format={_GIT_FORMAT}",
    ]
    norm_since = _normalize_since(since)
    if norm_since:
        cmd.append(f"--since={norm_since}")
    norm_until = _normalize_since(until)
    if norm_until:
        cmd.append(f"--until={norm_until}")
    if limit is not None and limit > 0:
        cmd.append(f"-n{int(limit)}")

    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []

    if result.returncode != 0:
        return []

    out: list[CommitEntry] = []
    # Records are separated by _RECORD_SEP. The last record is
    # terminated too, so split + filter blank entries.
    for record in result.stdout.split(_RECORD_SEP):
        entry = _parse_record(record)
        if entry is not None:
            out.append(entry)
    return out


# ─── Markdown rendering ────────────────────────────────────────────────────


def _scope_tag(subject: str) -> str:
    """Extract the scope token from a subject prefix for display.

    For ``ui(alerts): foo`` returns ``ui(alerts)``. For ``engine+ui: foo``
    returns ``engine+ui``. For ``feat: foo`` returns ``feat``. Bare
    subjects return empty string."""
    if not subject:
        return ""
    match = _PREFIX_RE.match(subject)
    if not match:
        return ""
    end = subject.index(":", match.end() - 1)
    return subject[:end].strip()


def _subject_body(subject: str) -> str:
    """Return the subject with its prefix stripped — what shows up
    after the bolded scope tag in a rendered bullet."""
    if not subject:
        return ""
    match = _PREFIX_RE.match(subject)
    if not match:
        return subject.strip()
    return subject[subject.index(":", match.end() - 1) + 1:].strip()


def _render_commit_bullet(commit: CommitEntry) -> str:
    """Render one commit as a Markdown list item.

    Format:
        - **scope** subject-body (`shortsha`)
          - summary line wrapped to fit (optional)
    """
    scope = _scope_tag(commit.subject)
    body = _subject_body(commit.subject)
    if scope:
        head = f"- **{scope}** {body} (`{commit.short_sha}`)"
    else:
        head = f"- {body or commit.subject} (`{commit.short_sha}`)"
    if commit.summary:
        # Cap the summary so the bullet stays scannable. A truncated
        # one-liner is more useful than three lines of detail in a
        # bird's-eye changelog.
        summary = commit.summary
        if len(summary) > 240:
            summary = summary[:237].rstrip() + "..."
        return f"{head}\n  - {summary}"
    return head


def _category_section(category: str, commits: list[CommitEntry], *, heading_level: int) -> str:
    """Render one category sub-section for a date or category group."""
    if not commits:
        return ""
    label = _CATEGORY_LABELS.get(category, category.title())
    emoji = _CATEGORY_EMOJI.get(category, "")
    prefix = f"{emoji} " if emoji else ""
    hashes = "#" * heading_level
    lines = [f"{hashes} {prefix}{label}", ""]
    for commit in commits:
        lines.append(_render_commit_bullet(commit))
    return "\n".join(lines)


def _group_by_category(commits: list[CommitEntry]) -> dict[str, list[CommitEntry]]:
    """Bucket commits by category, preserving the input commit order
    within each bucket. Keys are returned in ``_CATEGORY_LABELS`` order
    so the output reads ``Features → Fixes → UI → …``."""
    buckets: dict[str, list[CommitEntry]] = {key: [] for key in _CATEGORY_LABELS}
    for c in commits:
        buckets.setdefault(c.category, []).append(c)
    # Drop empty buckets so the renderer doesn't print empty headers.
    return {k: v for k, v in buckets.items() if v}


def _header_block(title: str, *, commit_count: int, generated_at: Optional[datetime] = None) -> str:
    """Top-of-file header block — title, generated timestamp, the
    DO NOT EDIT banner, and the rendered-commit count."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y-%m-%d")
    return (
        f"# {title}\n\n"
        f"_Generated {stamp} from the git log — covers {commit_count} commit"
        f"{'' if commit_count == 1 else 's'}. "
        f"Conventional-commit prefixes (`feat:`, `fix:`, `ui:`, `engine:`, …) "
        f"bucket entries into categories._\n\n"
        f"**DO NOT EDIT MANUALLY** — regenerate with `python -m tools.changelog_cli`.\n"
    )


def render_changelog_markdown(
    commits: list[CommitEntry],
    *,
    title: str = "Changelog",
    group_by: str = "date",
    generated_at: Optional[datetime] = None,
) -> str:
    """Render a list of commits as a Markdown changelog.

    Parameters
    ----------
    commits
        Newest-first list (the order ``parse_commits`` returns).
    title
        Top-level header.
    group_by
        ``'date'`` — one ``## YYYY-MM-DD`` section per day, with
            category sub-sections inside each day.
        ``'category'`` — one ``## Category`` section per bucket
            (Features / Fixes / …) with the full date in each bullet.
        ``'flat'`` — no inner sections, just one bullet per commit
            ordered newest-first.

    Returns
    -------
    The Markdown document as a single string, terminated by a newline.
    """
    if group_by not in ("date", "category", "flat"):
        raise ValueError(f"unknown group_by: {group_by!r}")

    parts: list[str] = [
        _header_block(title, commit_count=len(commits), generated_at=generated_at),
    ]

    if not commits:
        parts.append("_No commits in the requested range._\n")
        return "\n".join(parts)

    if group_by == "flat":
        parts.append("## All commits\n")
        for c in commits:
            # Flat layout shows the date inline so the reader still has
            # temporal context without per-day sub-sections.
            bullet = _render_commit_bullet(c)
            parts.append(f"{bullet}  \n  _on {c.date}_" if c.summary else f"{bullet}  _({c.date})_")
        parts.append("")
        return "\n".join(parts)

    if group_by == "category":
        buckets = _group_by_category(commits)
        for category in _CATEGORY_LABELS:
            if category not in buckets:
                continue
            section = _category_section(category, buckets[category], heading_level=2)
            if section:
                parts.append(section)
                parts.append("")
        return "\n".join(parts)

    # group_by == "date" — one section per day, categories inside.
    # Group preserving the input order (newest-first).
    by_date: dict[str, list[CommitEntry]] = {}
    for c in commits:
        by_date.setdefault(c.date, []).append(c)

    for day, day_commits in by_date.items():
        parts.append(f"## {day}\n")
        # Inside a day we also bucket by category so the eye scans
        # ``Features → Fixes → UI`` rather than a mixed list.
        day_buckets = _group_by_category(day_commits)
        for category in _CATEGORY_LABELS:
            if category not in day_buckets:
                continue
            section = _category_section(category, day_buckets[category], heading_level=3)
            if section:
                parts.append(section)
                parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ─── File-writing convenience ──────────────────────────────────────────────


def write_changelog(
    path: str | Path = "CHANGELOG.md",
    *,
    since: Optional[str] = "90d",
    until: Optional[str] = None,
    limit: Optional[int] = None,
    group_by: str = "date",
    title: str = "Changelog",
    repo_path: Optional[Path] = None,
) -> int:
    """Generate a CHANGELOG.md and write it to ``path``.

    Returns the number of commits rendered. The default ``since='90d'``
    matches the convention recorded in ``docs/DEPLOYMENT.md`` for the
    nightly regeneration cron.
    """
    commits = parse_commits(
        since=since,
        until=until,
        limit=limit,
        repo_path=repo_path,
    )
    text = render_changelog_markdown(commits, title=title, group_by=group_by)
    out_path = Path(path)
    if not out_path.is_absolute():
        out_path = _PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return len(commits)
