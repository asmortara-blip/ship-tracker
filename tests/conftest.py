"""Shared pytest fixtures for the Ship Tracker suite.

The fixtures here are intentionally small and synthetic — they exercise
code paths without requiring live API access. For realistic-scale parquet
fixtures recorded from a known-good run, see ``tests/fixtures/``.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def freight_data_fixture() -> dict[str, pd.DataFrame]:
    """Synthetic freight rate history for two routes × 90 days."""
    base = date.today() - timedelta(days=90)
    dates = [base + timedelta(days=i) for i in range(90)]

    def _route(rid: str, rate_start: float, slope: float) -> pd.DataFrame:
        return pd.DataFrame({
            "date":       pd.to_datetime(dates),
            "route_id":   rid,
            "rate_usd":   [rate_start + slope * i for i in range(90)],
            "source":     "fixture",
        })
    return {
        "transpacific_eb": _route("transpacific_eb", 2200.0, 3.5),
        "asia_europe":     _route("asia_europe",     1800.0, -2.1),
    }


@pytest.fixture
def route_results_fixture() -> list[dict]:
    """Minimal route result rows — matches the shape consumed by ``tab_routes``."""
    return [
        {
            "route_id":               "transpacific_eb",
            "origin_locode":          "CNSHA",
            "dest_locode":            "USLAX",
            "origin_region":          "APAC",
            "dest_region":            "NAM",
            "transit_days":           14,
            "current_rate_usd_feu":   2850.0,
            "rate_trend":             "Rising",
            "rate_pct_change_30d":    0.045,
            "opportunity_score":      0.72,
            "opportunity_label":      "Strong",
            "rate_momentum_component":        0.8,
            "demand_imbalance_component":     0.65,
            "congestion_clearance_component": 0.70,
            "macro_tailwind_component":       0.55,
            "rationale":              "Strong demand, easing congestion.",
        },
    ]


@pytest.fixture
def insights_fixture() -> list[dict]:
    return [
        {"title": "China exports accelerating", "score": 0.82,
         "action": "Prioritize", "category": "MACRO",
         "rationale": "PMI 52.1, exports +6.3% YoY"},
    ]
