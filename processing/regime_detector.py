from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class MacroRegime:
    """Macro regime classification with shipping-specific context."""

    regime: str                        # "EXPANSION" | "SLOWDOWN" | "CONTRACTION" | "RECOVERY"
    confidence: float                  # [0, 1]
    bdi_trend: str                     # "rising" | "falling" | "flat"
    pmi_level: float                   # current PMI or 50.0 if unavailable
    pmi_direction: str                 # "improving" | "deteriorating" | "flat"
    fuel_environment: str              # "high" | "moderate" | "low"
    shipping_regime_label: str         # e.g. "Bull Freight Market"
    regime_color: str                  # hex color string
    best_routes_in_regime: list[str]   # route IDs that historically outperform
    best_stocks_in_regime: list[str]   # ticker symbols
    regime_description: str            # 2-3 sentences on regime and shipping implications
    days_in_regime: int = 30           # placeholder estimate based on signal changes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bdi_trend(macro_data: dict[str, pd.DataFrame]) -> tuple[str, float, float]:
    """Return (trend, avg_30d, avg_90d) using the BSXRLM series."""
    df = macro_data.get("BSXRLM")
    if df is None or df.empty:
        return "flat", 0.0, 0.0

    vals = df["value"].dropna()
    if len(vals) < 2:
        return "flat", float(vals.iloc[-1]) if len(vals) else 0.0, 0.0

    avg_30 = float(vals.tail(30).mean())
    avg_90 = float(vals.tail(90).mean())

    if avg_90 == 0:
        return "flat", avg_30, avg_90

    ratio = avg_30 / avg_90
    if ratio > 1.03:
        trend = "rising"
    elif ratio < 0.97:
        trend = "falling"
    else:
        trend = "flat"

    return trend, avg_30, avg_90


def _pmi_proxy(macro_data: dict[str, pd.DataFrame]) -> tuple[float, str]:
    """Return (pmi_level_equivalent, direction) using MANEMP as a proxy.

    MANEMP is manufacturing employment — used as a PMI-like cyclical indicator.
    We map its relationship to its 90-day average onto a 45-55 PMI-equivalent scale.
    """
    # Try MANEMP first, then fall back to IPMAN
    for series_id in ("MANEMP", "IPMAN"):
        df = macro_data.get(series_id)
        if df is not None and not df.empty:
            vals = df["value"].dropna()
            if len(vals) >= 2:
                current = float(vals.iloc[-1])
                avg_90 = float(vals.tail(90).mean())
                avg_30 = float(vals.tail(30).mean())

                if avg_90 == 0:
                    return 50.0, "flat"

                # Map ratio to PMI-equivalent: 0.95 ratio → ~47.5, 1.05 → ~52.5
                ratio = current / avg_90
                pmi_equiv = 50.0 + (ratio - 1.0) * 50.0
                pmi_equiv = max(40.0, min(60.0, pmi_equiv))

                # Direction: compare recent 30d avg vs 90d avg
                direction_ratio = avg_30 / avg_90
                if direction_ratio > 1.01:
                    direction = "improving"
                elif direction_ratio < 0.99:
                    direction = "deteriorating"
                else:
                    direction = "flat"

                return pmi_equiv, direction

    return 50.0, "flat"


