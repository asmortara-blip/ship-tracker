"""Tests for processing.supply_chain_risk — composite supply-chain risk score."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from processing.supply_chain_risk import compute_supply_chain_risk_score


@dataclass
class _FakePort:
    locode: str
    congestion_index: float = 0.5


def _freight_df(rates: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=len(rates), freq="D"),
        "rate_usd_per_feu": rates,
    })


# ─── Output shape ───────────────────────────────────────────────────────────

def test_returns_dict_with_required_keys() -> None:
    out = compute_supply_chain_risk_score([], {}, {})
    for key in ("composite_score", "risk_level", "risk_factors",
                "chokepoint_risks", "geo_risks", "narrative"):
        assert key in out


def test_composite_score_in_unit_interval() -> None:
    out = compute_supply_chain_risk_score([], {}, {})
    assert 0.0 <= out["composite_score"] <= 1.0


def test_risk_level_is_valid_label() -> None:
    out = compute_supply_chain_risk_score([], {}, {})
    assert out["risk_level"] in ("LOW", "MODERATE", "HIGH", "CRITICAL")


# ─── Risk factor presence + structure ──────────────────────────────────────

def test_all_six_risk_factors_present() -> None:
    """The implementation produces 6 named risk factors."""
    out = compute_supply_chain_risk_score([], {}, {})
    factors = out["risk_factors"]
    for key in ("congestion", "rate_vol", "geopolitical",
                "weather", "chokepoint", "reliability"):
        assert key in factors
        assert "score" in factors[key]
        assert "label" in factors[key]
        assert "detail" in factors[key]


def test_each_factor_score_in_unit_interval() -> None:
    out = compute_supply_chain_risk_score([], {}, {})
    for factor in out["risk_factors"].values():
        assert 0.0 <= factor["score"] <= 1.0


# ─── Congestion-driven behavior ────────────────────────────────────────────

def test_high_port_congestion_raises_congestion_factor() -> None:
    """Ports averaging ~0.85 congestion → congestion factor near 0.85."""
    ports = [_FakePort(locode="P1", congestion_index=0.85),
             _FakePort(locode="P2", congestion_index=0.90)]
    out = compute_supply_chain_risk_score(ports, {}, {})
    assert out["risk_factors"]["congestion"]["score"] >= 0.80


def test_low_port_congestion_drops_congestion_factor() -> None:
    ports = [_FakePort(locode="P1", congestion_index=0.10)]
    out = compute_supply_chain_risk_score(ports, {}, {})
    assert out["risk_factors"]["congestion"]["score"] <= 0.30


def test_empty_ports_uses_default_congestion() -> None:
    """No port data → default 0.5 fallback."""
    out = compute_supply_chain_risk_score([], {}, {})
    assert out["risk_factors"]["congestion"]["score"] == 0.5


# ─── Rate volatility ───────────────────────────────────────────────────────

def test_freight_data_volatility_picked_up() -> None:
    """Stable rates → low rate_vol score; choppy rates → higher."""
    stable = _freight_df([2000.0] * 60)
    choppy = _freight_df([2000.0 + (-1) ** i * 200 for i in range(60)])
    out_stable = compute_supply_chain_risk_score([], {"r": stable}, {})
    out_choppy = compute_supply_chain_risk_score([], {"r": choppy}, {})
    assert out_choppy["risk_factors"]["rate_vol"]["score"] > out_stable["risk_factors"]["rate_vol"]["score"]


def test_freight_data_missing_rate_column_safely_ignored() -> None:
    """A DataFrame without rate_usd_per_feu is silently skipped."""
    bad = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=20, freq="D"),
                        "other_col": range(20)})
    out = compute_supply_chain_risk_score([], {"r": bad}, {})
    # No crash; rate_vol still has the default ~0.3.
    assert "rate_vol" in out["risk_factors"]


def test_non_dataframe_freight_data_ignored() -> None:
    """Non-DataFrame entries are skipped without raising."""
    out = compute_supply_chain_risk_score([], {"r": [1, 2, 3]}, {})
    assert "rate_vol" in out["risk_factors"]


# ─── Output content ────────────────────────────────────────────────────────

def test_chokepoint_and_geo_risks_lists_populated() -> None:
    """Output exposes the static catalogs verbatim."""
    out = compute_supply_chain_risk_score([], {}, {})
    assert len(out["chokepoint_risks"]) > 0
    assert len(out["geo_risks"]) > 0


def test_narrative_contains_composite_percent() -> None:
    out = compute_supply_chain_risk_score([], {}, {})
    composite_pct = f"{out['composite_score']:.0%}"
    assert composite_pct in out["narrative"]


def test_high_risk_inputs_produce_high_or_critical_label() -> None:
    """All-congested ports + choppy rates should push into HIGH or above."""
    ports = [_FakePort(locode=f"P{i}", congestion_index=0.95) for i in range(5)]
    choppy = _freight_df([2000.0 + (-1) ** i * 500 for i in range(60)])
    out = compute_supply_chain_risk_score(ports, {"r": choppy}, {})
    assert out["risk_level"] in ("HIGH", "CRITICAL", "MODERATE")
