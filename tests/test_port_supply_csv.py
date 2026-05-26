"""Defining-property tests for utils/port_supply_csv.py."""
from __future__ import annotations

import csv
import io

import pytest

from processing.port_supply_lines import (
    build_company_port_footprints,
    build_port_supply_chains,
)
from utils.port_supply_csv import (
    chains_to_exposure_csv,
    chains_to_summary_csv,
    footprints_to_csv,
)


# ── 1. Summary exporter — one row per port ────────────────────────────────

def test_summary_csv_header_and_row_count() -> None:
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    reader = csv.reader(io.StringIO(out))
    rows = list(reader)
    # Header + N port rows
    assert len(rows) == 1 + len(chains)
    # Required columns
    header = rows[0]
    for col in ("locode", "port_name", "supply_deficit_days",
                "severity_label", "n_exposed_companies",
                "top_exposed_tickers"):
        assert col in header


def test_summary_csv_includes_top_tickers_inline() -> None:
    """For any port with exposed companies, the top_exposed_tickers
    cell must list at least the first ticker — operators reading the
    CSV in Excel should see WHO is exposed without a JOIN."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    rows = list(csv.DictReader(io.StringIO(out)))
    has_top_ticker = False
    for row in rows:
        if row["n_exposed_companies"] != "0" and row["top_exposed_tickers"]:
            has_top_ticker = True
            break
    assert has_top_ticker


def test_summary_csv_empty_input_returns_header_only() -> None:
    out = chains_to_summary_csv([])
    reader = csv.reader(io.StringIO(out))
    rows = list(reader)
    assert len(rows) == 1   # just the header
    assert rows[0][0] == "locode"


def test_summary_csv_handles_none_input() -> None:
    """None must be treated as empty, not crash."""
    out = chains_to_summary_csv(None)  # type: ignore[arg-type]
    assert "locode" in out
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 1


# ── 2. Exposure exporter — flattened port × company ──────────────────────

def test_exposure_csv_one_row_per_exposed_company() -> None:
    """For each port, the exposure CSV produces one row per exposed
    company (or one empty row if the port has no exposed companies)."""
    chains = build_port_supply_chains()
    expected_rows = 0
    for c in chains:
        expected_rows += max(1, len(c.exposed_companies or []))
    out = chains_to_exposure_csv(chains)
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 1 + expected_rows  # +1 for header


def test_exposure_csv_carries_port_state_on_every_row() -> None:
    """Each (port, company) row must echo the port's supply state so
    Excel pivot tables don't need a separate port-state JOIN."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows, "expected at least one row"
    sample = rows[0]
    for col in ("locode", "port_name", "region",
                "supply_deficit_days", "severity_label",
                "ticker", "exposure_weight"):
        assert col in sample


def test_exposure_csv_via_commodities_pipe_separated() -> None:
    """The via_commodities field is rendered as 'A | B | C' so it
    survives a re-CSV-import without breaking column alignment."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    rows = list(csv.DictReader(io.StringIO(out)))
    found_pipe = False
    for row in rows:
        if " | " in row.get("via_commodities", ""):
            found_pipe = True
            break
    assert found_pipe, "expected at least one row with multiple commodities"


# ── 3. Footprint exporter — reverse axis ──────────────────────────────────

def test_footprints_csv_one_row_per_ticker_port_pair() -> None:
    footprints = build_company_port_footprints()
    expected = 0
    for fp in footprints:
        expected += max(1, len(fp.port_exposures or []))
    out = footprints_to_csv(footprints)
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 1 + expected


def test_footprints_csv_repeats_aggregates_per_row() -> None:
    """A single ticker can have many rows (one per port). The
    aggregate columns (total_exposure, deficit_weighted_score, etc.)
    must repeat on every row for the same ticker so spreadsheet
    filters work without a JOIN."""
    footprints = build_company_port_footprints()
    out = footprints_to_csv(footprints)
    rows = list(csv.DictReader(io.StringIO(out)))
    # Group by ticker.
    by_ticker: dict[str, list[dict]] = {}
    for row in rows:
        by_ticker.setdefault(row["ticker"], []).append(row)
    # For every multi-row ticker, the aggregate columns must be identical.
    for ticker, group in by_ticker.items():
        if len(group) < 2:
            continue
        first = group[0]
        for sibling in group[1:]:
            for col in ("total_exposure", "deficit_weighted_score",
                        "n_deficit_ports", "n_ports_touched"):
                assert sibling[col] == first[col]


def test_footprints_csv_handles_empty_input() -> None:
    out = footprints_to_csv([])
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) == 1
    assert rows[0][0] == "ticker"


# ── 4. CSV correctness — embedded commas / quotes escape correctly ───────

def test_summary_csv_round_trip_through_dictreader() -> None:
    """Summary fields contain commas (e.g. summary text). The csv.writer
    must escape them so csv.DictReader recovers the original cells."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows
    # Verify summary contains a comma (proves it round-tripped through
    # the escaping layer rather than being silently truncated).
    summary_with_comma = any("," in r["summary"] for r in rows)
    assert summary_with_comma


def test_all_exporters_use_unix_newlines() -> None:
    """RFC 4178 + cross-platform compat — newline='\\n', no CRLF."""
    chains = build_port_supply_chains()
    summary = chains_to_summary_csv(chains)
    exposure = chains_to_exposure_csv(chains)
    footprints = footprints_to_csv(build_company_port_footprints())
    for out in (summary, exposure, footprints):
        assert "\r\n" not in out
