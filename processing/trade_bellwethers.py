"""Trade bellwether indicators — early-warning signals from leading economic data.

Combines yield curve, PMI, housing starts, consumer sentiment, and trade balance
into a composite shipping demand leading indicator score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger


# ── Indicator weights (sum to 1.0) ──────────────────────────────────────────
INDICATOR_WEIGHTS = {
    "yield_curve":       0.20,   # 10Y-2Y spread: inversion signals recession
    "pmi":               0.20,   # ISM Manufacturing PMI: > 50 expansion
    "housing_starts":    0.10,   # Leads container imports by 4-6 months
    "consumer_sentiment":0.10,   # UMich sentiment drives retail imports
    "trade_balance":     0.15,   # Goods deficit widening = more imports
    "industrial_prod":   0.15,   # Manufacturing output proxy
    "durable_goods":     0.10,   # New orders signal future trade
}


def _safe_last(df: pd.DataFrame | pd.Series | None) -> float | None:
    """Extract last non-NaN value from a DataFrame or Series."""
    if df is None:
        return None
    try:
        if isinstance(df, pd.DataFrame):
            if df.empty:
                return None
            # FRED frames are [date, value]; select by name so column 0 (date)
            # is never grabbed by accident.
            col = "value" if "value" in df.columns else df.columns[-1]
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
            return float(vals.iloc[-1]) if len(vals) > 0 else None
        if isinstance(df, pd.Series):
            vals = pd.to_numeric(df, errors="coerce").dropna()
            return float(vals.iloc[-1]) if len(vals) > 0 else None
    except Exception:
        return None
    return None


def _safe_pct_change(df: pd.DataFrame | pd.Series | None, periods: int = 1) -> float | None:
    """Get percentage change over N periods."""
    if df is None:
        return None
    try:
        if isinstance(df, pd.DataFrame):
            if df.empty:
                return None
            col = "value" if "value" in df.columns else df.columns[-1]
            vals = pd.to_numeric(df[col], errors="coerce").dropna()
        else:
            vals = pd.to_numeric(df, errors="coerce").dropna()

        if len(vals) < periods + 1:
            return None
        curr = float(vals.iloc[-1])
        prev = float(vals.iloc[-(periods + 1)])
        if prev == 0:
            return None
        return (curr - prev) / abs(prev)
    except Exception:
        return None


def _normalize_score(value: float | None, low: float, high: float) -> float:
    """Normalize a raw value to 0-1 score."""
    if value is None:
        return 0.5  # neutral when missing
    clamped = max(low, min(high, value))
    return (clamped - low) / (high - low) if high != low else 0.5


def compute_bellwether_score(macro_data: dict) -> dict:
    """Compute composite trade bellwether score from macro data.

    Returns dict with:
        - composite_score: 0-1 overall health
        - composite_label: "Expansion" / "Neutral" / "Contraction"
        - indicators: dict of individual indicator scores and raw values
        - narrative: editorial summary string
        - timestamp: ISO timestamp
    """
    if not macro_data:
        return {
            "composite_score": 0.5,
            "composite_label": "No Data",
            "indicators": {},
            "narrative": "Insufficient data to compute trade bellwether score.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    indicators = {}

    # 1. Yield Curve (T10Y2Y)
    yc_val = _safe_last(macro_data.get("T10Y2Y"))
    yc_score = _normalize_score(yc_val, -1.0, 2.5)  # -1% inverted to +2.5% steep
    indicators["yield_curve"] = {
        "raw": yc_val,
        "score": yc_score,
        "label": "10Y-2Y Spread",
        "unit": "%",
        "interpretation": "Inverted" if yc_val is not None and yc_val < 0 else (
            "Flat" if yc_val is not None and yc_val < 0.5 else "Normal"),
    }

    # 2. PMI
    pmi_val = _safe_last(macro_data.get("NAPMPI"))
    pmi_score = _normalize_score(pmi_val, 40, 65)
    indicators["pmi"] = {
        "raw": pmi_val,
        "score": pmi_score,
        "label": "ISM Manufacturing PMI",
        "unit": "",
        "interpretation": "Expansion" if pmi_val is not None and pmi_val > 50 else "Contraction",
    }

    # 3. Housing Starts
    hs_val = _safe_last(macro_data.get("HOUST"))
    hs_chg = _safe_pct_change(macro_data.get("HOUST"), 3)
    hs_score = _normalize_score(hs_chg, -0.15, 0.15) if hs_chg is not None else (
        _normalize_score(hs_val, 800, 1800) if hs_val else 0.5)
    indicators["housing_starts"] = {
        "raw": hs_val,
        "score": hs_score,
        "label": "Housing Starts",
        "unit": "K ann.",
        "interpretation": "Growing" if hs_chg is not None and hs_chg > 0 else "Declining",
    }

    # 4. Consumer Sentiment
    cs_val = _safe_last(macro_data.get("UMCSENT"))
    cs_score = _normalize_score(cs_val, 50, 110)
    indicators["consumer_sentiment"] = {
        "raw": cs_val,
        "score": cs_score,
        "label": "UMich Consumer Sentiment",
        "unit": "",
        "interpretation": "Optimistic" if cs_val is not None and cs_val > 80 else "Cautious",
    }

    # 5. Trade Balance (more negative = more imports = bullish for shipping)
    tb_val = _safe_last(macro_data.get("BOPGSTB"))
    # Invert: larger deficit (more negative) = more shipping demand
    tb_score = _normalize_score(tb_val, -120, -40) if tb_val is not None else 0.5
    tb_score = 1.0 - tb_score  # Invert so larger deficit = higher score
    indicators["trade_balance"] = {
        "raw": tb_val,
        "score": tb_score,
        "label": "Goods Trade Balance",
        "unit": "$B",
        "interpretation": "Wide deficit (bullish shipping)" if tb_val is not None and tb_val < -70 else "Narrow deficit",
    }

    # 6. Industrial Production
    ip_val = _safe_last(macro_data.get("IPMAN"))
    ip_chg = _safe_pct_change(macro_data.get("IPMAN"), 3)
    ip_score = _normalize_score(ip_chg, -0.05, 0.05) if ip_chg is not None else 0.5
    indicators["industrial_prod"] = {
        "raw": ip_val,
        "score": ip_score,
        "label": "Manufacturing Output",
        "unit": "idx",
        "interpretation": "Expanding" if ip_chg is not None and ip_chg > 0 else "Contracting",
    }

    # 7. Durable Goods Orders
    dg_val = _safe_last(macro_data.get("DGORDER"))
    dg_chg = _safe_pct_change(macro_data.get("DGORDER"), 1)
    dg_score = _normalize_score(dg_chg, -0.10, 0.10) if dg_chg is not None else 0.5
    indicators["durable_goods"] = {
        "raw": dg_val,
        "score": dg_score,
        "label": "Durable Goods Orders",
        "unit": "$M",
        "interpretation": "Growing" if dg_chg is not None and dg_chg > 0 else "Declining",
    }

    # Composite
    composite = 0.0
    total_weight = 0.0
    for key, weight in INDICATOR_WEIGHTS.items():
        if key in indicators:
            composite += indicators[key]["score"] * weight
            total_weight += weight
    if total_weight > 0:
        composite /= total_weight

    if composite >= 0.65:
        label = "Expansion"
    elif composite >= 0.45:
        label = "Neutral"
    else:
        label = "Contraction"

    # Editorial narrative
    bullish = [k for k, v in indicators.items() if v["score"] >= 0.6]
    bearish = [k for k, v in indicators.items() if v["score"] < 0.4]

    narrative_parts = []
    if label == "Expansion":
        narrative_parts.append(
            f"Leading indicators point to expansion in global trade volumes. "
            f"The composite bellwether score of {composite:.0%} reflects strength across "
            f"{len(bullish)} of {len(indicators)} tracked indicators."
        )
    elif label == "Contraction":
        narrative_parts.append(
            f"Warning signals flash across trade bellwethers as {len(bearish)} of "
            f"{len(indicators)} indicators point to contraction. "
            f"The composite score of {composite:.0%} suggests defensive positioning."
        )
    else:
        narrative_parts.append(
            f"Mixed signals from economic bellwethers paint an uncertain picture. "
            f"The composite score of {composite:.0%} sits in neutral territory with "
            f"{len(bullish)} bullish and {len(bearish)} bearish readings."
        )

    # Add yield curve commentary
    yc_data = indicators.get("yield_curve", {})
    if yc_data.get("raw") is not None:
        if yc_data["raw"] < 0:
            narrative_parts.append(
                f"The yield curve remains inverted at {yc_data['raw']:.2f}%, "
                f"historically a reliable recession predictor that precedes shipping downturns by 12-18 months."
            )
        elif yc_data["raw"] < 0.5:
            narrative_parts.append(
                f"The yield curve spread is flat at {yc_data['raw']:.2f}%, "
                f"signaling uncertainty about growth trajectory."
            )

    return {
        "composite_score": composite,
        "composite_label": label,
        "indicators": indicators,
        "narrative": " ".join(narrative_parts),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def compute_earnings_calendar() -> list[dict]:
    """Generate a shipping company earnings calendar.

    Returns a list of upcoming earnings events with context.
    Since we don't have a live earnings API, this uses known
    quarterly patterns for tracked shipping companies.
    """
    now = datetime.now(timezone.utc)
    quarter = (now.month - 1) // 3 + 1
    year = now.year

    # Typical earnings dates (approximate)
    companies = [
        {"ticker": "ZIM", "name": "ZIM Integrated Shipping",
         "sector": "Container", "market_cap": "$1.8B",
         "q_pattern": {"Q1": "05-20", "Q2": "08-14", "Q3": "11-13", "Q4": "03-12"}},
        {"ticker": "MATX", "name": "Matson Inc",
         "sector": "Container/Jones Act", "market_cap": "$4.2B",
         "q_pattern": {"Q1": "05-01", "Q2": "07-25", "Q3": "11-01", "Q4": "02-20"}},
        {"ticker": "SBLK", "name": "Star Bulk Carriers",
         "sector": "Dry Bulk", "market_cap": "$2.1B",
         "q_pattern": {"Q1": "05-22", "Q2": "08-08", "Q3": "11-20", "Q4": "02-27"}},
        {"ticker": "DAC", "name": "Danaos Corporation",
         "sector": "Container Leasing", "market_cap": "$1.5B",
         "q_pattern": {"Q1": "05-09", "Q2": "08-12", "Q3": "11-04", "Q4": "02-14"}},
        {"ticker": "CMRE", "name": "Costamare Inc",
         "sector": "Container/Bulk", "market_cap": "$1.6B",
         "q_pattern": {"Q1": "04-28", "Q2": "07-22", "Q3": "10-27", "Q4": "01-30"}},
        {"ticker": "GOGL", "name": "Golden Ocean Group",
         "sector": "Dry Bulk", "market_cap": "$2.8B",
         "q_pattern": {"Q1": "05-28", "Q2": "08-25", "Q3": "11-25", "Q4": "02-18"}},
        {"ticker": "DSX", "name": "Diana Shipping",
         "sector": "Dry Bulk", "market_cap": "$0.5B",
         "q_pattern": {"Q1": "05-15", "Q2": "08-05", "Q3": "11-10", "Q4": "02-25"}},
    ]

    calendar = []
    for co in companies:
        # Find next 2 earnings dates
        for q_name, mm_dd in co["q_pattern"].items():
            try:
                month, day = int(mm_dd.split("-")[0]), int(mm_dd.split("-")[1])
                # Try current year and next year
                for y in [year, year + 1]:
                    try:
                        dt = datetime(y, month, day, tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    if dt > now:
                        days_until = (dt - now).days
                        calendar.append({
                            "ticker": co["ticker"],
                            "company": co["name"],
                            "sector": co["sector"],
                            "market_cap": co["market_cap"],
                            "quarter": q_name,
                            "date": dt.strftime("%Y-%m-%d"),
                            "date_display": dt.strftime("%b %-d, %Y"),
                            "days_until": days_until,
                            "status": "Upcoming" if days_until > 7 else "This Week",
                        })
                        break
            except Exception:
                continue

    calendar.sort(key=lambda x: x["days_until"])
    return calendar


def compute_yield_curve_analysis(macro_data: dict) -> dict:
    """Analyze the Treasury yield curve for shipping implications.

    Returns curve points, spread analysis, and historical context.
    """
    tenors = [
        ("DGS1M", "1M"),
        ("DGS3M", "3M"),
        ("DGS6M", "6M"),
        ("DGS1",  "1Y"),
        ("DGS2",  "2Y"),
        ("DGS5",  "5Y"),
        ("DGS10", "10Y"),
        ("DGS30", "30Y"),
    ]

    curve_points = []
    for series_id, label in tenors:
        val = _safe_last(macro_data.get(series_id))
        if val is not None:
            curve_points.append({"tenor": label, "yield": val})

    # Key spreads
    t10 = _safe_last(macro_data.get("DGS10"))
    t2 = _safe_last(macro_data.get("DGS2"))
    t3m = _safe_last(macro_data.get("DGS3M"))
    t10y2y = _safe_last(macro_data.get("T10Y2Y"))

    spreads = {}
    if t10 is not None and t2 is not None:
        spreads["10Y-2Y"] = t10 - t2
    if t10y2y is not None:
        spreads["10Y-2Y (FRED)"] = t10y2y
    if t10 is not None and t3m is not None:
        spreads["10Y-3M"] = t10 - t3m

    # Determine curve shape
    if spreads.get("10Y-2Y", spreads.get("10Y-2Y (FRED)", 0)) < -0.2:
        shape = "Inverted"
        implication = "Historically precedes recession by 12-18 months. Shipping volumes typically decline 2-3 quarters after inversion."
    elif spreads.get("10Y-2Y", spreads.get("10Y-2Y (FRED)", 0)) < 0.3:
        shape = "Flat"
        implication = "Market uncertainty about growth trajectory. Shipping companies should prepare for potential slowdown."
    elif spreads.get("10Y-2Y", spreads.get("10Y-2Y (FRED)", 0)) < 1.5:
        shape = "Normal"
        implication = "Healthy term structure supports moderate growth expectations and stable trade volumes."
    else:
        shape = "Steep"
        implication = "Strong growth expectations. Rising long-term rates suggest increasing economic activity and trade demand."

    return {
        "curve_points": curve_points,
        "spreads": spreads,
        "shape": shape,
        "implication": implication,
        "t10": t10,
        "t2": t2,
    }
