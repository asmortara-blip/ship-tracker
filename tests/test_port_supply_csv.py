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
    CSV_SCHEMA_VERSION,
    SEVERITY_BUCKETS,
    chains_to_deficit_watchlist_csv,
    chains_to_exposure_csv,
    chains_to_regional_rollup_csv,
    chains_to_summary_csv,
    footprints_to_csv,
)


# ── Shared helpers — every CSV from this module carries a BOM + metadata ──

def _split_metadata_and_data(out: str) -> tuple[list[str], str]:
    """Split the BOM + comment header off from the data block so tests
    can validate each separately without the csv.reader tripping on
    comment lines."""
    # Strip BOM if present
    if out.startswith("﻿"):
        out = out[1:]
    lines = out.split("\n")
    meta = [l for l in lines if l.startswith("# ")]
    data_lines = [l for l in lines if not l.startswith("# ") and l != ""]
    return meta, "\n".join(data_lines)


# ── 1. Summary exporter — one row per port ────────────────────────────────

def test_summary_csv_header_and_row_count() -> None:
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    # Header + N port rows
    assert len(rows) == 1 + len(chains)
    # Required columns — including the new derived ones.
    header = rows[0]
    for col in ("locode", "port_name", "supply_deficit_days",
                "severity_label", "n_exposed_companies",
                "top_exposed_tickers",
                "deficit_rank", "regional_avg_deficit",
                "vs_regional_deficit", "exposure_concentration_hhi",
                "total_exposure_weight"):
        assert col in header


def test_summary_csv_includes_top_tickers_inline() -> None:
    """For any port with exposed companies, the top_exposed_tickers
    cell must list at least the first ticker — operators reading the
    CSV in Excel should see WHO is exposed without a JOIN."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    has_top_ticker = False
    for row in rows:
        if row["n_exposed_companies"] != "0" and row["top_exposed_tickers"]:
            has_top_ticker = True
            break
    assert has_top_ticker


def test_summary_csv_empty_input_returns_header_only() -> None:
    out = chains_to_summary_csv([])
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1   # just the header
    assert rows[0][0] == "deficit_rank"


def test_summary_csv_handles_none_input() -> None:
    """None must be treated as empty, not crash."""
    out = chains_to_summary_csv(None)  # type: ignore[arg-type]
    assert "locode" in out
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1


# ── 1b. Derived columns — deficit_rank + regional context + HHI ──────────

def test_summary_csv_deficit_rank_is_unique_and_sequential() -> None:
    """deficit_rank should be 1..N exactly once each, with rank=1 going
    to the most-stressed port (lowest supply_deficit_days)."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    ranks = sorted(int(r["deficit_rank"]) for r in rows)
    assert ranks == list(range(1, len(chains) + 1))
    # The row with rank 1 should also be the row with the
    # lowest supply_deficit_days.
    rank_1 = next(r for r in rows if int(r["deficit_rank"]) == 1)
    min_deficit = min(float(r["supply_deficit_days"]) for r in rows)
    assert float(rank_1["supply_deficit_days"]) == min_deficit


def test_summary_csv_hhi_in_unit_interval() -> None:
    """HHI is bounded in [0, 1] by construction — its lower bound is
    1/n for a perfectly diversified set, upper bound is 1.0 for a
    single-name. Empty port → 0.0 by convention."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        hhi = float(row["exposure_concentration_hhi"])
        assert 0.0 <= hhi <= 1.0


def test_summary_csv_regional_avg_matches_region_mean() -> None:
    """vs_regional_deficit must equal supply_deficit_days - regional_avg.
    Pins the arithmetic so an Excel user reading the column trusts it."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        deficit = float(row["supply_deficit_days"])
        regional = float(row["regional_avg_deficit"])
        vs_reg = float(row["vs_regional_deficit"])
        assert abs(vs_reg - (deficit - regional)) < 1e-9


# ── 2. Exposure exporter — flattened port × company ──────────────────────

def test_exposure_csv_one_row_per_exposed_company() -> None:
    """For each port, the exposure CSV produces one row per exposed
    company (or one empty row if the port has no exposed companies)."""
    chains = build_port_supply_chains()
    expected_rows = 0
    for c in chains:
        expected_rows += max(1, len(c.exposed_companies or []))
    out = chains_to_exposure_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1 + expected_rows  # +1 for header


def test_exposure_csv_carries_port_state_on_every_row() -> None:
    """Each (port, company) row must echo the port's supply state so
    Excel pivot tables don't need a separate port-state JOIN."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    assert rows, "expected at least one row"
    sample = rows[0]
    for col in ("locode", "port_name", "region",
                "supply_deficit_days", "severity_label",
                "ticker", "exposure_weight",
                "share_within_port", "rank_within_port"):
        assert col in sample


def test_exposure_csv_via_commodities_pipe_separated() -> None:
    """The via_commodities field is rendered as 'A | B | C' so it
    survives a re-CSV-import without breaking column alignment."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    found_pipe = False
    for row in rows:
        if " | " in row.get("via_commodities", ""):
            found_pipe = True
            break
    assert found_pipe, "expected at least one row with multiple commodities"


