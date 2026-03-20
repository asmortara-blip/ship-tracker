"""
leading_indicators.py
=====================
Tracks and scores economic leading indicators for shipping demand forecasting.
Uses FRED series IDs as primary keys.  All heavy computation is pure-Python /
pandas so the module imports cleanly even without a live FRED connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# Data-class
# ---------------------------------------------------------------------------

@dataclass
class LeadingIndicator:
    """One economic leading indicator with its current reading and signal."""

    series_id: str           # FRED series ID
    name: str                # Human-readable name
    current_value: float     # Most-recent observation
    previous_value: float    # Observation one period prior
    change_pct: float        # (current - previous) / abs(previous) * 100
    signal: str              # "BULLISH" | "BEARISH" | "NEUTRAL"
    shipping_implication: str  # One-sentence implication for shipping demand
    lead_time_weeks: int     # How many weeks ahead this series leads shipping demand
    weight: float            # Importance weight in composite score (sum to ~1.0)
    data_frequency: str      # "Monthly" | "Weekly" | "Daily"


# ---------------------------------------------------------------------------
# Indicator catalogue
# ---------------------------------------------------------------------------

# fmt: off
LEADING_INDICATORS: Dict[str, dict] = {
    # ── Manufacturing / Production ─────────────────────────────────────────
    "MANEMP": {
        "name": "Manufacturing Employment",
        "lead_time_weeks": 4,
        "weight": 0.12,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Rising manufacturing payrolls signal expanding factory output and"
            " higher demand for raw-material and finished-goods shipping."
        ),
    },
    "AMTMNO": {
        "name": "Manufacturing New Orders",
        "lead_time_weeks": 6,
        "weight": 0.15,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "New orders placed with manufacturers directly translate to future"
            " containerised and bulk cargo volumes 4-8 weeks out."
        ),
    },
    "IPMAN": {
        "name": "Industrial Production — Manufacturing",
        "lead_time_weeks": 2,
        "weight": 0.10,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Accelerating industrial output requires more raw-material inbound"
            " shipments and drives finished-goods export volumes."
        ),
    },
    # ── Consumer / Retail ─────────────────────────────────────────────────
    "UMCSENT": {
        "name": "University of Michigan Consumer Sentiment",
        "lead_time_weeks": 8,
        "weight": 0.08,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Improving consumer confidence presages higher retail spending and"
            " increased import volumes from Asia to North America."
        ),
    },
    "MRTSSM44000USS": {
        "name": "Retail Sales — Total",
        "lead_time_weeks": 6,
        "weight": 0.10,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Strong retail sales drive replenishment orders and container"
            " import demand on trans-Pacific lanes."
        ),
    },
    # ── Freight & Commodity Benchmarks ────────────────────────────────────
    "BSXRLM": {
        "name": "Baltic Dry Index",
        "lead_time_weeks": 0,
        "weight": 0.15,
        "data_frequency": "Daily",
        "inverse_signal": False,
        "shipping_implication": (
            "The BDI is a coincident indicator of bulk-freight demand and"
            " directly reflects dry-bulk spot-rate conditions globally."
        ),
    },
    "DCOILWTICO": {
        "name": "WTI Crude Oil Price",
        "lead_time_weeks": 2,
        "weight": 0.10,
        "data_frequency": "Daily",
        "inverse_signal": True,  # Higher oil = higher costs = BEARISH for margins
        "shipping_implication": (
            "Rising crude prices elevate bunker-fuel costs, compressing"
            " shipping margins and suppressing net freight demand."
        ),
    },
    "PPIACO": {
        "name": "PPI — All Commodities",
        "lead_time_weeks": 4,
        "weight": 0.08,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Broad commodity-price inflation signals higher trade values and"
            " supports increased shipping activity for raw materials."
        ),
    },
    # ── Construction / Housing ────────────────────────────────────────────
    "HOUST": {
        "name": "Housing Starts",
        "lead_time_weeks": 12,
        "weight": 0.06,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Housing-start growth generates demand for lumber, steel, and"
            " appliances, boosting bulk and container shipping 10-14 weeks ahead."
        ),
    },
    "PERMIT": {
        "name": "Building Permits",
        "lead_time_weeks": 14,
        "weight": 0.06,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Building permits are the earliest construction signal, indicating"
            " future material imports and bulk-commodity demand up to 16 weeks out."
        ),
    },
    # ── Labour Market ─────────────────────────────────────────────────────
    "UNRATE": {
        "name": "Unemployment Rate",
        "lead_time_weeks": 8,
        "weight": 0.04,
        "data_frequency": "Monthly",
        "inverse_signal": True,  # Rising unemployment = BEARISH for demand
        "shipping_implication": (
            "A rising unemployment rate suppresses consumer spending and"
            " weakens import-driven shipping demand over the following two months."
        ),
    },
    "IC4WSA": {
        "name": "Initial Jobless Claims (4-Wk MA)",
        "lead_time_weeks": 4,
        "weight": 0.04,
        "data_frequency": "Weekly",
        "inverse_signal": True,
        "shipping_implication": (
            "Surging initial claims are an early warning of labour-market"
            " deterioration that precedes pullbacks in goods trade flows."
        ),
    },
    # ── Trade / Inventory ─────────────────────────────────────────────────
    "ISRATIO": {
        "name": "Manufacturing & Trade Inventory/Sales Ratio",
        "lead_time_weeks": 6,
        "weight": 0.04,
        "data_frequency": "Monthly",
        "inverse_signal": True,  # High ratio = inventory glut = BEARISH for new orders
        "shipping_implication": (
            "A rising inventory-to-sales ratio signals an inventory glut that"
            " will delay new import orders and suppress container volumes."
        ),
    },
    "BOPTEXP": {
        "name": "US Exports of Goods & Services",
        "lead_time_weeks": 4,
        "weight": 0.04,
        "data_frequency": "Monthly",
        "inverse_signal": False,
        "shipping_implication": (
            "Growing US goods exports directly increase outbound container"
            " and breakbulk volumes from North American ports."
        ),
    },
    "DEXCHUS": {
        "name": "China / US Exchange Rate (CNY per USD)",
        "lead_time_weeks": 6,
        "weight": 0.04,
        "data_frequency": "Daily",
        "inverse_signal": True,  # Stronger USD = dearer US imports from China = BEARISH
        "shipping_implication": (
            "A strengthening US dollar makes Chinese exports cheaper, initially"
            " boosting volumes, but may suppress US export competitiveness."
        ),
    },
}
# fmt: on


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest_two(df: pd.DataFrame) -> tuple[float, float]:
    """Return (current, previous) values from a FRED dataframe."""
    if df is None or df.empty or "value" not in df.columns:
        return 0.0, 0.0
    vals = df.sort_values("date")["value"].dropna() if "date" in df.columns else df["value"].dropna()
    if len(vals) == 0:
        return 0.0, 0.0
    current = float(vals.iloc[-1])
    previous = float(vals.iloc[-2]) if len(vals) >= 2 else current
    return current, previous


def _change_pct(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / abs(previous) * 100.0


def _classify_signal(change_pct: float, inverse: bool, threshold: float = 0.5) -> str:
    """Map a percentage change to BULLISH / BEARISH / NEUTRAL, respecting inverse series."""
    if abs(change_pct) < threshold:
        return "NEUTRAL"
    raw_bullish = change_pct > 0
    is_bullish = (raw_bullish and not inverse) or (not raw_bullish and inverse)
    return "BULLISH" if is_bullish else "BEARISH"


def _signal_weight(signal: str) -> float:
    """Map signal string to numeric weight for composite scoring."""
    return {"BULLISH": 1.0, "NEUTRAL": 0.0, "BEARISH": -1.0}.get(signal, 0.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_leading_indicators(macro_data: dict) -> List[LeadingIndicator]:
    """
    Hydrate ``LEADING_INDICATORS`` metadata with live values from *macro_data*.

    Parameters
    ----------
    macro_data:
        Dict mapping FRED series_id -> pd.DataFrame with columns ``date`` and ``value``.

    Returns
    -------
    List of :class:`LeadingIndicator`, one per series found in *macro_data*.
    """
    results: List[LeadingIndicator] = []
    for series_id, meta in LEADING_INDICATORS.items():
        df = macro_data.get(series_id)
        current, previous = _latest_two(df)
        cpct = _change_pct(current, previous)
        signal = _classify_signal(cpct, meta["inverse_signal"])
        results.append(LeadingIndicator(
            series_id=series_id,
            name=meta["name"],
            current_value=current,
            previous_value=previous,
            change_pct=cpct,
            signal=signal,
            shipping_implication=meta["shipping_implication"],
            lead_time_weeks=meta["lead_time_weeks"],
            weight=meta["weight"],
            data_frequency=meta["data_frequency"],
        ))
        logger.debug(
            "LeadingIndicator {sid}: current={cur:.4g} prev={prev:.4g} "
            "chg={chg:+.2f}% signal={sig}",
            sid=series_id, cur=current, prev=previous, chg=cpct, sig=signal,
        )
    return results


def compute_leading_indicator_score(macro_data: dict) -> dict:
    """
    Compute a composite shipping-demand score from available leading indicators.

    Returns
    -------
    dict with keys:
        composite_score     float  0-1
        bullish_count       int
        bearish_count       int
        neutral_count       int
        top_bullish_indicators  list[str]  (series names)
        top_bearish_indicators  list[str]
        four_week_forecast  str   "EXPANSION" | "CONTRACTION" | "STABLE"
        weighted_signal     float  -1 to +1
    """
    indicators = build_leading_indicators(macro_data)
    if not indicators:
        logger.warning("compute_leading_indicator_score: no indicator data available")
        return {
            "composite_score": 0.5,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "top_bullish_indicators": [],
            "top_bearish_indicators": [],
            "four_week_forecast": "STABLE",
            "weighted_signal": 0.0,
        }

    weighted_sum = 0.0
    weight_total = 0.0
    bullish, bearish, neutral = [], [], []

    for ind in indicators:
        sw = _signal_weight(ind.signal) * ind.weight
        weighted_sum += sw
        weight_total += ind.weight
        if ind.signal == "BULLISH":
            bullish.append(ind)
        elif ind.signal == "BEARISH":
            bearish.append(ind)
        else:
            neutral.append(ind)

    weighted_signal = weighted_sum / weight_total if weight_total > 0 else 0.0
    # Map [-1, 1] -> [0, 1]
    composite_score = (weighted_signal + 1.0) / 2.0

    # Sort sub-lists by weight descending for "top N" consumers
    bullish_sorted = sorted(bullish, key=lambda x: x.weight, reverse=True)
    bearish_sorted = sorted(bearish, key=lambda x: x.weight, reverse=True)

    if weighted_signal > 0.15:
        forecast = "EXPANSION"
    elif weighted_signal < -0.15:
        forecast = "CONTRACTION"
    else:
        forecast = "STABLE"

    logger.info(
        "Leading indicator composite: score={s:.3f} weighted_signal={w:+.3f} forecast={f}",
        s=composite_score, w=weighted_signal, f=forecast,
    )
    return {
        "composite_score": round(composite_score, 4),
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "neutral_count": len(neutral),
        "top_bullish_indicators": [i.name for i in bullish_sorted[:3]],
        "top_bearish_indicators": [i.name for i in bearish_sorted[:3]],
        "four_week_forecast": forecast,
        "weighted_signal": round(weighted_signal, 4),
    }


def build_lead_lag_matrix(
    macro_data: dict,
    freight_data: dict | None = None,
) -> pd.DataFrame:
    """
    Cross-correlate each leading indicator with BDI (BSXRLM) at lags
    0, 2, 4, 6, 8, 12 weeks (in trading days: 0, 10, 20, 30, 40, 60).

    Parameters
    ----------
    macro_data:
        Dict mapping FRED series_id -> DataFrame[date, value].
    freight_data:
        Optional dict; if it contains 'BDI' or 'FBX' keys those override BSXRLM.

    Returns
    -------
    pd.DataFrame  rows=indicator names, columns=lag week labels, values=Pearson r.
    """
    LAG_WEEKS = [0, 2, 4, 6, 8, 12]
    LAG_DAYS = [w * 5 for w in LAG_WEEKS]  # approximate trading days

    # Resolve benchmark series (prefer explicit freight_data BDI/FBX)
    benchmark_series: pd.Series | None = None
    if freight_data:
        for key in ("BDI", "FBX", "BSXRLM"):
            bdf = freight_data.get(key)
            if bdf is not None and not bdf.empty and "value" in bdf.columns:
                tmp = bdf.copy()
                if "date" in tmp.columns:
                    tmp = tmp.sort_values("date").set_index("date")
                benchmark_series = tmp["value"].dropna()
                logger.debug("Lead-lag matrix: using {} as benchmark", key)
                break

    if benchmark_series is None:
        bdf = macro_data.get("BSXRLM")
        if bdf is not None and not bdf.empty and "value" in bdf.columns:
            tmp = bdf.copy()
            if "date" in tmp.columns:
                tmp = tmp.sort_values("date").set_index("date")
            benchmark_series = tmp["value"].dropna()

    col_labels = ["Lag " + str(w) + "wk" for w in LAG_WEEKS]
    rows: dict[str, list[float]] = {}

    for series_id, meta in LEADING_INDICATORS.items():
        df = macro_data.get(series_id)
        if df is None or df.empty or benchmark_series is None:
            rows[meta["name"]] = [float("nan")] * len(LAG_WEEKS)
            continue

        ind_df = df.copy()
        if "date" in ind_df.columns:
            ind_df = ind_df.sort_values("date").set_index("date")
        ind_series = ind_df["value"].dropna()

        row_corrs: list[float] = []
        for lag_days in LAG_DAYS:
            try:
                if lag_days == 0:
                    aligned = pd.concat(
                        [ind_series.rename("ind"), benchmark_series.rename("bdi")],
                        axis=1,
                        join="inner",
                    ).dropna()
                else:
                    # Shift the indicator forward (it leads the benchmark)
                    shifted_ind = ind_series.shift(lag_days, freq="D")
                    aligned = pd.concat(
                        [shifted_ind.rename("ind"), benchmark_series.rename("bdi")],
                        axis=1,
                        join="inner",
                    ).dropna()

                if len(aligned) >= 5:
                    r = float(aligned["ind"].corr(aligned["bdi"]))
                    r = round(r, 4) if not np.isnan(r) else float("nan")
                else:
                    r = float("nan")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Lead-lag corr failed for {} lag={}: {}", series_id, lag_days, exc)
                r = float("nan")
            row_corrs.append(r)

        rows[meta["name"]] = row_corrs

    df_out = pd.DataFrame(rows, index=col_labels).T
    df_out.index.name = "Indicator"
    df_out.columns.name = "Lag"
    logger.info("build_lead_lag_matrix: {} indicators x {} lags", len(df_out), len(LAG_WEEKS))
    return df_out


def get_recession_probability(macro_data: dict) -> float:
    """
    Estimate recession probability using two proxies:

    1. Sahm-rule approximation — if the 3-month average unemployment rate
       rises >= 0.5 pp relative to the prior 12-month low, the rule fires.
    2. Yield-curve proxy — uses the unemployment-rate trend slope as a stand-in
       when a genuine spread series is unavailable.

    Returns
    -------
    float in [0, 1] representing estimated recession probability.
    """
    prob_sahm = 0.0
    prob_slope = 0.0

    # ── Sahm Rule (UNRATE) ────────────────────────────────────────────────
    unrate_df = macro_data.get("UNRATE")
    if unrate_df is not None and not unrate_df.empty and "value" in unrate_df.columns:
        ur = unrate_df.sort_values("date")["value"].dropna() if "date" in unrate_df.columns else unrate_df["value"].dropna()
        if len(ur) >= 12:
            ur_3m_avg = float(ur.tail(3).mean())
            ur_12m_low = float(ur.tail(12).min())
            sahm_gap = ur_3m_avg - ur_12m_low
            logger.debug("Sahm gap: {:.3f}", sahm_gap)
            # Probability ramps from 0 at gap=0 to 1.0 at gap>=1.0
            prob_sahm = min(1.0, max(0.0, sahm_gap / 1.0))
        else:
            logger.warning("get_recession_probability: insufficient UNRATE observations ({})", len(ur))
    else:
        logger.warning("get_recession_probability: UNRATE not in macro_data")

    # ── Yield-curve proxy via initial claims trend ─────────────────────────
    claims_df = macro_data.get("IC4WSA")
    if claims_df is not None and not claims_df.empty and "value" in claims_df.columns:
        cl = claims_df.sort_values("date")["value"].dropna() if "date" in claims_df.columns else claims_df["value"].dropna()
        if len(cl) >= 8:
            recent_avg = float(cl.tail(4).mean())
            prior_avg = float(cl.iloc[-8:-4].mean())
            if prior_avg > 0:
                slope_pct = (recent_avg - prior_avg) / prior_avg * 100
                # Probability ramps from 0 at -inf to 1.0 at slope >= +20%
                prob_slope = min(1.0, max(0.0, slope_pct / 20.0))
                logger.debug("Claims slope: {:.2f}% -> prob_slope={:.3f}", slope_pct, prob_slope)

    # Weighted blend: Sahm rule is more reliable
    recession_prob = 0.65 * prob_sahm + 0.35 * prob_slope
    recession_prob = round(min(1.0, max(0.0, recession_prob)), 4)
    logger.info("Recession probability: {:.1%}", recession_prob)
    return recession_prob
