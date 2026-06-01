"""``python -m tools.changelog_cli`` — regenerate ``CHANGELOG.md``.

Wraps :mod:`tools.changelog_gen`. The default invocation writes a
90-day window grouped by date to ``CHANGELOG.md`` at the repo root.

Usage
-----
    # Default — last 90 days, grouped by date, written to CHANGELOG.md
    python -m tools.changelog_cli

    # Absolute date cutoff
    python -m tools.changelog_cli --since 2026-01-01

    # Relative shorthand — same as `30 days ago` to git
    python -m tools.changelog_cli --since 30d

    # Upper bound
    python -m tools.changelog_cli --until 2026-05-01

    # Cap on commit count
    python -m tools.changelog_cli --limit 200

    # Group by category instead of date
    python -m tools.changelog_cli --group-by category

    # Custom output destination
    python -m tools.changelog_cli --out docs/CHANGELOG.md

    # Print to stdout (no file written) — handy for piping or previewing
    python -m tools.changelog_cli --print

Exit codes
----------
* ``0`` — the changelog was rendered (file written, or stdout flushed).
* ``1`` — an unexpected error occurred while writing the file. The
  error message is printed to stderr; stdout is left empty so
  ``--print`` piping stays clean.
* ``2`` — argparse rejected the invocation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from tools.changelog_gen import (
    parse_commits,
    render_changelog_markdown,
    write_changelog,
)


_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
_DEFAULT_OUT: Path = _PROJECT_ROOT / "CHANGELOG.md"
_DEFAULT_SINCE: str = "90d"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.changelog_cli",
        description=(
            "Regenerate CHANGELOG.md from the git log. Default window: "
            "the last 90 days, grouped by date."
        ),
    )
    p.add_argument(
        "--since",
        default=_DEFAULT_SINCE,
        help=(
            "Lower-bound cutoff. Accepts an absolute date "
            "(`2026-01-01`), a git relative string (`2 weeks ago`), "
            "or the shorthand `30d` / `12w` / `6m` / `1y` (default: "
            f"{_DEFAULT_SINCE})."
        ),
    )
    p.add_argument(
        "--until",
        default=None,
        help="Upper-bound cutoff. Same shape as --since (default: none).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of commits to include (default: no cap).",
    )
    p.add_argument(
        "--group-by",
        choices=("date", "category", "flat"),
        default="date",
        help=(
            "How to group commits in the rendered Markdown "
            "(default: date)."
        ),
    )
    p.add_argument(
        "--title",
        default="Changelog",
        help="Top-level title (default: Changelog).",
    )
    p.add_argument(
        "--out",
        default=None,
        help=f"Output file path (default: {_DEFAULT_OUT}).",
    )
    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print to stdout instead of writing a file.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    if args.print_only:
        commits = parse_commits(
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
        text = render_changelog_markdown(
            commits,
            title=args.title,
            group_by=args.group_by,
        )
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        print(f"rendered {len(commits)} commits", file=sys.stderr)
        return 0

    out_path = Path(args.out) if args.out else _DEFAULT_OUT
    try:
        count = write_changelog(
            path=out_path,
            since=args.since,
            until=args.until,
            limit=args.limit,
            group_by=args.group_by,
            title=args.title,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to write {out_path}: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {out_path} ({count} commits)", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
