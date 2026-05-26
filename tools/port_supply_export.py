"""tools/port_supply_export.py — CLI bulk-export for Port Supply Lines CSVs.

Dumps any/all of the five ``utils.port_supply_csv`` views to a directory
in one command — useful for:

  * cron-driven daily exports to an internal share for spreadsheet use
  * batch / scripting workflows that don't want to round-trip through
    the Streamlit UI or the authenticated HTTP API
  * one-shot operator pulls during an incident triage

Usage::

    # default: all 5 views, current dir, 40FT_DRY container type
    python -m tools.port_supply_export

    # specific subset + custom output dir
    python -m tools.port_supply_export --views summary,watchlist --out-dir /tmp/snapshots

    # different container type + tighter watchlist threshold
    python -m tools.port_supply_export \\
        --container-type 40FT_REEFER \\
        --threshold-days -10.0

    # quiet mode for cron; JSON manifest for scripting
    python -m tools.port_supply_export --quiet --json

Exit codes:
  0   every requested view written successfully
  1   any view failed to build (others still attempted)
  2   bad argument
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


__all__ = [
    "VIEW_REGISTRY",
    "ExportResult",
    "export_views",
    "main",
]


# Mapping of view-name → (default basename stem, builder factory). The
# factory closes over the args dict so each builder knows the
# container_type / threshold without per-call boilerplate at the
# export site.
def _build_summary(args: dict) -> str:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_summary_csv
    chains = build_port_supply_chains(container_type=args["container_type"])
    return chains_to_summary_csv(chains, container_type=args["container_type"])


def _build_exposure(args: dict) -> str:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_exposure_csv
    chains = build_port_supply_chains(container_type=args["container_type"])
    return chains_to_exposure_csv(chains, container_type=args["container_type"])


def _build_footprint(args: dict) -> str:
    from processing.port_supply_lines import build_company_port_footprints
    from utils.port_supply_csv import footprints_to_csv
    footprints = build_company_port_footprints(
        container_type=args["container_type"],
    )
    return footprints_to_csv(footprints, container_type=args["container_type"])


def _build_regional(args: dict) -> str:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_regional_rollup_csv
    chains = build_port_supply_chains(container_type=args["container_type"])
    return chains_to_regional_rollup_csv(
        chains, container_type=args["container_type"],
    )


def _build_watchlist(args: dict) -> str:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_deficit_watchlist_csv
    chains = build_port_supply_chains(container_type=args["container_type"])
    return chains_to_deficit_watchlist_csv(
        chains,
        container_type=args["container_type"],
        threshold_days=args["threshold_days"],
    )


VIEW_REGISTRY: dict[str, tuple[str, Callable[[dict], str]]] = {
    "summary":   ("port_supply_summary",         _build_summary),
    "exposure":  ("port_company_exposure",       _build_exposure),
    "footprint": ("company_port_footprint",      _build_footprint),
    "regional":  ("port_supply_regional_rollup", _build_regional),
    "watchlist": ("port_deficit_watchlist",      _build_watchlist),
}

# Canonical "everything" alias for --views.
ALL_VIEWS: tuple[str, ...] = tuple(VIEW_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    """One view's export outcome."""

    view: str
    path: str       # absolute path; empty when failed
    bytes_written: int
    ok: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Pure export entry point — testable without subprocess
# ---------------------------------------------------------------------------


def export_views(
    *,
    views: Iterable[str] = ALL_VIEWS,
    out_dir: str | Path = ".",
    container_type: str = "40FT_DRY",
    threshold_days: float = -3.0,
    stamp: str | None = None,
) -> list[ExportResult]:
    """Render the requested views + write each to ``out_dir``.

    Returns one ``ExportResult`` per requested view, in input order.
    Failures on one view do NOT abort the rest — every view is attempted
    and the per-view ok flag carries the outcome. Callers can decide
    how to act on partial failure (the CLI exits 1 if any view failed).

    ``stamp`` is the YYYYMMDD date stamp embedded in filenames. Defaults
    to today's UTC date; tests inject a fixed stamp for determinism.
    """
    requested = list(views) if views else list(ALL_VIEWS)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if stamp is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    args = {
        "container_type": container_type,
        "threshold_days": threshold_days,
    }

    results: list[ExportResult] = []
    for view in requested:
        if view not in VIEW_REGISTRY:
            results.append(ExportResult(
                view=view, path="", bytes_written=0,
                ok=False,
                error=f"unknown view '{view}' (valid: {sorted(VIEW_REGISTRY)})",
            ))
            continue
        stem, builder = VIEW_REGISTRY[view]
        filename = f"{stem}_{container_type.lower()}_{stamp}.csv"
        target = out_dir / filename
        try:
            body = builder(args)
            target.write_text(body, encoding="utf-8")
            results.append(ExportResult(
                view=view,
                path=str(target.resolve()),
                bytes_written=len(body.encode("utf-8")),
                ok=True,
            ))
        except Exception as exc:
            results.append(ExportResult(
                view=view, path="", bytes_written=0,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            ))
    return results


