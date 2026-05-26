"""tools/port_supply_diff.py — diff two Port Supply Lines snapshots.

Compares two saved per-port summary CSVs (produced by
``utils.port_supply_csv.chains_to_summary_csv`` or
``tools.port_supply_export``) and reports what changed between them:

  * **severity band shifts**     — every port that moved from one
                                   severity band to another, with the
                                   from/to bands and the day delta
  * **deficit-day deltas**       — every port whose ``supply_deficit_days``
                                   moved by more than a configurable
                                   threshold (default ±1.0d)
  * **watchlist transitions**    — ports that *entered* deficit (became
                                   <= 0d) or *exited* it (became > 0d)
                                   between the two snapshots
  * **exposed-ticker changes**   — ports whose top-5 exposed tickers
                                   reshuffled (set-symmetric difference)

Useful for:
  * **morning standup** — diff yesterday vs today, paste the report
    into the deck
  * **incident triage** — diff pre-incident vs post-incident snapshot
    to scope the blast radius
  * **regression checks** — diff a known-good baseline vs a candidate
    snapshot to catch unintended methodology drift

Usage::

    # Default: human-readable text report
    python -m tools.port_supply_diff before.csv after.csv

    # JSON manifest for scripting / Slack-bot ingestion
    python -m tools.port_supply_diff before.csv after.csv --json

    # Markdown for pasting into a ticket / standup deck
    python -m tools.port_supply_diff before.csv after.csv --format markdown

    # Tighter day-delta sensitivity
    python -m tools.port_supply_diff before.csv after.csv --min-delta 0.5

Exit codes:
  0   diff completed successfully (any number of changes reported)
  1   one or both files could not be read / parsed
  2   bad argument
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


__all__ = [
    "PortRow",
    "PortDelta",
    "DiffReport",
    "parse_summary_csv",
    "compare_snapshots",
    "format_text_report",
    "format_json_report",
    "format_markdown_report",
    "main",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PortRow:
    """One row read from a per-port summary CSV — just the fields the
    diff cares about."""

    locode: str
    name: str
    region: str
    supply_deficit_days: float
    severity_label: str
    top_exposed_tickers: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)   # full row, for downstream use


@dataclass
class PortDelta:
    """One port's change between two snapshots."""

    locode: str
    name: str
    region: str
    # Severity band transition
    severity_before: str
    severity_after: str
    severity_shifted: bool
    # Deficit-day shift
    deficit_before: float
    deficit_after: float
    deficit_delta: float        # after - before; negative = worsening
    # Watchlist transitions
    entered_deficit: bool       # was > 0d, now <= 0d
    exited_deficit: bool        # was <= 0d, now > 0d
    # Exposed-ticker reshuffle
    tickers_before: list[str] = field(default_factory=list)
    tickers_after: list[str] = field(default_factory=list)
    tickers_added: list[str] = field(default_factory=list)
    tickers_removed: list[str] = field(default_factory=list)


@dataclass
class DiffReport:
    """Top-level diff result."""

    n_ports_before: int = 0
    n_ports_after: int = 0
    # Ports present in only one snapshot
    locodes_only_in_before: list[str] = field(default_factory=list)
    locodes_only_in_after: list[str] = field(default_factory=list)
    # Per-port changes (intersection only — locodes in both snapshots)
    severity_shifts: list[PortDelta] = field(default_factory=list)
    deficit_moves: list[PortDelta] = field(default_factory=list)
    entered_deficit: list[PortDelta] = field(default_factory=list)
    exited_deficit: list[PortDelta] = field(default_factory=list)
    ticker_shuffles: list[PortDelta] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV parser — accepts the BOM + comment-header layout the exporter emits
# ---------------------------------------------------------------------------


def parse_summary_csv(path: str | Path) -> list[PortRow]:
    """Read a per-port summary CSV (BOM + comment header tolerated).

    Returns one ``PortRow`` per data row. Rows missing the required
    ``locode`` field are skipped silently — they shouldn't exist in a
    well-formed export but the parser stays defensive.
    """
    text = Path(path).read_text(encoding="utf-8")
    # Strip BOM if present
    if text.startswith("﻿"):
        text = text[1:]
    # Drop comment-header lines (start with '# ') before the CSV body
    body_lines = [
        line for line in text.split("\n")
        if line and not line.startswith("# ")
    ]
    if not body_lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(body_lines)))
    out: list[PortRow] = []
    for row in reader:
        locode = (row.get("locode") or "").strip()
        if not locode:
            continue
        # Tickers are pipe-separated in the export ('ZIM | MATX | DAC')
        ticker_str = (row.get("top_exposed_tickers") or "").strip()
        tickers = (
            [t.strip() for t in ticker_str.split("|") if t.strip()]
            if ticker_str else []
        )
        try:
            deficit = float((row.get("supply_deficit_days") or "0").strip())
        except ValueError:
            deficit = 0.0
        out.append(PortRow(
            locode=locode,
            name=row.get("port_name", "") or "",
            region=row.get("region", "") or "",
            supply_deficit_days=deficit,
            severity_label=row.get("severity_label", "") or "",
            top_exposed_tickers=tickers,
            raw=dict(row),
        ))
    return out


