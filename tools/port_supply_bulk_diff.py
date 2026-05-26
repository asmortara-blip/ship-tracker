"""tools/port_supply_bulk_diff.py — aggregate snapshot deltas over a window.

Walks N days of port-supply snapshots, pairs consecutive days, computes the
per-day diff via ``tools.port_supply_diff.compare_snapshots``, and produces
a per-port volatility leaderboard so operators can answer *"what changed
this week?"* in one report.

For each port present in the window, the aggregator counts:

  * ``n_days_in_deficit``           — how many of the N pairs had the
                                      port in ``supply_deficit_days <= 0``
                                      on the **after** side
  * ``cumulative_deficit_day_delta``— sum of ``|deficit_delta|`` across
                                      pairs (movement, regardless of sign)
  * ``n_severity_shifts``           — count of pairs where the port shifted
                                      severity band
  * ``n_entered_deficit``           — count of pairs where the port crossed
                                      from surplus into deficit
  * ``n_exited_deficit``            — count of pairs where the port crossed
                                      back from deficit into surplus
  * ``worst_single_day_delta``      — largest *signed* single-day move
                                      (negative = worst worsening; the
                                      absolute value drives sort)

The CLI emits a ranked leaderboard — most-volatile ports first — in
text, JSON, or markdown.

Usage::

    # Default: last 7 days, 40FT_DRY, text report to stdout
    python -m tools.port_supply_bulk_diff

    # Two-week window, JSON output written to a file
    python -m tools.port_supply_bulk_diff --window-days 14 \\
        --format json --out /tmp/bulk_diff.json

    # Markdown for pasting into a ticket / standup deck
    python -m tools.port_supply_bulk_diff --format markdown

Exit codes:
  0   walk completed (empty leaderboard / clamped window included)
  1   bad argument
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from processing.port_supply_history import (
    _snapshot_filename,
    list_snapshot_dates,
    snapshot_dir_for,
)
from tools.port_supply_diff import (
    DiffReport,
    PortRow,
    compare_snapshots,
    parse_summary_csv,
)


__all__ = [
    "PortVolatilityRow",
    "BulkDiffResult",
    "aggregate_diff_window",
    "build_bulk_diff",
    "format_text_report",
    "format_json_report",
    "format_markdown_report",
    "main",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class PortVolatilityRow:
    """Per-port aggregate stats across the snapshot window.

    Fields are 0 / 0.0 / "" by default so a port that appears in the
    window but never moves still shows up in the iteration without
    needing special-case handling in the aggregator.
    """

    locode: str
    name: str = ""
    region: str = ""
    n_days_in_deficit: int = 0
    cumulative_deficit_day_delta: float = 0.0
    n_severity_shifts: int = 0
    n_entered_deficit: int = 0
    n_exited_deficit: int = 0
    worst_single_day_delta: float = 0.0


@dataclass
class BulkDiffResult:
    """Top-level result for one bulk-diff run.

    ``snapshot_dates`` is the *actual* set of dates walked (may be
    shorter than ``window_days_requested`` when the snapshot history
    is thin — the CLI surfaces that via ``warning``).
    """

    window_days_requested: int = 0
    container_type: str = ""
    snapshot_dates: list[str] = field(default_factory=list)
    n_pairs: int = 0
    leaderboard: list[PortVolatilityRow] = field(default_factory=list)
    warning: str = ""


# ---------------------------------------------------------------------------
# Pure-function aggregator — testable without I/O
# ---------------------------------------------------------------------------


def aggregate_diff_window(
    snapshot_pairs: Sequence[tuple[Sequence[PortRow], Sequence[PortRow]]],
    *,
    min_delta_days: float = 1.0,
) -> list[PortVolatilityRow]:
    """Aggregate per-port volatility stats across a sequence of (before,
    after) snapshot pairs.

    Returns a leaderboard sorted so the most-volatile / most-concerning
    ports come first. The sort key is a tuple chosen so the leaderboard
    surfaces ports that are *both* persistently in deficit AND moving
    materially — single big moves OR persistent presence in the deficit
    band both bubble up.

    ``min_delta_days`` is passed straight through to ``compare_snapshots``
    so the per-pair "material move" filter matches the per-day CLI.
    """
    if not snapshot_pairs:
        return []

    # Per-locode accumulator (so a port can be aggregated across pairs).
    acc: dict[str, PortVolatilityRow] = {}

    for before_rows, after_rows in snapshot_pairs:
        report: DiffReport = compare_snapshots(
            before_rows, after_rows, min_delta_days=min_delta_days,
        )

        # Rebuild a locode → after_row map so we can score deficit
        # presence on the "after" side of each pair (today's state).
        after_by_locode = {r.locode: r for r in after_rows}

        # Count any port that's in deficit on the AFTER side of this
        # pair. That gives "how many days in deficit across the window"
        # which is what operators ask about.
        for locode, row in after_by_locode.items():
            entry = acc.setdefault(
                locode,
                PortVolatilityRow(
                    locode=locode,
                    name=row.name,
                    region=row.region,
                ),
            )
            # Keep the most-recent non-empty name/region we see.
            if row.name and not entry.name:
                entry.name = row.name
            if row.region and not entry.region:
                entry.region = row.region
            if row.supply_deficit_days <= 0.0:
                entry.n_days_in_deficit += 1

        # Walk every per-port delta in this pair's report. We iterate
        # the union of severity_shifts + deficit_moves + transitions
        # because the same port can show in multiple buckets; using a
        # locode set dedupes that.
        all_deltas = (
            list(report.severity_shifts)
            + list(report.deficit_moves)
            + list(report.entered_deficit)
            + list(report.exited_deficit)
        )
        seen_locodes_this_pair: set[str] = set()
        for delta in all_deltas:
            if delta.locode in seen_locodes_this_pair:
                continue
            seen_locodes_this_pair.add(delta.locode)

            entry = acc.setdefault(
                delta.locode,
                PortVolatilityRow(
                    locode=delta.locode,
                    name=delta.name,
                    region=delta.region,
                ),
            )
            if delta.name and not entry.name:
                entry.name = delta.name
            if delta.region and not entry.region:
                entry.region = delta.region

            entry.cumulative_deficit_day_delta += abs(delta.deficit_delta)
            if delta.severity_shifted:
                entry.n_severity_shifts += 1
            if delta.entered_deficit:
                entry.n_entered_deficit += 1
            if delta.exited_deficit:
                entry.n_exited_deficit += 1
            # Track the largest *signed* single-day move — negative
            # values dominate when worsening, positive when easing.
            if abs(delta.deficit_delta) > abs(entry.worst_single_day_delta):
                entry.worst_single_day_delta = delta.deficit_delta

    # Sort: most-concerning ports first. The key combines persistent
    # deficit presence + cumulative movement + transition count so a
    # port that's *both* often-in-deficit AND moving materially leads.
    rows = list(acc.values())
    rows.sort(
        key=lambda r: (
            r.n_days_in_deficit,
            r.cumulative_deficit_day_delta,
            r.n_severity_shifts,
            r.n_entered_deficit + r.n_exited_deficit,
            abs(r.worst_single_day_delta),
        ),
        reverse=True,
    )
    return rows


# ---------------------------------------------------------------------------
# Snapshot-walker — load + pair + aggregate
# ---------------------------------------------------------------------------


def _snapshot_exists(
    snapshot_date: date,
    *,
    container_type: str,
    root: Path | None,
) -> bool:
    """True if the per-port CSV for the given date + container type
    exists under ``root`` (or the default SNAPSHOT_ROOT when root is
    None)."""
    return (
        snapshot_dir_for(snapshot_date, root=root)
        / _snapshot_filename(container_type)
    ).exists()


def build_bulk_diff(
    *,
    window_days: int = 7,
    container_type: str = "40FT_DRY",
    today: date | None = None,
    root: Path | None = None,
    min_delta_days: float = 1.0,
) -> BulkDiffResult:
    """Walk the snapshot history + aggregate per-port volatility stats.

    The window is "the last ``window_days+1`` dated snapshots ending on
    or before ``today``" (we need N+1 snapshots to form N pairs). When
    the history is thinner than that, the result's ``warning`` field is
    populated + ``snapshot_dates`` reflects what was actually walked.

    Returns an always-populated ``BulkDiffResult`` — never raises on
    missing snapshots, just reports an empty leaderboard.
    """
    today = today or datetime.now(timezone.utc).date()
    requested = max(0, int(window_days))
    result = BulkDiffResult(
        window_days_requested=requested,
        container_type=container_type,
    )

    # window_days=0 → no pairs requested. Return early with a clean
    # empty result so callers can render "no changes" instead of an
    # error.
    if requested == 0:
        return result

    # All available snapshot dates with a matching container-type CSV,
    # filtered to those <= today, oldest-first.
    all_dates = [
        d for d in list_snapshot_dates(root=root)
        if d <= today
        and _snapshot_exists(d, container_type=container_type, root=root)
    ]

    # Need at least 2 snapshots to form 1 pair.
    if len(all_dates) < 2:
        result.warning = (
            f"insufficient snapshots for {container_type} "
            f"(have {len(all_dates)}, need at least 2)"
        )
        result.snapshot_dates = [d.isoformat() for d in all_dates]
        return result

    # We want N+1 snapshots to form N pairs. Clamp to what's available
    # and surface the clamp in the warning so callers know they got
    # less than they asked for.
    desired_snapshots = requested + 1
    if len(all_dates) < desired_snapshots:
        result.warning = (
            f"window clamped: requested {requested}d "
            f"({desired_snapshots} snapshots), have {len(all_dates)}"
        )
        used_dates = all_dates
    else:
        used_dates = all_dates[-desired_snapshots:]

    result.snapshot_dates = [d.isoformat() for d in used_dates]

    # Load every used snapshot once + pair consecutive ones.
    parsed: list[list[PortRow]] = []
    for d in used_dates:
        path = (
            snapshot_dir_for(d, root=root) / _snapshot_filename(container_type)
        )
        try:
            parsed.append(parse_summary_csv(path))
        except FileNotFoundError:
            # Already filtered above, but defensive against a race.
            continue

    pairs = list(zip(parsed[:-1], parsed[1:]))
    result.n_pairs = len(pairs)
    result.leaderboard = aggregate_diff_window(
        pairs, min_delta_days=min_delta_days,
    )
    return result


# ---------------------------------------------------------------------------
# Formatters — text / json / markdown
# ---------------------------------------------------------------------------


def format_text_report(result: BulkDiffResult) -> str:
    sep = "-" * 72
    lines = [
        sep,
        "Port Supply Lines - bulk diff (window leaderboard)",
        sep,
        f"Container type: {result.container_type}",
        f"Requested window: {result.window_days_requested}d  "
        f"Pairs walked: {result.n_pairs}",
    ]
    if result.snapshot_dates:
        lines.append(
            f"Dates: {result.snapshot_dates[0]} -> "
            f"{result.snapshot_dates[-1]}"
        )
    if result.warning:
        lines.append(f"Warning: {result.warning}")
    lines.append("")

    if not result.leaderboard:
        lines.append("(no ports in window)")
        return "\n".join(lines)

    lines.append(
        f"{'Locode':7} {'Port':24} {'Days↓':>6} {'Σ|Δ|d':>8} "
        f"{'Shifts':>7} {'Entered':>8} {'Exited':>7} {'Worst Δ':>8}"
    )
    lines.append("-" * 78)
    for row in result.leaderboard[:25]:
        lines.append(
            f"{row.locode:7} {row.name[:24]:24} "
            f"{row.n_days_in_deficit:6d} "
            f"{row.cumulative_deficit_day_delta:8.1f} "
            f"{row.n_severity_shifts:7d} "
            f"{row.n_entered_deficit:8d} "
            f"{row.n_exited_deficit:7d} "
            f"{row.worst_single_day_delta:+8.1f}"
        )
    if len(result.leaderboard) > 25:
        lines.append(f"... and {len(result.leaderboard) - 25} more")
    return "\n".join(lines)


def format_json_report(result: BulkDiffResult) -> str:
    payload = {
        "window_days_requested": result.window_days_requested,
        "container_type":        result.container_type,
        "snapshot_dates":        list(result.snapshot_dates),
        "n_pairs":               result.n_pairs,
        "warning":               result.warning,
        "leaderboard": [
            {
                "locode":                       r.locode,
                "name":                         r.name,
                "region":                       r.region,
                "n_days_in_deficit":            r.n_days_in_deficit,
                "cumulative_deficit_day_delta": round(
                    r.cumulative_deficit_day_delta, 4,
                ),
                "n_severity_shifts":            r.n_severity_shifts,
                "n_entered_deficit":            r.n_entered_deficit,
                "n_exited_deficit":             r.n_exited_deficit,
                "worst_single_day_delta":       round(
                    r.worst_single_day_delta, 4,
                ),
            }
            for r in result.leaderboard
        ],
    }
    return json.dumps(payload, indent=2)


def format_markdown_report(result: BulkDiffResult) -> str:
    out: list[str] = [
        "# Port Supply Lines - bulk diff",
        "",
        f"_Container type: **{result.container_type}** - "
        f"requested **{result.window_days_requested}d** - "
        f"walked **{result.n_pairs}** pairs_",
        "",
    ]
    if result.snapshot_dates:
        out.append(
            f"_Date range: `{result.snapshot_dates[0]}` -> "
            f"`{result.snapshot_dates[-1]}`_"
        )
        out.append("")
    if result.warning:
        out.append(f"> **Warning:** {result.warning}")
        out.append("")

    out.append("## Volatility leaderboard")
    if not result.leaderboard:
        out.append("")
        out.append("_(no ports in window)_")
        return "\n".join(out)

    out.append("")
    out.append(
        "| Locode | Port | Region | Days in deficit | "
        "Σ\\|Δ\\| days | Severity shifts | Entered | Exited | Worst Δ |"
    )
    out.append(
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for r in result.leaderboard[:30]:
        out.append(
            f"| `{r.locode}` | {r.name} | {r.region} | "
            f"{r.n_days_in_deficit} | "
            f"{r.cumulative_deficit_day_delta:.1f} | "
            f"{r.n_severity_shifts} | "
            f"{r.n_entered_deficit} | "
            f"{r.n_exited_deficit} | "
            f"{r.worst_single_day_delta:+.1f} |"
        )
    if len(result.leaderboard) > 30:
        out.append("")
        out.append(f"_... and {len(result.leaderboard) - 30} more_")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.port_supply_bulk_diff",
        description=(
            "Aggregate Port Supply Lines snapshot diffs over a window "
            "(e.g. last week) and rank ports by volatility."
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Number of day-to-day pairs to walk (default 7).",
    )
    parser.add_argument(
        "--container-type",
        default="40FT_DRY",
        help="Container type to walk (default 40FT_DRY).",
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json", "markdown"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write the report to this path instead of stdout.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help=(
            "Snapshot root override (defaults to the SNAPSHOT_ROOT in "
            "processing.port_supply_history). Useful for tests + "
            "ad-hoc replay of an archived snapshot tree."
        ),
    )
    parser.add_argument(
        "--min-delta",
        type=float,
        default=1.0,
        help=(
            "Per-pair minimum |deficit_days| to count as a material "
            "move (default 1.0). Mirrors the --min-delta knob on the "
            "per-day diff CLI."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else None

    result = build_bulk_diff(
        window_days=args.window_days,
        container_type=args.container_type,
        root=root,
        min_delta_days=args.min_delta,
    )

    # Surface clamp / insufficient-snapshot warnings on stderr so the
    # exit code can stay 0 (the run technically succeeded — there was
    # just nothing to diff). Operators piping the stdout payload into
    # a downstream tool don't want a noisy mixed stream.
    if result.warning:
        print(f"warning: {result.warning}", file=sys.stderr)

    formatter = {
        "text":     format_text_report,
        "json":     format_json_report,
        "markdown": format_markdown_report,
    }[args.format]
    output = formatter(result)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
