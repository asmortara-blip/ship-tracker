"""supply_chain_risk.py — Comprehensive supply chain risk assessment.

Computes a composite supply chain risk score from:
  1. Port congestion levels
  2. Freight rate volatility
  3. Geopolitical risk factors
  4. Weather/seasonal risk
  5. Chokepoint concentration risk
  6. Schedule reliability
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger


# ── Risk factor weights ──────────────────────────────────────────────────────
RISK_WEIGHTS = {
    "congestion":    0.20,
    "rate_vol":      0.15,
    "geopolitical":  0.20,
    "weather":       0.10,
    "chokepoint":    0.20,
    "reliability":   0.15,
}

# ── Chokepoint definitions ───────────────────────────────────────────────────
CHOKEPOINTS = [
    {
        "name": "Suez Canal",
        "region": "Middle East",
        "share_global_trade": 12.0,
        "key_routes": ["Asia-Europe", "Asia-Mediterranean"],
        "base_risk": 0.45,  # elevated due to Houthi attacks
        "description": "Critical east-west trade artery handling 12% of global seaborne trade",
    },
    {
        "name": "Panama Canal",
        "region": "Central America",
        "share_global_trade": 5.0,
        "key_routes": ["Asia-USEC", "USWC-Europe"],
        "base_risk": 0.55,  # drought restrictions ongoing
        "description": "Connects Pacific and Atlantic; drought has reduced daily transits",
    },
    {
        "name": "Strait of Malacca",
        "region": "Southeast Asia",
        "share_global_trade": 25.0,
        "key_routes": ["Middle East-Asia", "Africa-Asia"],
        "base_risk": 0.25,
        "description": "World's busiest shipping lane handling 25% of global trade",
    },
    {
        "name": "Strait of Hormuz",
        "region": "Middle East",
        "share_global_trade": 21.0,
        "key_routes": ["Persian Gulf-Global"],
        "base_risk": 0.50,
        "description": "Critical for global oil supply; 21% of petroleum transits",
    },
    {
        "name": "Bab el-Mandeb",
        "region": "Red Sea",
        "share_global_trade": 10.0,
        "key_routes": ["Asia-Europe via Suez"],
        "base_risk": 0.60,  # highest due to active conflict
        "description": "Red Sea gateway; active conflict has forced route diversions",
    },
    {
        "name": "Cape of Good Hope",
        "region": "South Africa",
        "share_global_trade": 8.0,
        "key_routes": ["Asia-Europe (alternative)"],
        "base_risk": 0.20,
        "description": "Alternative to Suez routing; longer transit but lower risk",
    },
]

# ── Geopolitical risk scenarios ──────────────────────────────────────────────
GEO_RISKS = [
    {
        "scenario": "Red Sea / Houthi Disruption",
        "probability": 0.75,
        "impact": "HIGH",
        "affected_routes": ["Asia-Europe", "Asia-Mediterranean"],
        "description": "Ongoing Houthi attacks forcing Suez diversions via Cape of Good Hope. "
                       "Adds 10-14 days and $1M+ in fuel costs per voyage.",
    },
    {
        "scenario": "US-China Trade Escalation",
        "probability": 0.45,
        "impact": "HIGH",
        "affected_routes": ["Trans-Pacific", "Asia-USEC"],
        "description": "Tariff escalation risks significant volume shifts. "
                       "Front-loading effect may temporarily boost then crater volumes.",
    },
    {
        "scenario": "Panama Canal Drought Worsening",
        "probability": 0.35,
        "impact": "MODERATE",
        "affected_routes": ["Asia-USEC", "USWC-Europe"],
        "description": "Further draft restrictions could reduce daily transits below 24, "
                       "forcing more diversions and increasing transit times.",
    },
    {
        "scenario": "EU Emissions Regulation Impact",
        "probability": 0.80,
        "impact": "MODERATE",
        "affected_routes": ["All EU-bound"],
        "description": "EU ETS inclusion of shipping from 2024 adds $50-150/TEU for EU trades. "
                       "May accelerate fleet renewal and slow-steaming adoption.",
    },
    {
        "scenario": "Taiwan Strait Escalation",
        "probability": 0.10,
        "impact": "CRITICAL",
        "affected_routes": ["All Asia-Pacific"],
        "description": "Low probability but catastrophic impact. Would disrupt semiconductor "
                       "supply chains and potentially 30%+ of global container traffic.",
    },
    {
        "scenario": "Port Labor Actions",
        "probability": 0.30,
        "impact": "MODERATE",
        "affected_routes": ["USWC", "Northern Europe"],
        "description": "Periodic port strikes or work slowdowns. US West Coast and "
                       "Northern European ports most exposed.",
    },
]


def compute_supply_chain_risk_score(
    port_results: list,
    freight_data: dict,
    macro_data: dict,
    route_results: list = None,
) -> dict:
    """Compute composite supply chain risk score.

    Returns:
        composite_score: 0-1 (0 = low risk, 1 = critical risk)
        risk_level: LOW / MODERATE / HIGH / CRITICAL
        risk_factors: dict of individual factor scores
        chokepoint_risks: list of chokepoint assessments
        geo_risks: list of geopolitical scenarios
        narrative: editorial risk summary
    """
    risk_factors = {}

    # 1. Congestion risk
    congestion_scores = []
    for pr in (port_results or []):
        cong = getattr(pr, "congestion_index", None)
        if cong is not None:
            congestion_scores.append(cong)
    avg_congestion = sum(congestion_scores) / len(congestion_scores) if congestion_scores else 0.5
    risk_factors["congestion"] = {
        "score": avg_congestion,
        "label": "Port Congestion",
        "detail": f"Average congestion index: {avg_congestion:.2f} across {len(congestion_scores)} ports",
    }

    # 2. Rate volatility
    vol_scores = []
    for route_key, data in (freight_data or {}).items():
        if not isinstance(data, (pd.DataFrame, pd.Series)):
            continue
        try:
            if isinstance(data, pd.DataFrame):
                # Select the rate column by name; column 0 is `date` and would
                # make pct_change() raise on a Timedelta (silently caught here,
                # leaving rate_vol stuck at the default).
                if "rate_usd_per_feu" not in data.columns:
                    continue
                series = pd.to_numeric(data["rate_usd_per_feu"], errors="coerce").dropna()
            else:
                series = pd.to_numeric(data, errors="coerce").dropna()
            if len(series) > 10:
                daily_vol = float(series.pct_change().dropna().std())
                # Normalize: daily vol of 5% = score 1.0
                vol_scores.append(min(1.0, daily_vol / 0.05))
        except Exception:
            pass
    avg_vol = sum(vol_scores) / len(vol_scores) if vol_scores else 0.3
    risk_factors["rate_vol"] = {
        "score": avg_vol,
        "label": "Rate Volatility",
        "detail": f"Normalized volatility: {avg_vol:.2f} ({len(vol_scores)} routes)",
    }

    # 3. Geopolitical risk (from predefined scenarios)
    active_high = sum(1 for g in GEO_RISKS if g["probability"] > 0.5 and g["impact"] in ("HIGH", "CRITICAL"))
    geo_score = min(1.0, active_high * 0.25 + 0.2)  # base 0.2 + 0.25 per active high-impact
    risk_factors["geopolitical"] = {
        "score": geo_score,
        "label": "Geopolitical Risk",
        "detail": f"{active_high} high-probability/high-impact scenarios active",
    }

    # 4. Weather/seasonal
    now = datetime.now(timezone.utc)
    month = now.month
    # Typhoon season (Jun-Nov), winter storms (Dec-Feb)
    if month in (7, 8, 9, 10):
        weather_score = 0.6  # Peak typhoon season
    elif month in (11, 12, 1, 2):
        weather_score = 0.45  # Winter storms
    else:
        weather_score = 0.2  # Low season
    risk_factors["weather"] = {
        "score": weather_score,
        "label": "Weather Risk",
        "detail": f"Seasonal factor for {now.strftime('%B')}: {weather_score:.2f}",
    }

    # 5. Chokepoint concentration
    high_risk_chokepoints = sum(1 for c in CHOKEPOINTS if c["base_risk"] > 0.4)
    chokepoint_score = min(1.0, high_risk_chokepoints / len(CHOKEPOINTS) + 0.1)
    risk_factors["chokepoint"] = {
        "score": chokepoint_score,
        "label": "Chokepoint Risk",
        "detail": f"{high_risk_chokepoints} of {len(CHOKEPOINTS)} chokepoints at elevated risk",
    }

    # 6. Schedule reliability
    # Approximate from port congestion inverse
    reliability_score = 1.0 - (1.0 - avg_congestion) * 0.8  # High congestion = low reliability = high risk
    risk_factors["reliability"] = {
        "score": min(1.0, reliability_score),
        "label": "Schedule Reliability Risk",
        "detail": f"Reliability risk score: {reliability_score:.2f}",
    }

    # Composite
    composite = 0.0
    for key, weight in RISK_WEIGHTS.items():
        if key in risk_factors:
            composite += risk_factors[key]["score"] * weight

    if composite >= 0.70:
        level = "CRITICAL"
    elif composite >= 0.50:
        level = "HIGH"
    elif composite >= 0.30:
        level = "MODERATE"
    else:
        level = "LOW"

    # Narrative
    top_risks = sorted(risk_factors.items(), key=lambda x: x[1]["score"], reverse=True)[:3]
    narrative_parts = []

    if level in ("HIGH", "CRITICAL"):
        narrative_parts.append(
            f"Supply chain risk is {level.lower()}, with a composite score of {composite:.0%}. "
            f"The primary risk drivers are {top_risks[0][1]['label'].lower()} "
            f"and {top_risks[1][1]['label'].lower()}, both registering elevated readings."
        )
    elif level == "MODERATE":
        narrative_parts.append(
            f"Supply chain conditions carry moderate risk at {composite:.0%}. "
            f"While no single factor is critical, {top_risks[0][1]['label'].lower()} "
            f"warrants close monitoring."
        )
    else:
        narrative_parts.append(
            f"Supply chain risk is contained at {composite:.0%}, with no factors "
            f"registering above caution thresholds."
        )

    # Add geopolitical context
    active_geo = [g for g in GEO_RISKS if g["probability"] > 0.3]
    if active_geo:
        top_geo = max(active_geo, key=lambda x: x["probability"])
        narrative_parts.append(
            f"The most probable geopolitical risk remains {top_geo['scenario']} "
            f"at {top_geo['probability']:.0%} probability with {top_geo['impact']} impact potential."
        )

    return {
        "composite_score": composite,
        "risk_level": level,
        "risk_factors": risk_factors,
        "chokepoint_risks": CHOKEPOINTS,
        "geo_risks": GEO_RISKS,
        "narrative": " ".join(narrative_parts),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