def _export_xlsx(
    *,
    out_dir: str | Path,
    container_type: str,
    threshold_days: float,
    stamp: str | None = None,
) -> ExportResult:
    """Build the single-workbook .xlsx bundle + write it under
    ``out_dir`` with the canonical filename stamp."""
    if stamp is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"port_supply_lines_workbook"
        f"_{container_type.lower()}_{stamp}.xlsx"
    )
    target = out_dir / filename
    try:
        from processing.port_supply_lines import (
            build_company_port_footprints,
            build_port_supply_chains,
        )
        from utils.port_supply_xlsx import build_workbook
        chains = build_port_supply_chains(container_type=container_type)
        footprints = build_company_port_footprints(
            container_type=container_type,
        )
        data = build_workbook(
            chains, footprints,
            container_type=container_type,
            threshold_days=threshold_days,
        )
        target.write_bytes(data)
        return ExportResult(
            view="xlsx_workbook",
            path=str(target.resolve()),
            bytes_written=len(data),
            ok=True,
        )
    except Exception as exc:
        return ExportResult(
            view="xlsx_workbook", path="", bytes_written=0,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_views(raw: str | None) -> list[str]:
    """Parse ``--views`` argument: 'all' / comma-separated subset."""
    if not raw or raw.lower() == "all":
        return list(ALL_VIEWS)
    return [v.strip() for v in raw.split(",") if v.strip()]


def _format_text_report(results: list[ExportResult]) -> str:
    """Human-readable summary for ``--format text`` (default)."""
    width_view = max((len(r.view) for r in results), default=8) + 2
    lines = [
        "─" * 64,
        "Port Supply Lines — CSV bulk export",
        "─" * 64,
    ]
    for r in results:
        flag = "OK " if r.ok else "ERR"
        if r.ok:
            kb = r.bytes_written / 1024.0
            lines.append(
                f"[{flag}] {r.view.ljust(width_view)} {kb:6.1f} KB  {r.path}"
            )
        else:
            lines.append(
                f"[{flag}] {r.view.ljust(width_view)}            {r.error}"
            )
    ok_count = sum(1 for r in results if r.ok)
    lines.append("─" * 64)
    lines.append(f"Summary: {ok_count} of {len(results)} views exported.")
    return "\n".join(lines)


def _format_json_report(results: list[ExportResult]) -> str:
    return json.dumps({
        "ok_count": sum(1 for r in results if r.ok),
        "total":    len(results),
        "results": [
            {
                "view":          r.view,
                "path":          r.path,
                "bytes_written": r.bytes_written,
                "ok":            r.ok,
                "error":         r.error,
            }
            for r in results
        ],
    }, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.port_supply_export",
        description=__doc__.splitlines()[0] if __doc__ else "",
    )
    parser.add_argument(
        "--views",
        default="all",
        help=(
            "Comma-separated view names to export, or 'all' (default). "
            f"Valid: {', '.join(ALL_VIEWS)}."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to write the CSVs into (default: current dir).",
    )
    parser.add_argument(
        "--container-type",
        default="40FT_DRY",
        choices=["40FT_DRY", "20FT_DRY", "40FT_HC",
                 "40FT_REEFER", "20FT_TANK"],
        help="Container-type slice of regional supply state (default: 40FT_DRY).",
    )
    parser.add_argument(
        "--threshold-days",
        type=float,
        default=-3.0,
        help=(
            "Deficit-watchlist firing threshold in days "
            "(negative = deficit; default -3.0)."
        ),
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress stdout (useful for cron). Exit code still signals success.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit a JSON manifest instead of the human-readable text report. "
            "Pairs well with --quiet=false for scripting."
        ),
    )
    parser.add_argument(
        "--xlsx",
        action="store_true",
        help=(
            "Also write a single .xlsx workbook (all 5 sheets bundled) "
            "alongside the CSV files. Useful for analysts who prefer a "
            "single file with sheets they can pivot across."
        ),
    )
    args = parser.parse_args(argv)

    views = _parse_views(args.views)
    unknown = [v for v in views if v not in VIEW_REGISTRY]
    if unknown:
        print(
            f"error: unknown view(s): {unknown} "
            f"(valid: {sorted(VIEW_REGISTRY)})",
            file=sys.stderr,
        )
        return 2

    results = export_views(
        views=views,
        out_dir=args.out_dir,
        container_type=args.container_type,
        threshold_days=args.threshold_days,
    )

    if args.xlsx:
        results.append(_export_xlsx(
            out_dir=args.out_dir,
            container_type=args.container_type,
            threshold_days=args.threshold_days,
        ))

    if not args.quiet:
        if args.json:
            print(_format_json_report(results))
        else:
            print(_format_text_report(results))

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
