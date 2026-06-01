"""tools/snapshot_integrity_cli.py — operator-callable snapshot integrity checks.

Wraps ``processing.snapshot_integrity`` so operators (and CI) can:
  * Check a single snapshot date for missing or corrupted files
  * Sweep the entire snapshot history at once
  * Use ``--verify`` as a CI/cron gate that exits 1 when anything's broken

The single-date check covers the date dir's expected files (per-port
summary + regional rollup) and attempts a CSV parse of the per-port
summary so truncated or malformed files surface as ``corrupted`` rather
than silently passing.

Usage::

    # Check today's snapshot (default container 40FT_DRY)
    python -m tools.snapshot_integrity_cli --date 2026-05-26

    # Sweep the whole snapshot tree
    python -m tools.snapshot_integrity_cli --all

    # CI gate — exits 1 if any snapshot is corrupted or missing files
    python -m tools.snapshot_integrity_cli --all --verify

    # Filter the sweep to recent dates only
    python -m tools.snapshot_integrity_cli --all --since 2026-05-01

    # JSON output for downstream tooling
    python -m tools.snapshot_integrity_cli --all --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from processing.snapshot_integrity import (
    SnapshotIntegrityReport,
    check_all_snapshots,
    check_snapshot_integrity,
    summarize_integrity_run,
)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_text_report(reports: list[SnapshotIntegrityReport]) -> str:
    """Human-readable per-report listing + summary footer."""
    if not reports:
        return "(no snapshots found)"
    lines: list[str] = []
    for r in reports:
        marker = "OK  " if r.ok else "FAIL"
        lines.append(f"[{marker}] {r.date_iso} ({r.container_type})")
        if r.files_missing:
            lines.append(f"        missing: {', '.join(r.files_missing)}")
        if r.files_corrupted:
            lines.append(f"        corrupted: {', '.join(r.files_corrupted)}")
        if r.parse_warnings:
            for w in r.parse_warnings[:3]:
                lines.append(f"        parse: {w}")
            if len(r.parse_warnings) > 3:
                lines.append(f"        ... +{len(r.parse_warnings) - 3} more")
    s = summarize_integrity_run(reports)
    lines.append("")
    lines.append(
        f"Summary: {s['n_ok']}/{s['n_dates_checked']} OK, "
        f"{s['n_missing']} with missing files, "
        f"{s['n_corrupted']} corrupted."
    )
    if s["oldest_problem_date"]:
        lines.append(f"Oldest unhealthy date: {s['oldest_problem_date']}")
    return "\n".join(lines)


def _format_json_report(reports: list[SnapshotIntegrityReport]) -> str:
    """Machine-readable: list of report dicts + summary block."""
    payload = {
        "reports": [asdict(r) for r in reports],
        "summary": summarize_integrity_run(reports),
    }
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Exposed so tools.cli_index can introspect it."""
    parser = argparse.ArgumentParser(
        prog="tools.snapshot_integrity_cli",
        description="Check port-supply snapshot files for completeness + parse warnings.",
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--date",
        help="Check a single ISO date (YYYY-MM-DD).",
    )
    selector.add_argument(
        "--all", action="store_true",
        help="Sweep every snapshot date in the tree.",
    )
    parser.add_argument(
        "--container-type", default="40FT_DRY",
        help="Container type to check (default: 40FT_DRY).",
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Snapshot root override (default: project's cache/).",
    )
    parser.add_argument(
        "--since", default=None,
        help="With --all, only check dates on-or-after this ISO date.",
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json"), default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help=(
            "Exit 1 if any snapshot has missing or corrupted files. "
            "Designed for CI / cron gating."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.date:
        try:
            d = date.fromisoformat(args.date)
        except ValueError:
            print(f"error: bad --date {args.date!r}: expected YYYY-MM-DD",
                  file=sys.stderr)
            return 1
        reports = [check_snapshot_integrity(
            d, container_type=args.container_type, root=args.root,
        )]
    else:
        since = None
        if args.since:
            try:
                since = date.fromisoformat(args.since)
            except ValueError:
                print(f"error: bad --since {args.since!r}: expected YYYY-MM-DD",
                      file=sys.stderr)
                return 1
        reports = check_all_snapshots(
            container_type=args.container_type, root=args.root, since=since,
        )

    formatter = _format_text_report if args.format == "text" else _format_json_report
    print(formatter(reports))

    if args.verify and any(not r.ok for r in reports):
        return 1
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
