"""tools/forecast_accuracy_cli.py — query the forecast accuracy log.

Wraps ``processing.forecast_accuracy_tracker`` so operators (and CI) can
query the pairing summary without writing Python. Reads from the
on-disk JSONL store at ``cache/forecast_log/`` (or a ``--root``
override for testing).

Usage::

    # MAE summary over the last 60 days for every lane in the store.
    python -m tools.forecast_accuracy_cli

    # Narrow to one lane.
    python -m tools.forecast_accuracy_cli --lane transpacific_eb

    # JSON output for downstream dashboards.
    python -m tools.forecast_accuracy_cli --format json

    # Tight 14-day window for a sharper recent picture.
    python -m tools.forecast_accuracy_cli --window-days 14

    # Pin "today" — useful in tests + replaying old data.
    python -m tools.forecast_accuracy_cli --today 2026-05-26
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from processing.forecast_accuracy_tracker import (
    match_forecasts_to_actuals,
    summarize_accuracy,
)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_text(summary: dict, rows: list, lane: str | None) -> str:
    """Plain-text summary suitable for the terminal + the operator digest."""
    lane_label = lane or "(all lanes)"
    lines = [
        f"Forecast Accuracy — {lane_label}",
        "=" * 56,
        f"Pairs scored: {summary['n_pairs']}",
        f"MAE:          {summary['mae']:.4f}",
    ]
    # Optional fields the summary may or may not carry — render only
    # when populated so the output stays compact.
    for key, label in [
        ("mae_by_horizon",       "MAE by horizon"),
        ("sign_agreement_rate",  "Sign agreement"),
        ("mean_abs_error",       "Mean |error|"),
    ]:
        if key in summary and summary[key] is not None:
            value = summary[key]
            if isinstance(value, dict):
                # Skip None values inside the per-horizon dict — empty
                # horizons surface as None and don't need to render.
                pretty = ", ".join(
                    f"{k}={v:.3f}" for k, v in sorted(value.items())
                    if v is not None
                )
                if pretty:
                    lines.append(f"{label:14} {pretty}")
            elif isinstance(value, float):
                lines.append(f"{label:14} {value:.4f}")
            else:
                lines.append(f"{label:14} {value}")
    if not rows:
        lines.append("")
        lines.append("(no paired rows — nothing to score in this window)")
    return "\n".join(lines)


def _format_json(summary: dict, rows: list, lane: str | None) -> str:
    """Machine-readable: summary + per-row breakdown."""
    payload = {
        "lane":    lane,
        "summary": summary,
        "rows":    [
            {
                "forecast_date_iso": r.forecast_date_iso,
                "target_date_iso":   r.target_date_iso,
                "horizon_days":      r.horizon_days,
                "lane_id":           r.lane_id,
                "predicted":         r.predicted,
                "actual":            r.actual,
                "error":             r.error,
                "abs_error":         r.abs_error,
                "signed_error":      r.signed_error,
            }
            for r in rows
        ],
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Exposed so tools.cli_index can introspect it."""
    parser = argparse.ArgumentParser(
        prog="tools.forecast_accuracy_cli",
        description=(
            "Query the forecast accuracy log: pair logged forecasts to "
            "actuals by horizon + summarise MAE / sign-agreement."
        ),
    )
    parser.add_argument(
        "--lane",
        help=(
            "Filter to a single lane id (e.g. 'transpacific_eb', 'fleet'). "
            "Default: aggregate every lane in the store."
        ),
    )
    parser.add_argument(
        "--window-days", type=int, default=60,
        help=(
            "Only score forecasts whose target date is within this many "
            "days of --today (default 60). Set very large to score "
            "the entire history."
        ),
    )
    parser.add_argument(
        "--today", default=None,
        help=(
            "Anchor 'today' as an ISO YYYY-MM-DD date (default: "
            "actual UTC today). Useful for tests + historical replays."
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=None,
        help="Forecast log root override (default: project's cache/).",
    )
    parser.add_argument(
        "--format", "-f",
        choices=("text", "json"), default="text",
        help="Output format (default: text).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    anchor: date | None = None
    if args.today:
        try:
            anchor = date.fromisoformat(args.today)
        except ValueError:
            print(f"error: bad --today {args.today!r}: expected YYYY-MM-DD",
                  file=sys.stderr)
            return 1

    try:
        rows = match_forecasts_to_actuals(
            window_days=args.window_days,
            root=args.root,
            today=anchor,
        )
    except Exception as exc:
        print(f"error: failed to read forecast log: {exc}", file=sys.stderr)
        return 1

    if args.lane:
        rows = [r for r in rows if r.lane_id == args.lane]

    summary = summarize_accuracy(rows)

    formatter = _format_text if args.format == "text" else _format_json
    print(formatter(summary, rows, args.lane))
    return 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