# ---------------------------------------------------------------------------
# Pure comparator — testable without the CLI
# ---------------------------------------------------------------------------


def compare_snapshots(
    before: Iterable[PortRow],
    after: Iterable[PortRow],
    *,
    min_delta_days: float = 1.0,
) -> DiffReport:
    """Compare two parsed snapshot row-sets + return a structured diff.

    ``min_delta_days`` filters the deficit_moves list — only ports
    whose ``supply_deficit_days`` moved by more than this absolute
    threshold show up. Severity-band shifts + watchlist transitions
    are always reported regardless of the day-delta size (the band
    boundary itself is the signal).
    """
    by_locode_before = {r.locode: r for r in before}
    by_locode_after = {r.locode: r for r in after}

    report = DiffReport(
        n_ports_before=len(by_locode_before),
        n_ports_after=len(by_locode_after),
        locodes_only_in_before=sorted(
            by_locode_before.keys() - by_locode_after.keys()
        ),
        locodes_only_in_after=sorted(
            by_locode_after.keys() - by_locode_before.keys()
        ),
    )

    # Intersection — every port present in both snapshots
    for locode in sorted(by_locode_before.keys() & by_locode_after.keys()):
        b = by_locode_before[locode]
        a = by_locode_after[locode]
        delta = a.supply_deficit_days - b.supply_deficit_days
        severity_shifted = b.severity_label != a.severity_label
        # Watchlist transitions use 0d as the natural deficit/non-deficit boundary
        was_deficit = b.supply_deficit_days <= 0.0
        now_deficit = a.supply_deficit_days <= 0.0
        entered = (not was_deficit) and now_deficit
        exited = was_deficit and (not now_deficit)

        before_tix = set(b.top_exposed_tickers)
        after_tix = set(a.top_exposed_tickers)
        added = sorted(after_tix - before_tix)
        removed = sorted(before_tix - after_tix)

        pd_obj = PortDelta(
            locode=locode,
            name=a.name or b.name,
            region=a.region or b.region,
            severity_before=b.severity_label,
            severity_after=a.severity_label,
            severity_shifted=severity_shifted,
            deficit_before=b.supply_deficit_days,
            deficit_after=a.supply_deficit_days,
            deficit_delta=delta,
            entered_deficit=entered,
            exited_deficit=exited,
            tickers_before=list(b.top_exposed_tickers),
            tickers_after=list(a.top_exposed_tickers),
            tickers_added=added,
            tickers_removed=removed,
        )

        if severity_shifted:
            report.severity_shifts.append(pd_obj)
        if abs(delta) >= min_delta_days:
            report.deficit_moves.append(pd_obj)
        if entered:
            report.entered_deficit.append(pd_obj)
        if exited:
            report.exited_deficit.append(pd_obj)
        if added or removed:
            report.ticker_shuffles.append(pd_obj)

    # Within each bucket, sort by abs(delta) desc so the most-material
    # changes lead the human-readable report.
    for bucket in (
        report.severity_shifts,
        report.deficit_moves,
        report.entered_deficit,
        report.exited_deficit,
        report.ticker_shuffles,
    ):
        bucket.sort(key=lambda d: abs(d.deficit_delta), reverse=True)

    return report


# ---------------------------------------------------------------------------
# Formatters — text / json / markdown
# ---------------------------------------------------------------------------


def format_text_report(report: DiffReport) -> str:
    lines = [
        "─" * 72,
        "Port Supply Lines — snapshot diff",
        "─" * 72,
        f"Before: {report.n_ports_before} ports · "
        f"After: {report.n_ports_after} ports",
    ]
    if report.locodes_only_in_before:
        lines.append(
            f"Locodes dropped: {', '.join(report.locodes_only_in_before)}"
        )
    if report.locodes_only_in_after:
        lines.append(
            f"Locodes added:   {', '.join(report.locodes_only_in_after)}"
        )
    lines.append("")

    def _section(title: str, deltas: list[PortDelta]) -> None:
        lines.append(f"── {title} ({len(deltas)}) ──")
        if not deltas:
            lines.append("  (none)")
            return
        for d in deltas[:10]:
            lines.append(
                f"  {d.locode:7} {d.name:24} "
                f"{d.severity_before:18} → {d.severity_after:18} "
                f"({d.deficit_before:+5.1f}d → {d.deficit_after:+5.1f}d, "
                f"Δ {d.deficit_delta:+5.1f}d)"
            )
        if len(deltas) > 10:
            lines.append(f"  … and {len(deltas) - 10} more")

    _section("Severity band shifts", report.severity_shifts)
    lines.append("")
    _section("Material deficit-day moves", report.deficit_moves)
    lines.append("")
    _section("Entered deficit", report.entered_deficit)
    lines.append("")
    _section("Exited deficit", report.exited_deficit)
    lines.append("")
    lines.append(f"── Top-ticker reshuffles ({len(report.ticker_shuffles)}) ──")
    if not report.ticker_shuffles:
        lines.append("  (none)")
    else:
        for d in report.ticker_shuffles[:8]:
            added = ", ".join(d.tickers_added) or "—"
            removed = ", ".join(d.tickers_removed) or "—"
            lines.append(
                f"  {d.locode:7} {d.name:24} +[{added}]  -[{removed}]"
            )
        if len(report.ticker_shuffles) > 8:
            lines.append(f"  … and {len(report.ticker_shuffles) - 8} more")
    return "\n".join(lines)


