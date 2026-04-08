"""index_tracker.py — Comprehensive shipping index tracking with historical context.

Tracks BDI, SCFI, WCI, CCFI and other freight indices with:
  1. 52-week range and current position
  2. Historical percentile ranking
  3. Moving averages (7d, 30d, 90d)
  4. Rate of change momentum
  5. Editorial context generation
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger


# ── Index definitions ────────────────────────────────────────────────────────
INDEX_DEFINITIONS = {
    "BDI": {
        "name": "Baltic Dry Index",
        "source": "Baltic Exchange",
        "description": "Composite index of dry bulk shipping costs for Capesize, Panamax, and Supramax vessels",
        "unit": "points",
        "significance": "Leading indicator for global commodity trade and industrial activity",
    },
    "SCFI": {
        "name": "Shanghai Containerized Freight Index",
        "source": "Shanghai Shipping Exchange",
        "description": "Spot rate index for container shipping from Shanghai to global destinations",
        "unit": "$/TEU",
        "significance": "Benchmark for Asia-origin container freight pricing",
    },
    "WCI": {
        "name": "World Container Index",
        "source": "Freightos Baltic",
        "description": "Weekly composite of 8 major trade routes covering 40-ft container spot rates",
        "unit": "$/FEU",
        "significance": "Broad measure of global container shipping costs",
    },
    "CCFI": {
        "name": "China Containerized Freight Index",
        "source": "Shanghai Shipping Exchange",
        "description": "Comprehensive index of container freight rates from Chinese ports",
        "unit": "points",
        "significance": "Key indicator for China export pricing and trade volume",
    },
}


def _safe_series(data: dict, key: str) -> pd.Series | None:
    val = data.get(key)
    if val is None:
        return None
    if isinstance(val, pd.DataFrame):
        if val.empty:
            return None
        return val.iloc[:, 0].dropna()
    if isinstance(val, pd.Series):
        return val.dropna()
    return None


def compute_index_dashboard(freight_data: dict, macro_data: dict = None) -> list[dict]:
    """Compute comprehensive dashboard data for all tracked shipping indices.

    Returns list of index dicts with historical context, moving averages,
    and editorial commentary.
    """
    results = []

    # Try to match freight data keys to index definitions
    key_mapping = {}
    for idx_key in INDEX_DEFINITIONS:
        # Try exact match first
        if idx_key in freight_data:
            key_mapping[idx_key] = idx_key
            continue
        # Try case-insensitive match
        for fk in freight_data:
            if str(fk).upper() == idx_key:
                key_mapping[idx_key] = fk
                break

    # Also check for BDI in macro data (FRED: BDIY)
    if "BDI" not in key_mapping and macro_data:
        if "BDIY" in macro_data:
            key_mapping["BDI"] = "BDIY"

    for idx_key, defn in INDEX_DEFINITIONS.items():
        data_key = key_mapping.get(idx_key)
        source = freight_data if data_key and data_key in freight_data else (macro_data or {})
        series = _safe_series(source, data_key) if data_key else None

        index_data = {
            "key": idx_key,
            "name": defn["name"],
            "source": defn["source"],
            "description": defn["description"],
            "unit": defn["unit"],
            "significance": defn["significance"],
            "has_data": series is not None and len(series) > 0,
        }

        if series is None or len(series) < 2:
            # Provide mock/fallback data for display
            index_data.update({
                "current": None,
                "prev": None,
                "change_1d": None,
                "change_7d": None,
                "change_30d": None,
                "high_52w": None,
                "low_52w": None,
                "range_position": None,
                "percentile": None,
                "ma_7": None,
                "ma_30": None,
                "ma_90": None,
                "above_ma_30": None,
                "momentum": "N/A",
                "commentary": f"Data for {defn['name']} is currently unavailable.",
            })
            results.append(index_data)
            continue

        try:
            current = float(series.iloc[-1])
            prev = float(series.iloc[-2]) if len(series) >= 2 else current

            # Changes
            chg_1d = (current - prev) / abs(prev) * 100 if prev != 0 else 0
            chg_7d = None
            chg_30d = None
            if len(series) >= 8:
                p7 = float(series.iloc[-8])
                chg_7d = (current - p7) / abs(p7) * 100 if p7 != 0 else 0
            if len(series) >= 31:
                p30 = float(series.iloc[-31])
                chg_30d = (current - p30) / abs(p30) * 100 if p30 != 0 else 0

            # 52-week range (approximate using available data)
            recent = series.iloc[-min(252, len(series)):]
            high_52w = float(recent.max())
            low_52w = float(recent.min())
            range_pct = (current - low_52w) / (high_52w - low_52w) * 100 if high_52w != low_52w else 50

            # Percentile
            percentile = float((series < current).sum() / len(series) * 100)

            # Moving averages
            ma_7 = float(series.iloc[-min(7, len(series)):].mean())
            ma_30 = float(series.iloc[-min(30, len(series)):].mean())
            ma_90 = float(series.iloc[-min(90, len(series)):].mean())

            above_ma_30 = current > ma_30

            # Momentum classification
            if chg_30d is not None:
                if chg_30d > 10:
                    momentum = "Strong Rally"
                elif chg_30d > 3:
                    momentum = "Uptrend"
                elif chg_30d > -3:
                    momentum = "Consolidating"
                elif chg_30d > -10:
                    momentum = "Downtrend"
                else:
                    momentum = "Sharp Decline"
            else:
                momentum = "N/A"

            # Generate editorial commentary
            commentary_parts = []

            if percentile > 80:
                commentary_parts.append(
                    f"The {defn['name']} stands at {current:,.0f}, trading in the {percentile:.0f}th "
                    f"percentile of its historical range, suggesting rates are elevated relative to norms."
                )
            elif percentile > 60:
                commentary_parts.append(
                    f"At {current:,.0f}, the {defn['name']} sits above its historical median "
                    f"in the {percentile:.0f}th percentile, reflecting moderately firm conditions."
                )
            elif percentile > 40:
                commentary_parts.append(
                    f"The {defn['name']} at {current:,.0f} trades near its historical midpoint "
                    f"({percentile:.0f}th percentile), with no strong directional bias."
                )
            else:
                commentary_parts.append(
                    f"At {current:,.0f}, the {defn['name']} is in the {percentile:.0f}th percentile "
                    f"of its range, indicating rates are depressed relative to historical norms."
                )

            if above_ma_30:
                commentary_parts.append(
                    f"The index trades above its 30-day moving average of {ma_30:,.0f}, "
                    f"a constructive technical signal."
                )
            else:
                commentary_parts.append(
                    f"The index remains below its 30-day moving average of {ma_30:,.0f}, "
                    f"signaling ongoing downward pressure."
                )

            index_data.update({
                "current": current,
                "prev": prev,
                "change_1d": chg_1d,
                "change_7d": chg_7d,
                "change_30d": chg_30d,
                "high_52w": high_52w,
                "low_52w": low_52w,
                "range_position": range_pct,
                "percentile": percentile,
                "ma_7": ma_7,
                "ma_30": ma_30,
                "ma_90": ma_90,
                "above_ma_30": above_ma_30,
                "momentum": momentum,
                "commentary": " ".join(commentary_parts),
                "n_observations": len(series),
            })
        except Exception as exc:
            logger.warning(f"Index computation failed for {idx_key}: {exc}")
            index_data["commentary"] = f"Error computing {defn['name']} analytics."

        results.append(index_data)

    return results