def _fuel_environment(macro_data: dict[str, pd.DataFrame]) -> str:
    """Classify fuel cost environment using WPU101 (producer price index for fuel)."""
    df = macro_data.get("WPU101")
    if df is None or df.empty:
        # Fall back to WTI crude
        df = macro_data.get("DCOILWTICO")

    if df is None or df.empty:
        return "moderate"

    vals = df["value"].dropna()
    if len(vals) < 2:
        return "moderate"

    current = float(vals.iloc[-1])
    avg_90 = float(vals.tail(90).mean())

    if avg_90 == 0:
        return "moderate"

    ratio = current / avg_90
    if ratio > 1.08:
        return "high"
    elif ratio < 0.92:
        return "low"
    else:
        return "moderate"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_macro_regime(macro_data: dict[str, pd.DataFrame]) -> MacroRegime:
    """Classify the current macro regime and return a MacroRegime dataclass.

    Classification matrix
    ---------------------
    EXPANSION  : PMI > 52 AND BDI rising AND fuel moderate
    SLOWDOWN   : PMI 48-52 OR BDI flat
    CONTRACTION: PMI < 48 AND BDI falling
    RECOVERY   : PMI rising from below 50 AND BDI bottoming (flat after falling)

    Missing data defaults to SLOWDOWN at 0.3 confidence.
    """
    try:
        bdi_trend, bdi_30, bdi_90 = _bdi_trend(macro_data)
        pmi_level, pmi_direction = _pmi_proxy(macro_data)
        fuel_env = _fuel_environment(macro_data)
    except Exception:
        return MacroRegime(
            regime="SLOWDOWN",
            confidence=0.3,
            bdi_trend="flat",
            pmi_level=50.0,
            pmi_direction="flat",
            fuel_environment="moderate",
            shipping_regime_label="Moderating Growth",
            regime_color="#3b82f6",
            best_routes_in_regime=["intra_asia_china_sea"],
            best_stocks_in_regime=["CMRE", "SBLK"],
            regime_description=(
                "Data unavailable to determine regime — defaulting to Slowdown. "
                "Mixed signals suggest neither strong bullish nor bearish freight conditions. "
                "Selective route exposure is recommended."
            ),
        )

    have_data = (bdi_30 > 0 or pmi_level != 50.0)

    # --- EXPANSION ---
    if pmi_level > 52 and bdi_trend == "rising" and fuel_env == "moderate":
        confidence = _score_confidence(
            [pmi_level > 54, bdi_trend == "rising", fuel_env == "moderate", pmi_direction == "improving"],
            base=0.70,
        )
        return MacroRegime(
            regime="EXPANSION",
            confidence=confidence if have_data else 0.3,
            bdi_trend=bdi_trend,
            pmi_level=pmi_level,
            pmi_direction=pmi_direction,
            fuel_environment=fuel_env,
            shipping_regime_label="Bull Freight Market",
            regime_color="#10b981",
            best_routes_in_regime=["transpacific_eb", "asia_europe"],
            best_stocks_in_regime=["ZIM", "MATX", "DAC"],
            regime_description=(
                "Global manufacturing is expanding with rising freight demand and constructive BDI momentum. "
                "Transpacific and Asia-Europe lanes benefit most as container volumes climb. "
                "This is the historically strongest period for shipping equities and spot rates."
            ),
        )

    # --- CONTRACTION ---
    if pmi_level < 48 and bdi_trend == "falling":
        confidence = _score_confidence(
            [pmi_level < 46, bdi_trend == "falling", pmi_direction == "deteriorating", fuel_env == "high"],
            base=0.65,
        )
        return MacroRegime(
            regime="CONTRACTION",
            confidence=confidence if have_data else 0.3,
            bdi_trend=bdi_trend,
            pmi_level=pmi_level,
            pmi_direction=pmi_direction,
            fuel_environment=fuel_env,
            shipping_regime_label="Bear Freight Cycle",
            regime_color="#ef4444",
            best_routes_in_regime=[],
            best_stocks_in_regime=[],
            regime_description=(
                "Manufacturing activity is contracting and freight indices are declining, signalling reduced cargo volumes. "
                "Vessel oversupply relative to demand typically compresses spot rates across all major lanes. "
                "Shipping equities tend to underperform; capital preservation is preferred over new route exposure."
            ),
        )

    # --- RECOVERY ---
    # PMI rising from below 50 (improving direction) AND BDI bottoming (flat after a period of falling)
    if pmi_level < 52 and pmi_direction == "improving" and bdi_trend == "flat":
        confidence = _score_confidence(
            [pmi_level > 48, pmi_direction == "improving", bdi_trend == "flat", fuel_env != "high"],
            base=0.55,
        )
        return MacroRegime(
            regime="RECOVERY",
            confidence=confidence if have_data else 0.3,
            bdi_trend=bdi_trend,
            pmi_level=pmi_level,
            pmi_direction=pmi_direction,
            fuel_environment=fuel_env,
            shipping_regime_label="Early Recovery",
            regime_color="#f59e0b",
            best_routes_in_regime=["transpacific_eb", "asia_europe"],
            best_stocks_in_regime=["ZIM", "MATX"],
            regime_description=(
                "Leading indicators suggest a cyclical trough has passed with manufacturing sentiment turning higher. "
                "Freight rates have stabilised and early restocking demand is emerging on key transpacific and Asia-Europe lanes. "
                "Early-cycle shipping names historically deliver the strongest returns at this inflection point."
            ),
        )

    # --- SLOWDOWN (default / catch-all) ---
    confidence = _score_confidence(
        [48 <= pmi_level <= 52, bdi_trend == "flat"],
        base=0.50,
    )
    return MacroRegime(
        regime="SLOWDOWN",
        confidence=confidence if have_data else 0.3,
        bdi_trend=bdi_trend,
        pmi_level=pmi_level,
        pmi_direction=pmi_direction,
        fuel_environment=fuel_env,
        shipping_regime_label="Moderating Growth",
        regime_color="#3b82f6",
        best_routes_in_regime=["intra_asia_china_sea"],
        best_stocks_in_regime=["CMRE", "SBLK"],
        regime_description=(
            "Global growth is moderating but not contracting, with PMI near the expansion-contraction boundary. "
            "Intra-Asia trade lanes hold up better than long-haul routes as regional demand stays relatively resilient. "
            "Selective positioning in lower-beta shipping names is favoured over broad exposure."
        ),
    )


