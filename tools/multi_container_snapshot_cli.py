"""tools/multi_container_snapshot_cli.py — fan-out daily snapshot runner.

Drives ``processing.multi_container_snapshot.run_multi_container_snapshot_job``
to persist today's port-supply snapshot for every requested container
type in one shell out. Mirrors the per-container CLI ergonomics (text vs
json output, optional root override) so operators can drop this into
the same cron / launchd / Makefile slot as the single-container helper.

Usage::

    # Default — fan out across DEFAULT_CONTAINER_TYPES
    python -m tools.multi_container_snapshot_cli

    # Only the dry slices
    python -m tools.multi_container_snapshot_cli --container-types 40FT_DRY,20FT_DRY

    # Pin the date (back-fill, tests)
    python -m tools.multi_container_snapshot_cli --today 2026-05-26

    # JSON output for downstream tooling
    python -m tools.multi_container_snapshot_cli --format json

Exit codes:
  0   every per-container snapshot landed cleanly
  1   one or more per-container runs failed (per-container error
      details in the formatted output)
  2   bad CLI argument (unknown container type, malformed --today)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


__all__ = ["main"]


# ---------------------------------------------------------------------------
# Formatters — keep parity with the supply-shock CLI layout (text vs json
# are the two formats the operator dashboard knows how to consume).
# ---------------------------------------------------------------------------


def _format_text(result) -> str:
    """Human-readable summary — delegates to the module-level summarizer
    so the same text appears in logs and on stdout."""
    from processing.multi_container_snapshot import (
        summarize_multi_container_health,
    )
    return summarize_multi_container_health(result)


def _format_json(result) -> str:
    """Machine-readable view — one entry per container with the fields
    downstream tooling needs (ok/fail flag, byte count, snapshot path,
    prior-snapshot date if diffed). Mirrors the per-container JSON shape
    so existing consumers can lift fields without translation."""
    payload = {
        "today_iso":           result.today_iso,
        "total_bytes_written": result.total_bytes_written,
        "any_failed":          result.any_failed,
        "container_count":     len(result.per_container_results),
        "per_container": [
            {
                "container_type":         ct,
                "ok":                     bool(single.ok),
                "bytes_written":          int(single.bytes_written or 0),
                "snapshot_path":          single.snapshot_path,
                "regional_snapshot_path": single.regional_snapshot_path,
                "regional_bytes_written": int(single.regional_bytes_written or 0),
                "prior_snapshot_date":    single.prior_snapshot_date,
                "diff_present":           single.diff is not None,
                "error_msg":              single.error_msg,
            }
            for ct, single in result.per_container_results.items()
        ],
    }
    return json.dumps(payload, indent=2)


_FORMATTERS = {
    "text": _format_text,
    "json": _format_json,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from processing.multi_container_snapshot import (
        DEFAULT_CONTAINER_TYPES,
        run_multi_container_snapshot_job,
    )

    parser = argparse.ArgumentParser(
        prog="tools.multi_container_snapshot_cli",
        description=(
            "Run the daily port-supply snapshot job for every container "
            "type in one pass. Per-container failures don't poison the "
            "rest of the fan-out."
        ),
    )
    parser.add_argument(
        "--container-types",
        default=None,
        help=(
            "Comma-separated list of container types to snapshot "
            "(e.g. '40FT_DRY,40FT_REEFER'). "
            f"Defaults to {','.join(DEFAULT_CONTAINER_TYPES)}."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help=(
            "Override the snapshot tree root. Defaults to "
            "processing.port_supply_history.SNAPSHOT_ROOT."
        ),
    )
    parser.add_argument(
        "--today",
        default=None,
        help=(
            "Snapshot date in YYYY-MM-DD format. Defaults to UTC today."
        ),
    )
    parser.add_argument(
        "--min-diff-delta-days",
        type=float,
        default=1.0,
        help=(
            "Per-port deficit delta threshold for the diff comparator "
            "(default: 1.0)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=sorted(_FORMATTERS.keys()),
        default="text",
        help="Output format (default: text).",
    )
    args = parser.parse_args(argv)

    # Container-type list — comma split + uppercase normalization so the
    # operator can pass lowercase without surprise.
    container_types: list[str] | None
    if args.container_types is None:
        container_types = None
    else:
        raw_parts = [p.strip() for p in args.container_types.split(",")]
        container_types = [p.upper() for p in raw_parts if p]
        if not container_types:
            print(
                "error: --container-types is empty after parsing",
                file=sys.stderr,
            )
            return 2
        # Reject anything not in the equipment_tracker registry — this
        # is a friendlier failure than building chains for a typo'd
        # type and getting an empty result later.
        try:
            from processing.equipment_tracker import CONTAINER_TYPES
            known = set(CONTAINER_TYPES)
            unknown = [c for c in container_types if c not in known]
            if unknown:
                print(
                    f"error: unknown container type(s): {', '.join(unknown)}; "
                    f"known: {', '.join(sorted(known))}",
                    file=sys.stderr,
                )
                return 2
        except Exception:
            # If the registry import fails for any reason, fall through
            # — the underlying job will surface the issue per container.
            pass

    # Today — explicit parse so a bad date returns exit 2 cleanly rather
    # than blowing up inside the job.
    today_obj: date | None
    if args.today is None:
        today_obj = None
    else:
        try:
            today_obj = date.fromisoformat(args.today)
        except ValueError:
            print(
                f"error: --today is not a YYYY-MM-DD date: {args.today!r}",
                file=sys.stderr,
            )
            return 2

    # Root — passed through verbatim; the helper handles None.
    root = Path(args.root) if args.root else None

    result = run_multi_container_snapshot_job(
        container_types=container_types,
        root=root,
        today=today_obj,
        min_diff_delta_days=args.min_diff_delta_days,
    )

    formatter = _FORMATTERS[args.format]
    body = formatter(result)
    print(body)

    return 1 if result.any_failed else 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
