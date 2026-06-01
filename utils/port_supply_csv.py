"""utils/port_supply_csv.py — analytical CSV exporters for Port Supply Lines.

Five exporters covering both raw and aggregated views, each adding
derived analytical columns on top of the underlying joiner output so
the CSV is genuinely useful in a spreadsheet without a downstream join:

  1. ``chains_to_summary_csv``           — one row per port + ranks,
                                           regional context, exposure
                                           concentration (HHI)
  2. ``chains_to_exposure_csv``          — flattened (port × company)
                                           + share_within_port,
                                           share_within_company
  3. ``footprints_to_csv``               — reverse axis (ticker × port)
                                           + deficit_share, top_region,
                                           port_rank_in_footprint
  4. ``chains_to_regional_rollup_csv``   — one row per region with
                                           regional aggregates (port
                                           count, mean / min / max
                                           deficit, severity-bucket
                                           tallies, top exposed tickers
                                           by region)
  5. ``chains_to_deficit_watchlist_csv`` — only ports currently below a
                                           caller-supplied deficit
                                           threshold, action-oriented
                                           columns (action, top
                                           ticker, why)

All outputs:
  * Prefix a UTF-8 BOM (``\\ufeff``) so Excel opens them in UTF-8 mode
    without prompting and special characters in summary text or
    commodity names render correctly.
  * Embed a metadata-comment header (lines starting with ``# ``) carrying
    the generation timestamp, container type, source attribution, and
    schema version, so a CSV pulled hours ago can be audited.
  * Use stdlib ``csv.writer`` with default escaping → embedded commas
    and quotes in summary strings round-trip cleanly.
  * Use ``\\n`` line endings (RFC 4178, cross-platform).
"""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from typing import Iterable


__all__ = [
    "CSV_SCHEMA_VERSION",
    "SEVERITY_BUCKETS",
    "chains_to_summary_csv",
    "chains_to_exposure_csv",
    "footprints_to_csv",
    "chains_to_regional_rollup_csv",
    "chains_to_deficit_watchlist_csv",
]


# Bump on any breaking column-name or column-meaning change. Carried in
# the metadata header block so downstream parsers can branch on it.
CSV_SCHEMA_VERSION: int = 2

# Severity buckets the rollup CSV tallies. Matches the labels exposed
# by processing.port_supply_lines.SEVERITY_LABELS.
SEVERITY_BUCKETS: tuple[str, ...] = (
    "Critical Deficit", "Deficit", "Balanced", "Surplus", "Heavy Surplus",
)


# ---------------------------------------------------------------------------
# Metadata header block — every export starts with these comment lines
# ---------------------------------------------------------------------------


def _metadata_lines(
    *,
    container_type: str = "40FT_DRY",
    view: str = "",
    extra: dict | None = None,
) -> list[str]:
    """Return the comment-style metadata block that prefixes every CSV.

    Comment lines start with ``# `` so a CSV consumer can either skip
    them (most pandas/Excel imports tolerate leading comment lines via
    a ``comment`` kwarg) or parse them as plain text. Keeps the on-disk
    file self-describing so a downloaded CSV remains auditable hours
    later without round-tripping through the UI.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# port_supply_lines export — view={view or 'unspecified'}",
        f"# generated_at_utc={now}",
        f"# container_type={container_type}",
        f"# schema_version={CSV_SCHEMA_VERSION}",
        "# source=processing.port_supply_lines (modeled)",
    ]
    if extra:
        for k, v in extra.items():
            lines.append(f"# {k}={v}")
    return lines


def _open_buffer() -> io.StringIO:
    """Return a StringIO seeded with the UTF-8 BOM so Excel opens the
    file in UTF-8 mode and renders special characters correctly without
    prompting the user."""
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM
    return buf


def _write_metadata(buf: io.StringIO, lines: list[str]) -> None:
    for line in lines:
        buf.write(line + "\n")


# ---------------------------------------------------------------------------
# Derived analytics helpers
# ---------------------------------------------------------------------------


def _hhi(weights: list[float]) -> float:
    """Herfindahl-Hirschman Index over a list of non-negative weights.

    Normalised so each weight is a share of the total, then HHI = Σ
    share². Range: 1/n (perfectly diversified) to 1.0 (single-name
    concentration). Returns 0.0 for an empty list — vacuously
    undefined, but 0.0 sorts intuitively in spreadsheets.
    """
    total = sum(weights)
    if total <= 0 or not weights:
        return 0.0
    shares = [w / total for w in weights]
    return sum(s * s for s in shares)


def _regional_deficit_index(chains: Iterable) -> dict[str, float]:
    """Build a {region: mean_supply_deficit_days} lookup so per-port rows
    can render their position vs. their region's mean."""
    by_region: dict[str, list[float]] = {}
    for c in (chains or []):
        by_region.setdefault(c.port.region, []).append(
            float(c.port.supply_deficit_days)
        )
    return {
        region: (sum(vals) / len(vals)) if vals else 0.0
        for region, vals in by_region.items()
    }