def _delta_to_dict(d: PortDelta) -> dict:
    return {
        "locode":           d.locode,
        "name":             d.name,
        "region":           d.region,
        "severity_before":  d.severity_before,
        "severity_after":   d.severity_after,
        "severity_shifted": d.severity_shifted,
        "deficit_before":   round(d.deficit_before, 4),
        "deficit_after":    round(d.deficit_after, 4),
        "deficit_delta":    round(d.deficit_delta, 4),
        "entered_deficit":  d.entered_deficit,
        "exited_deficit":   d.exited_deficit,
        "tickers_added":    list(d.tickers_added),
        "tickers_removed":  list(d.tickers_removed),
    }


def format_json_report(report: DiffReport) -> str:
    return json.dumps({
        "n_ports_before": report.n_ports_before,
        "n_ports_after":  report.n_ports_after,
        "locodes_only_in_before": list(report.locodes_only_in_before),
        "locodes_only_in_after":  list(report.locodes_only_in_after),
        "severity_shifts":   [_delta_to_dict(d) for d in report.severity_shifts],
        "deficit_moves":     [_delta_to_dict(d) for d in report.deficit_moves],
        "entered_deficit":   [_delta_to_dict(d) for d in report.entered_deficit],
        "exited_deficit":    [_delta_to_dict(d) for d in report.exited_deficit],
        "ticker_shuffles":   [_delta_to_dict(d) for d in report.ticker_shuffles],
    }, indent=2)


def format_markdown_report(report: DiffReport) -> str:
    out: list[str] = [
        "# Port Supply Lines — snapshot diff",
        "",
        f"_Before: {report.n_ports_before} ports · "
        f"After: {report.n_ports_after} ports_",
        "",
    ]

    def _table(title: str, deltas: list[PortDelta]) -> None:
        out.append(f"## {title} ({len(deltas)})")
        if not deltas:
            out.append("_(none)_")
            out.append("")
            return
        out.append("| Locode | Port | Before | After | Δ days |")
        out.append("| --- | --- | --- | --- | ---: |")
        for d in deltas[:15]:
            out.append(
                f"| `{d.locode}` | {d.name} | "
                f"{d.severity_before} ({d.deficit_before:+.1f}d) | "
                f"{d.severity_after} ({d.deficit_after:+.1f}d) | "
                f"{d.deficit_delta:+.1f} |"
            )
        if len(deltas) > 15:
            out.append(f"_… and {len(deltas) - 15} more_")
        out.append("")

    _table("Severity band shifts", report.severity_shifts)
    _table("Material deficit-day moves", report.deficit_moves)
    _table("Entered deficit", report.entered_deficit)
    _table("Exited deficit", report.exited_deficit)

    out.append(f"## Top-ticker reshuffles ({len(report.ticker_shuffles)})")
    if not report.ticker_shuffles:
        out.append("_(none)_")
    else:
        out.append("| Locode | Port | Added | Removed |")
        out.append("| --- | --- | --- | --- |")
        for d in report.ticker_shuffles[:15]:
            added = ", ".join(f"`{t}`" for t in d.tickers_added) or "—"
            removed = ", ".join(f"`{t}`" for t in d.tickers_removed) or "—"
            out.append(
                f"| `{d.locode}` | {d.name} | {added} | {removed} |"
            )
        if len(report.ticker_shuffles) > 15:
            out.append(f"_… and {len(report.ticker_shuffles) - 15} more_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.port_supply_diff",
        description="Diff two Port Supply Lines summary snapshots.",
    )
    parser.add_argument(
        "before",
        help="Path to the earlier summary CSV (e.g. yesterday's export).",
    )
    parser.add_argument(
        "after",
        help="Path to the later summary CSV (e.g. today's export).",
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json", "markdown"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=1.0,
        help=(
            "Minimum |deficit_days| change to count as a 'material' "
            "move (default 1.0). Severity-band shifts + watchlist "
            "transitions are reported regardless."
        ),
    )
    args = parser.parse_args(argv)

    try:
        before = parse_summary_csv(args.before)
        after = parse_summary_csv(args.after)
    except FileNotFoundError as exc:
        print(f"error: could not read snapshot: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: snapshot parse failed: {exc}", file=sys.stderr)
        return 1

    report = compare_snapshots(before, after, min_delta_days=args.min_delta)

    formatter = {
        "text":     format_text_report,
        "json":     format_json_report,
        "markdown": format_markdown_report,
    }[args.format]
    print(formatter(report))
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
