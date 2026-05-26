"""utils/port_supply_csv.py — CSV exporters for the Port Supply Lines data.

Three exporters, each a pure function that takes the relevant
``processing.port_supply_lines`` output and returns a CSV string. No
file I/O — the UI layer pipes the strings into ``st.download_button``
and the CLI layer pipes them to stdout / a path.

Three exporters because the same data answers three operator questions:

  1. ``chains_to_summary_csv(chains)``  — one row per port. Best for
     "give me a spreadsheet ranking ports by surplus/deficit".
  2. ``chains_to_exposure_csv(chains)`` — flattened (port × company)
     join. Best for "load this in Excel + pivot to see which
     companies show up across multiple deficit ports".
  3. ``footprints_to_csv(footprints)``  — reverse axis (ticker × port).
     Best for "given a portfolio, give me each holding's port
     exposure landscape".

All three use the stdlib ``csv.writer`` with default escaping, so
embedded commas / quotes inside summary strings or commodity names are
handled correctly. Encoding is UTF-8 with ``newline=""`` per RFC 4180.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable


__all__ = [
    "chains_to_summary_csv",
    "chains_to_exposure_csv",
    "footprints_to_csv",
]


# ---------------------------------------------------------------------------
# Per-port summary — one row per port
# ---------------------------------------------------------------------------

_SUMMARY_HEADERS: list[str] = [
    "locode", "port_name", "region", "country_iso3",
    "lat", "lon",
    "container_type", "supply_deficit_days", "utilization_pct",
    "severity_label",
    "n_routes_touching", "n_exposed_companies",
    "top_exposed_tickers", "summary",
]


def chains_to_summary_csv(chains: Iterable) -> str:
    """One row per port — the high-level ranking + KPIs view.

    The ``top_exposed_tickers`` column is a comma-separated list of the
    chain's top-5 exposed tickers (in exposure order). That's a pipe-
    free string so it survives even if a downstream tool re-imports
    the CSV without proper quoting.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_SUMMARY_HEADERS)
    for chain in (chains or []):
        port = chain.port
        top_tickers = " | ".join(
            ce.ticker for ce in (chain.exposed_companies or [])[:5]
        )
        writer.writerow([
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
    "via_commodities", "via_routes",
]


def chains_to_exposure_csv(chains: Iterable) -> str:
    """Flattened (port × exposed company) join.

    Each row pairs one port with one of its exposed companies +
    carries the port's supply state on the same row so a pivot-table
    user can group by ticker and sum across deficit-stressed ports.

    Ports with zero exposed companies are still represented with a
    single row where the ticker / weight / via_* columns are empty —
    a downstream filter on ``ticker != ""`` drops them.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_EXPOSURE_HEADERS)
    for chain in (chains or []):
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
            writer.writerow(port_cols + ["", "", "", ""])
            continue
        for ce in companies:
            writer.writerow(port_cols + [
                ce.ticker,
                f"{float(ce.exposure_weight):.6f}",
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
    "port_locode", "port_name", "region",
    "supply_deficit_days", "severity_label",
    "exposure_weight",
]


def footprints_to_csv(footprints: Iterable) -> str:
    """One row per (ticker, port) pair from the reverse footprint view.

    Footprint-level aggregates (total_exposure, deficit_weighted_score,
    n_deficit_ports, n_ports_touched) repeat on every row for the same
    ticker so a spreadsheet user can filter to one ticker and still
    see the aggregate context without a JOIN.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_FOOTPRINT_HEADERS)
    for fp in (footprints or []):
        aggregates = [
            fp.ticker,
            f"{float(fp.total_exposure):.6f}",
            f"{float(fp.deficit_weighted_score):.6f}",
            int(fp.n_deficit_ports),
            len(fp.port_exposures or []),
        ]
        exposures = list(fp.port_exposures or [])
        if not exposures:
            writer.writerow(aggregates + ["", "", "", "", "", ""])
            continue
        for pe in exposures:
            writer.writerow(aggregates + [
                pe.port_locode,
                pe.port_name,
                pe.region,
                f"{float(pe.supply_deficit_days):+.2f}",
                pe.severity_label,
                f"{float(pe.exposure_weight):.6f}",
            ])
    return buf.getvalue()