# ---------------------------------------------------------------------------
# Per-port summary — one row per port + derived analytics
# ---------------------------------------------------------------------------

_SUMMARY_HEADERS: list[str] = [
    "deficit_rank",         # 1 = most-stressed (lowest deficit days)
    "locode", "port_name", "region", "country_iso3",
    "lat", "lon",
    "container_type", "supply_deficit_days", "utilization_pct",
    "severity_label",
    "n_routes_touching", "n_exposed_companies",
    # Derived analytical columns
    "regional_avg_deficit",       # mean deficit across all ports in this region
    "vs_regional_deficit",        # this port's deficit minus regional mean
    "exposure_concentration_hhi", # HHI across exposed companies' weights
    "total_exposure_weight",      # sum of exposed-company weights
    "top_exposed_tickers", "summary",
]


def chains_to_summary_csv(
    chains: Iterable,
    *,
    container_type: str = "40FT_DRY",
) -> str:
    """One row per port + ranking + regional context + concentration HHI."""
    chains_list = list(chains or [])
    buf = _open_buffer()
    _write_metadata(buf, _metadata_lines(
        container_type=container_type,
        view="per-port summary",
        extra={"n_rows": len(chains_list)},
    ))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_SUMMARY_HEADERS)

    # Derived inputs computed once for the batch
    regional_means = _regional_deficit_index(chains_list)
    # Rank by deficit_days ascending — most-stressed first → rank 1
    ranked = sorted(
        enumerate(chains_list),
        key=lambda iv: iv[1].port.supply_deficit_days,
    )
    rank_by_idx = {orig_idx: rank for rank, (orig_idx, _) in enumerate(ranked, 1)}

    for idx, chain in enumerate(chains_list):
        port = chain.port
        exposure_weights = [
            float(ce.exposure_weight) for ce in (chain.exposed_companies or [])
        ]
        hhi = _hhi(exposure_weights)
        total_w = sum(exposure_weights)
        regional_mean = regional_means.get(port.region, 0.0)
        vs_regional = float(port.supply_deficit_days) - regional_mean
        top_tickers = " | ".join(
            ce.ticker for ce in (chain.exposed_companies or [])[:5]
        )
        writer.writerow([
            rank_by_idx[idx],
            port.locode,
            port.name,
            port.region,
            getattr(port, "country_iso3", ""),
            f"{float(port.lat):.4f}",
            f"{float(port.lon):.4f}",
            port.container_type,
            f"{float(port.supply_deficit_days):+.2f}",
            f"{float(port.utilization_pct):.1f}",
            port.severity_label,
            len(chain.routes_touching or []),
            len(chain.exposed_companies or []),
            f"{regional_mean:+.2f}",
            f"{vs_regional:+.2f}",
            f"{hhi:.4f}",
            f"{total_w:.6f}",
            top_tickers,
            chain.summary,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Flattened port × company exposure — one row per (port, company) pair
# ---------------------------------------------------------------------------

_EXPOSURE_HEADERS: list[str] = [
    "locode", "port_name", "region", "country_iso3",
    "container_type", "supply_deficit_days", "severity_label",
    "ticker", "exposure_weight",
    # Derived analytical columns
    "share_within_port",      # this ticker's share of total port exposure
    "rank_within_port",       # 1 = top exposed company for this port
    "via_commodities", "via_routes",
]


def chains_to_exposure_csv(
    chains: Iterable,
    *,
    container_type: str = "40FT_DRY",
) -> str:
    """Flattened (port × company) join + per-row share + per-row rank."""
    chains_list = list(chains or [])
    buf = _open_buffer()
    _write_metadata(buf, _metadata_lines(
        container_type=container_type,
        view="port × company exposure (flat)",
        extra={"n_ports": len(chains_list)},
    ))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_EXPOSURE_HEADERS)

    for chain in chains_list:
        port = chain.port
        port_cols = [
            port.locode,
            port.name,
            port.region,
            getattr(port, "country_iso3", ""),
            port.container_type,
            f"{float(port.supply_deficit_days):+.2f}",
            port.severity_label,
        ]
        companies = list(chain.exposed_companies or [])
        if not companies:
            writer.writerow(port_cols + ["", "", "", "", "", ""])
            continue

        # Share-within-port calculation: each company's exposure as a
        # fraction of the port's total exposure weight. Falls back to
        # 0.0 if the total is non-positive (shouldn't happen but
        # defensive for empty / synthetic edge cases).
        total = sum(float(ce.exposure_weight) for ce in companies)
        for rank, ce in enumerate(companies, 1):
            weight = float(ce.exposure_weight)
            share = (weight / total) if total > 0 else 0.0
            writer.writerow(port_cols + [
                ce.ticker,
                f"{weight:.6f}",
                f"{share:.4f}",
                rank,
                " | ".join(ce.via_commodities or []),
                " | ".join(ce.via_routes or []),
            ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Reverse axis — ticker × port footprint
# ---------------------------------------------------------------------------

_FOOTPRINT_HEADERS: list[str] = [
    "ticker", "total_exposure", "deficit_weighted_score",
    "n_deficit_ports", "n_ports_touched",
    # Derived analytical columns
    "deficit_share",          # frac of total exposure in deficit ports
    "top_region",             # region that holds the heaviest port for this ticker
    "concentration_hhi",      # HHI across this ticker's port exposures
    "port_rank_in_footprint", # 1 = heaviest port for this ticker
    "port_locode", "port_name", "region",
    "supply_deficit_days", "severity_label",
    "exposure_weight",
]


def footprints_to_csv(
    footprints: Iterable,
    *,
    container_type: str = "40FT_DRY",
) -> str:
    """Reverse axis (ticker × port) + deficit_share + concentration HHI + top region."""
    footprints_list = list(footprints or [])
    buf = _open_buffer()
    _write_metadata(buf, _metadata_lines(
        container_type=container_type,
        view="ticker × port footprint",
        extra={"n_tickers": len(footprints_list)},
    ))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_FOOTPRINT_HEADERS)

    for fp in footprints_list:
        exposures = list(fp.port_exposures or [])
        exposure_weights = [float(pe.exposure_weight) for pe in exposures]
        total = sum(exposure_weights)
        deficit_total = sum(
            float(pe.exposure_weight) for pe in exposures
            if float(pe.supply_deficit_days) < 0
        )
        deficit_share = (deficit_total / total) if total > 0 else 0.0
        # Prefer the builder's full-footprint HHI so this CSV column matches the
        # COMPANY_CONCENTRATION alert's HHI. The local _hhi() runs over the
        # top-N-CAPPED exposure_weights and would overstate concentration (the
        # #8 fix computes it over every port the ticker touches). Fall back to
        # the local calc for stub footprints lacking the precomputed value.
        hhi = float(getattr(fp, "concentration_hhi", 0.0) or 0.0) or _hhi(exposure_weights)
        top_region = exposures[0].region if exposures else ""

        # Aggregate columns shared across every row of this ticker
        aggregates = [
            fp.ticker,
            f"{float(fp.total_exposure):.6f}",
            f"{float(fp.deficit_weighted_score):.6f}",
            int(fp.n_deficit_ports),
            len(exposures),
            f"{deficit_share:.4f}",
            top_region,
            f"{hhi:.4f}",
        ]
        if not exposures:
            writer.writerow(aggregates + ["", "", "", "", "", "", ""])
            continue
        for port_rank, pe in enumerate(exposures, 1):
            writer.writerow(aggregates + [
                port_rank,
                pe.port_locode,
                pe.port_name,
                pe.region,
                f"{float(pe.supply_deficit_days):+.2f}",
                pe.severity_label,
                f"{float(pe.exposure_weight):.6f}",
            ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Regional rollup — one row per region with aggregate stats
# ---------------------------------------------------------------------------

_REGIONAL_HEADERS: list[str] = [
    "region",
    "n_ports", "n_routes_total", "n_companies_total_unique",
    "mean_supply_deficit_days", "min_supply_deficit_days",
    "max_supply_deficit_days", "mean_utilization_pct",
    *(f"n_{label.lower().replace(' ', '_')}" for label in SEVERITY_BUCKETS),
    "top_exposed_tickers_by_region",
]


def chains_to_regional_rollup_csv(
    chains: Iterable,
    *,
    container_type: str = "40FT_DRY",
) -> str:
    """One row per region with aggregate supply stats + severity-bucket
    counts + top-5 exposed tickers across the region."""
    chains_list = list(chains or [])
    buf = _open_buffer()
    _write_metadata(buf, _metadata_lines(
        container_type=container_type,
        view="regional rollup",
        extra={"n_ports": len(chains_list)},
    ))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_REGIONAL_HEADERS)

    # Bucket by region
    by_region: dict[str, list] = {}
    for c in chains_list:
        by_region.setdefault(c.port.region, []).append(c)

    for region in sorted(by_region.keys()):
        bucket = by_region[region]
        deficits = [float(c.port.supply_deficit_days) for c in bucket]
        utilizations = [float(c.port.utilization_pct) for c in bucket]
        n_routes = sum(len(c.routes_touching or []) for c in bucket)

        # Severity bucket tallies in the same order as SEVERITY_BUCKETS
        severity_counts = {label: 0 for label in SEVERITY_BUCKETS}
        for c in bucket:
            label = c.port.severity_label
            if label in severity_counts:
                severity_counts[label] += 1

        # Top tickers across the region: sum exposure weights per ticker
        # across every port in the region, then take the top 5.
        ticker_totals: dict[str, float] = {}
        for c in bucket:
            for ce in (c.exposed_companies or []):
                ticker_totals[ce.ticker] = ticker_totals.get(
                    ce.ticker, 0.0,
                ) + float(ce.exposure_weight)
        top_tickers = " | ".join(
            ticker for ticker, _ in
            sorted(ticker_totals.items(), key=lambda kv: -kv[1])[:5]
        )

        writer.writerow([
            region,
            len(bucket),
            n_routes,
            len(ticker_totals),
            f"{(sum(deficits) / len(deficits)):+.2f}" if deficits else "+0.00",
            f"{min(deficits):+.2f}" if deficits else "+0.00",
            f"{max(deficits):+.2f}" if deficits else "+0.00",
            f"{(sum(utilizations) / len(utilizations)):.1f}"
                if utilizations else "0.0",
            *(severity_counts[label] for label in SEVERITY_BUCKETS),
            top_tickers,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Deficit watchlist — action-oriented subset for ops triage
# ---------------------------------------------------------------------------

_WATCHLIST_HEADERS: list[str] = [
    "deficit_rank",
    "locode", "port_name", "region",
    "container_type", "supply_deficit_days", "severity_label",
    "n_exposed_companies", "top_exposed_tickers",
    "action", "why",
]


def chains_to_deficit_watchlist_csv(
    chains: Iterable,
    *,
    container_type: str = "40FT_DRY",
    threshold_days: float = -3.0,
) -> str:
    """Action-oriented subset — only ports below ``threshold_days``,
    with a recommended action column and a 'why' explanation.

    Severity-to-action mapping pin:
      * Critical Deficit (<= -10d) → 'Escalate'  — page on-call
      * Deficit (-10 to -3d)       → 'Monitor'   — flag in daily review

    Empty result (no port below threshold) still produces a header row +
    a single comment line stating that nothing is currently in deficit,
    so an automated pull doesn't have to special-case zero-row CSVs.
    """
    chains_list = list(chains or [])
    in_deficit = [
        c for c in chains_list
        if float(c.port.supply_deficit_days) <= threshold_days
    ]
    in_deficit.sort(key=lambda c: c.port.supply_deficit_days)  # worst-first

    buf = _open_buffer()
    _write_metadata(buf, _metadata_lines(
        container_type=container_type,
        view="deficit watchlist",
        extra={
            "threshold_days": f"{threshold_days:+.1f}",
            "n_ports_in_deficit": len(in_deficit),
            "n_ports_scanned": len(chains_list),
        },
    ))
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_WATCHLIST_HEADERS)

    if not in_deficit:
        buf.write(
            f"# (no port currently <= {threshold_days:+.1f}d on "
            f"{container_type} container supply)\n"
        )
        return buf.getvalue()

    for rank, c in enumerate(in_deficit, 1):
        port = c.port
        deficit = float(port.supply_deficit_days)
        action = "Escalate" if deficit <= -10.0 else "Monitor"
        top_tickers = " | ".join(
            ce.ticker for ce in (c.exposed_companies or [])[:5]
        )
        why = (
            f"{port.severity_label} on {container_type}, "
            f"util ~{port.utilization_pct:.0f}%, "
            f"{len(c.routes_touching or [])} route(s) impacted, "
            f"{len(c.exposed_companies or [])} ticker(s) exposed"
        )
        writer.writerow([
            rank,
            port.locode,
            port.name,
            port.region,
            port.container_type,
            f"{deficit:+.2f}",
            port.severity_label,
            len(c.exposed_companies or []),
            top_tickers,
            action,
            why,
        ])
    return buf.getvalue()
