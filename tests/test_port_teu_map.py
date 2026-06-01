"""Tests for processing.port_teu_map — per-port TEU + import/export split.

Offline + deterministic: synthetic World Bank-shaped DataFrames (columns
country_iso3 / year / value) + tiny fake ports whose country codes are NOT in
PORT_TRAFFIC_WEIGHTS (so the per-port weight is 1.0 → per-port TEU == country
TEU, fully predictable).
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pandas as pd
import pytest

from processing.port_teu_map import (
    CATEGORIES,
    PortTEU,
    PortTEUTrend,
    aggregate_by_region,
    build_port_teu_map,
    build_port_teu_trends,
    export_share_for_country,
    rank_by_growth,
    rank_ports,
    summarize,
)


def _wb() -> dict:
    return {
        "IS.SHP.GOOD.TU": pd.DataFrame([
            {"country_iso3": "XXA", "year": 2022, "value": 10_000_000},  # 10M TEU
            {"country_iso3": "XXB", "year": 2022, "value": 4_000_000},   # 4M TEU
        ]),
        "TX.VAL.MRCH.CD.WT": pd.DataFrame([          # exports (USD)
            {"country_iso3": "XXA", "year": 2022, "value": 300},
            {"country_iso3": "XXB", "year": 2022, "value": 100},
        ]),
        "TM.VAL.MRCH.CD.WT": pd.DataFrame([          # imports (USD)
            {"country_iso3": "XXA", "year": 2022, "value": 100},
            {"country_iso3": "XXB", "year": 2022, "value": 300},
        ]),
    }


def _ports() -> list:
    return [
        NS(locode="XXAPT", name="Port A", region="RegA", country_iso3="XXA", lat=10.0, lon=20.0),
        NS(locode="XXBPT", name="Port B", region="RegB", country_iso3="XXB", lat=-5.0, lon=30.0),
    ]


def test_build_computes_totals_and_modeled_split() -> None:
    by = {pt.locode: pt for pt in build_port_teu_map(_wb(), ports=_ports())}
    a, b = by["XXAPT"], by["XXBPT"]
    # XXA: 10M TEU → 10.0; export share 300/400 = 0.75
    assert a.teu_total == pytest.approx(10.0)
    assert a.export_share == pytest.approx(0.75)
    assert a.teu_export == pytest.approx(7.5)
    assert a.teu_import == pytest.approx(2.5)
    assert a.teu_net == pytest.approx(5.0)        # net exporter
    # XXB: 4M TEU → 4.0; export share 100/400 = 0.25
    assert b.teu_total == pytest.approx(4.0)
    assert b.export_share == pytest.approx(0.25)
    assert b.teu_net == pytest.approx(-2.0)       # net importer


def test_total_equals_import_plus_export() -> None:
    for pt in build_port_teu_map(_wb(), ports=_ports()):
        assert pt.teu_total == pytest.approx(pt.teu_import + pt.teu_export, abs=1e-6)
        assert pt.teu_net == pytest.approx(pt.teu_export - pt.teu_import, abs=1e-6)
        assert 0.0 <= pt.export_share <= 1.0


def test_missing_trade_data_falls_back_to_balanced_split() -> None:
    wb = {"IS.SHP.GOOD.TU": pd.DataFrame([
        {"country_iso3": "XXA", "year": 2022, "value": 8_000_000}])}
    pt = build_port_teu_map(wb, ports=_ports()[:1])[0]
    assert pt.teu_total == pytest.approx(8.0)
    assert pt.export_share == pytest.approx(0.5)   # default balanced
    assert pt.teu_import == pytest.approx(pt.teu_export)


def test_no_teu_data_yields_zeros() -> None:
    for pt in build_port_teu_map({}, ports=_ports()):
        assert pt.teu_total == 0.0
        assert pt.teu_import == 0.0 and pt.teu_export == 0.0 and pt.teu_net == 0.0


def test_export_share_clamped_and_defaulted() -> None:
    assert export_share_for_country("NONE", {}) == 0.5
    wb = {"TX.VAL.MRCH.CD.WT": pd.DataFrame([{"country_iso3": "Z", "year": 2021, "value": 90}]),
          "TM.VAL.MRCH.CD.WT": pd.DataFrame([{"country_iso3": "Z", "year": 2021, "value": 10}])}
    assert export_share_for_country("Z", wb) == pytest.approx(0.9)


def test_rank_ports_by_category() -> None:
    teus = build_port_teu_map(_wb(), ports=_ports())
    assert [pt.locode for pt in rank_ports(teus, "total")] == ["XXAPT", "XXBPT"]   # 10 > 4
    assert [pt.locode for pt in rank_ports(teus, "net")] == ["XXAPT", "XXBPT"]     # +5 > -2
    assert rank_ports(teus, "total", top_n=1)[0].locode == "XXAPT"


def test_aggregate_by_region_and_summarize() -> None:
    teus = build_port_teu_map(_wb(), ports=_ports())
    agg = aggregate_by_region(teus, "total")
    assert agg == {"RegA": pytest.approx(10.0), "RegB": pytest.approx(4.0)}
    s = summarize(teus)
    assert s["n_ports"] == 2 and s["n_with_data"] == 2
    assert s["teu_total"] == pytest.approx(14.0)
    assert s["teu_export"] == pytest.approx(8.5)   # 7.5 + 1.0
    assert s["teu_import"] == pytest.approx(5.5)   # 2.5 + 3.0
    assert s["teu_net"] == pytest.approx(3.0)      # 5.0 + (-2.0)


def test_value_for_and_provenance() -> None:
    pt = build_port_teu_map(_wb(), ports=_ports()[:1])[0]
    assert pt.value_for("total") == pt.teu_total
    assert pt.value_for("net") == pt.teu_net
    assert pt.value_for("nonsense") == pt.teu_total   # unknown → total
    assert pt.provenance and "modeled" in pt.provenance.lower()
    assert set(CATEGORIES) == {"total", "import", "export", "net"}


def test_default_ports_uses_registry_without_raising() -> None:
    """build with ports=None pulls the real registry; returns a list (possibly
    zeros if no WB cache) and never raises."""
    out = build_port_teu_map({})
    assert isinstance(out, list)
    assert all(isinstance(pt, PortTEU) for pt in out)


# ── Trends over time ──────────────────────────────────────────────────────

def _wb_multiyear() -> dict:
    return {"IS.SHP.GOOD.TU": pd.DataFrame([
        {"country_iso3": "XXA", "year": 2020, "value": 8_000_000},
        {"country_iso3": "XXA", "year": 2021, "value": 9_000_000},
        {"country_iso3": "XXA", "year": 2022, "value": 10_000_000},   # rising
        {"country_iso3": "XXB", "year": 2020, "value": 5_000_000},
        {"country_iso3": "XXB", "year": 2022, "value": 4_000_000},    # declining
    ])}


def test_trends_compute_per_year_teu_and_cagr() -> None:
    by = {t.locode: t for t in build_port_teu_trends(_wb_multiyear(), ports=_ports())}
    a, b = by["XXAPT"], by["XXBPT"]
    assert a.years == [2020, 2021, 2022]
    assert a.teu_by_year == [pytest.approx(8.0), pytest.approx(9.0), pytest.approx(10.0)]
    assert a.cagr_pct == pytest.approx(11.8, abs=0.1)        # (10/8)^(1/2)-1
    assert a.yoy_latest_pct == pytest.approx(11.11, abs=0.1)  # 10/9-1
    assert b.cagr_pct == pytest.approx(-20.0, abs=0.1)        # (4/5)^(1/1)-1
    assert b.yoy_latest_pct == pytest.approx(-20.0, abs=0.1)


def test_trends_single_or_no_year_is_flat() -> None:
    wb = {"IS.SHP.GOOD.TU": pd.DataFrame([
        {"country_iso3": "XXA", "year": 2022, "value": 8_000_000}])}
    t = build_port_teu_trends(wb, ports=_ports()[:1])[0]
    assert t.n_years == 1 and t.cagr_pct == 0.0 and t.yoy_latest_pct == 0.0
    empty = build_port_teu_trends({}, ports=_ports()[:1])[0]
    assert empty.n_years == 0 and empty.teu_by_year == [] and empty.cagr_pct == 0.0


def test_rank_by_growth_orders_by_cagr() -> None:
    trends = build_port_teu_trends(_wb_multiyear(), ports=_ports())
    ranked = rank_by_growth(trends)
    assert [t.locode for t in ranked] == ["XXAPT", "XXBPT"]   # +11.8% > -20%
    assert isinstance(ranked[0], PortTEUTrend)
    # a single-year port is excluded from growth ranking (needs >=2 years)
    assert all(t.n_years >= 2 for t in ranked)