def test_exposure_csv_share_within_port_sums_to_one_per_port() -> None:
    """For any port with multiple exposed companies, the
    share_within_port column must sum to 1.0 (within tolerance) across
    that port's rows — proves the share calculation isn't double-counting
    or dropping mass."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    by_port: dict[str, list[float]] = {}
    for r in rows:
        if r["ticker"]:
            by_port.setdefault(r["locode"], []).append(
                float(r["share_within_port"]),
            )
    # Test at least one multi-company port — the synth has several.
    multi_company = {k: v for k, v in by_port.items() if len(v) >= 2}
    assert multi_company, "expected at least one port with multiple exposed companies"
    for locode, shares in multi_company.items():
        total = sum(shares)
        assert abs(total - 1.0) < 1e-3, (
            f"port {locode}: share_within_port sums to {total}, not 1.0"
        )


def test_exposure_csv_rank_within_port_is_sequential() -> None:
    """rank_within_port must read 1..N per port (heaviest first)."""
    chains = build_port_supply_chains()
    out = chains_to_exposure_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    by_port: dict[str, list[int]] = {}
    for r in rows:
        if r["ticker"]:
            by_port.setdefault(r["locode"], []).append(
                int(r["rank_within_port"]),
            )
    for locode, ranks in by_port.items():
        assert ranks == sorted(ranks)   # ranks ascend within a port
        assert ranks == list(range(1, len(ranks) + 1))   # contiguous 1..N


# ── 3. Footprint exporter — reverse axis ──────────────────────────────────

def test_footprints_csv_one_row_per_ticker_port_pair() -> None:
    footprints = build_company_port_footprints()
    expected = 0
    for fp in footprints:
        expected += max(1, len(fp.port_exposures or []))
    out = footprints_to_csv(footprints)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1 + expected


def test_footprints_csv_repeats_aggregates_per_row() -> None:
    """A single ticker can have many rows (one per port). The
    aggregate columns (total_exposure, deficit_weighted_score, etc.)
    must repeat on every row for the same ticker so spreadsheet
    filters work without a JOIN."""
    footprints = build_company_port_footprints()
    out = footprints_to_csv(footprints)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
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
                        "n_deficit_ports", "n_ports_touched",
                        "deficit_share", "top_region",
                        "concentration_hhi"):
                assert sibling[col] == first[col]


def test_footprints_csv_handles_empty_input() -> None:
    out = footprints_to_csv([])
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1
    assert rows[0][0] == "ticker"


def test_footprints_csv_deficit_share_in_unit_interval() -> None:
    """deficit_share is a fraction in [0, 1]."""
    footprints = build_company_port_footprints()
    out = footprints_to_csv(footprints)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        share = float(row["deficit_share"])
        assert 0.0 <= share <= 1.0


def test_footprints_csv_port_rank_within_footprint_is_sequential() -> None:
    """For every ticker, port_rank_in_footprint reads 1..N — heaviest port
    first. Spreadsheet users sorting by ticker + rank get the canonical
    ordering immediately."""
    footprints = build_company_port_footprints()
    out = footprints_to_csv(footprints)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    by_ticker: dict[str, list[int]] = {}
    for r in rows:
        if r["port_locode"]:
            by_ticker.setdefault(r["ticker"], []).append(
                int(r["port_rank_in_footprint"]),
            )
    for ticker, ranks in by_ticker.items():
        assert ranks == sorted(ranks)
        assert ranks == list(range(1, len(ranks) + 1))


# ── 4. CSV correctness — embedded commas / quotes escape correctly ───────

def test_summary_csv_round_trip_through_dictreader() -> None:
    """Summary fields contain commas (e.g. summary text). The csv.writer
    must escape them so csv.DictReader recovers the original cells."""
    chains = build_port_supply_chains()
    out = chains_to_summary_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
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
    rollup = chains_to_regional_rollup_csv(chains)
    watchlist = chains_to_deficit_watchlist_csv(chains)
    for out in (summary, exposure, footprints, rollup, watchlist):
        assert "\r\n" not in out


# ── 5. Every exporter prefixes UTF-8 BOM + metadata header ───────────────

def test_every_exporter_prefixes_utf8_bom() -> None:
    """Excel-friendly: BOM must be the FIRST character of the file so
    Excel opens in UTF-8 mode."""
    chains = build_port_supply_chains()
    footprints = build_company_port_footprints()
    for out in (
        chains_to_summary_csv(chains),
        chains_to_exposure_csv(chains),
        footprints_to_csv(footprints),
        chains_to_regional_rollup_csv(chains),
        chains_to_deficit_watchlist_csv(chains),
    ):
        assert out.startswith("﻿"), "expected UTF-8 BOM at file start"


def test_every_exporter_emits_metadata_header_block() -> None:
    """Every export carries a comment-style header block with the
    canonical metadata fields."""
    chains = build_port_supply_chains()
    footprints = build_company_port_footprints()
    for out in (
        chains_to_summary_csv(chains, container_type="40FT_DRY"),
        chains_to_exposure_csv(chains),
        footprints_to_csv(footprints),
        chains_to_regional_rollup_csv(chains),
        chains_to_deficit_watchlist_csv(chains),
    ):
        meta, _ = _split_metadata_and_data(out)
        meta_joined = "\n".join(meta)
        assert "view=" in meta_joined
        assert "generated_at_utc=" in meta_joined
        assert "container_type=" in meta_joined
        assert f"schema_version={CSV_SCHEMA_VERSION}" in meta_joined


# ── 6. Regional rollup exporter ──────────────────────────────────────────

def test_regional_rollup_has_one_row_per_region() -> None:
    chains = build_port_supply_chains()
    regions_in = {c.port.region for c in chains}
    out = chains_to_regional_rollup_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    assert len(rows) == len(regions_in)
    assert {r["region"] for r in rows} == regions_in


def test_regional_rollup_min_max_mean_consistency() -> None:
    """For every region row, min <= mean <= max on supply_deficit_days."""
    chains = build_port_supply_chains()
    out = chains_to_regional_rollup_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        lo = float(row["min_supply_deficit_days"])
        mid = float(row["mean_supply_deficit_days"])
        hi = float(row["max_supply_deficit_days"])
        assert lo <= mid <= hi


def test_regional_rollup_severity_bucket_counts_sum_to_n_ports() -> None:
    """Per region, the 5 severity-bucket columns must sum to n_ports."""
    chains = build_port_supply_chains()
    out = chains_to_regional_rollup_csv(chains)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        bucket_total = sum(
            int(row[f"n_{label.lower().replace(' ', '_')}"])
            for label in SEVERITY_BUCKETS
        )
        assert bucket_total == int(row["n_ports"]), (
            f"region {row['region']}: bucket total {bucket_total} "
            f"!= n_ports {row['n_ports']}"
        )


# ── 7. Deficit watchlist exporter ────────────────────────────────────────

def test_watchlist_excludes_ports_above_threshold() -> None:
    """Only ports at or below the threshold show up as rows."""
    chains = build_port_supply_chains()
    out = chains_to_deficit_watchlist_csv(chains, threshold_days=-3.0)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        if row.get("locode"):
            assert float(row["supply_deficit_days"]) <= -3.0


def test_watchlist_action_ladder() -> None:
    """Action column: CRITICAL (<= -10d) → 'Escalate', else 'Monitor'."""
    chains = build_port_supply_chains()
    out = chains_to_deficit_watchlist_csv(chains, threshold_days=0.0)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    for row in rows:
        if not row.get("locode"):
            continue
        deficit = float(row["supply_deficit_days"])
        action = row["action"]
        if deficit <= -10.0:
            assert action == "Escalate"
        else:
            assert action == "Monitor"


def test_watchlist_empty_result_emits_explanatory_comment() -> None:
    """If no port is below the threshold, the exporter still produces a
    header + a comment line explaining the empty result so automated
    pulls don't have to special-case zero-row CSVs."""
    chains = build_port_supply_chains()
    out = chains_to_deficit_watchlist_csv(chains, threshold_days=-1000.0)
    assert "no port currently" in out
    _, data = _split_metadata_and_data(out)
    rows = list(csv.reader(io.StringIO(data)))
    assert len(rows) == 1   # header only