def _score_confidence(conditions: list[bool], base: float) -> float:
    """Boost base confidence by 0.05 per satisfied condition, capped at 0.95."""
    boost = sum(0.05 for c in conditions if c)
    return min(0.95, base + boost)


# ---------------------------------------------------------------------------
# Regime multipliers
# ---------------------------------------------------------------------------

# Base multipliers per regime for every known route
_REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    "EXPANSION": {
        "transpacific_eb": 1.25,
        "transpacific_wb": 1.20,
        "asia_europe": 1.22,
        "transatlantic": 1.18,
        "intra_asia_china_sea": 1.15,
    },
    "SLOWDOWN": {
        "transpacific_eb": 1.00,
        "transpacific_wb": 0.98,
        "asia_europe": 1.00,
        "transatlantic": 0.97,
        "intra_asia_china_sea": 1.05,
    },
    "CONTRACTION": {
        "transpacific_eb": 0.80,
        "transpacific_wb": 0.82,
        "asia_europe": 0.78,
        "transatlantic": 0.85,
        "intra_asia_china_sea": 0.88,
    },
    "RECOVERY": {
        "transpacific_eb": 1.18,
        "transpacific_wb": 1.12,
        "asia_europe": 1.20,
        "transatlantic": 1.05,
        "intra_asia_china_sea": 1.08,
    },
}

_DEFAULT_MULTIPLIER: dict[str, float] = {
    "EXPANSION": 1.15,
    "SLOWDOWN": 1.00,
    "CONTRACTION": 0.82,
    "RECOVERY": 1.08,
}


def get_regime_multipliers(regime: MacroRegime) -> dict[str, float]:
    """Return a mapping of route_id -> score_multiplier for the given regime.

    Routes not explicitly listed receive the regime's default multiplier.
    Confidence scales the deviation from 1.0 so that low-confidence regimes
    produce more muted adjustments.
    """
    base_map = _REGIME_MULTIPLIERS.get(regime.regime, {})
    default = _DEFAULT_MULTIPLIER.get(regime.regime, 1.00)

    # Scale deviation from 1.0 by confidence so uncertain regimes are muted
    def _scaled(raw: float) -> float:
        deviation = (raw - 1.0) * regime.confidence
        return 1.0 + deviation

    result: dict[str, float] = {}
    # Return known routes with confidence-scaled multipliers
    all_routes = set(base_map) | {r for rm in _REGIME_MULTIPLIERS.values() for r in rm}
    for route_id in all_routes:
        raw = base_map.get(route_id, default)
        result[route_id] = _scaled(raw)

    # Always include a fallback key so callers can look up unknown routes
    result["_default"] = _scaled(default)

    return result