def test_watchlist_ordered_worst_first() -> None:
    """Rank 1 = most-stressed; deficit_days ascend across the rows."""
    chains = build_port_supply_chains()
    out = chains_to_deficit_watchlist_csv(chains, threshold_days=0.0)
    _, data = _split_metadata_and_data(out)
    rows = [r for r in csv.DictReader(io.StringIO(data)) if r.get("locode")]
    deficits = [float(r["supply_deficit_days"]) for r in rows]
    assert deficits == sorted(deficits)
    ranks = [int(r["deficit_rank"]) for r in rows]
    assert ranks == list(range(1, len(ranks) + 1))


def test_footprints_csv_concentration_hhi_matches_full_footprint_value() -> None:
    """Regression: the CSV's concentration_hhi column reports the builder's
    FULL-footprint HHI (fp.concentration_hhi), matching the
    COMPANY_CONCENTRATION alert — not a value recomputed over the top-N-capped
    exposures (which would overstate it; the #8 fix). Formatted .4f, so compare
    within that precision."""
    footprints = build_company_port_footprints()
    out = footprints_to_csv(footprints)
    _, data = _split_metadata_and_data(out)
    rows = list(csv.DictReader(io.StringIO(data)))
    by_ticker = {fp.ticker: fp for fp in footprints}
    seen = 0
    for row in rows:
        fp = by_ticker.get(row["ticker"])
        if fp is None:
            continue
        seen += 1
        assert float(row["concentration_hhi"]) == pytest.approx(
            float(fp.concentration_hhi), abs=1e-4
        )
    assert seen > 0, "expected at least one footprint row"
