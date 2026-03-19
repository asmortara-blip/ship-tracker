from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from ports.port_registry import PORTS, PORTS_BY_LOCODE, Port
from ports.product_mapper import get_category, get_color
from utils.helpers import safe_normalize, sigmoid, score_to_label, trend_label, now_iso


@dataclass
class PortDemandResult:
    locode: str
    port_name: str
    region: str
    country_iso3: str

    demand_score: float          # [0, 1] composite
    demand_label: str            # "High" | "Moderate" | "Low"
    demand_trend: str            # "Rising" | "Stable" | "Falling"

    import_value_usd: float      # Latest period import value USD
    export_value_usd: float      # Latest period export value USD
    top_products: list[dict]     # [{"category": str, "value_usd": float, "color": str}]

    congestion_index: float      # [0, 1] from AIS data
    vessel_count: int            # Raw vessel count from AIS
    throughput_teu_m: float      # TEU millions from World Bank

    trade_flow_component: float  # Sub-score [0, 1]
    congestion_component: float  # Sub-score [0, 1]
    throughput_component: float  # Sub-score [0, 1]

    data_freshness: str          # ISO timestamp of analysis
    has_real_data: bool          # False if all sources returned empty


def analyze_all_ports(
    trade_data: dict[str, pd.DataFrame],
    ais_data: dict[str, pd.DataFrame],
    wb_data: dict[str, pd.DataFrame],
    weights: dict | None = None,
) -> list[PortDemandResult]:
    """Score demand for all tracked ports.

    Args:
        trade_data: dict port_locode → DataFrame from comtrade_feed
        ais_data:   dict port_locode → DataFrame from ais_feed
        wb_data:    dict indicator_id → DataFrame from worldbank_feed
        weights:    optional override for scoring weights

    Returns:
        List of PortDemandResult, sorted by demand_score descending.
    """
    w = weights or {"trade_flow": 0.40, "congestion": 0.35, "throughput": 0.25}

    # First pass: compute raw values for all ports (needed for normalization)
    raw_values: list[dict] = []
    for port in PORTS:
        raw = _compute_raw(port, trade_data, ais_data, wb_data)
        raw_values.append(raw)

    # Normalize trade flow values across all ports so scores are relative
    all_import = [r["import_value_usd"] for r in raw_values]
    all_export = [r["export_value_usd"] for r in raw_values]
    global_import_min = min(all_import) if all_import else 0
    global_import_max = max(all_import) if all_import else 1
    global_export_min = min(all_export) if all_export else 0
    global_export_max = max(all_export) if all_export else 1

    results: list[PortDemandResult] = []
    for port, raw in zip(PORTS, raw_values):
        result = _build_result(port, raw, w,
                               global_import_min, global_import_max,
                               global_export_min, global_export_max)
        results.append(result)
        logger.debug(f"{port.locode}: demand_score={result.demand_score:.3f} ({result.demand_label})")

    results.sort(key=lambda r: r.demand_score, reverse=True)
    logger.info(f"Port demand analysis complete: {len(results)} ports scored")
    return results


def _compute_raw(
    port: Port,
    trade_data: dict,
    ais_data: dict,
    wb_data: dict,
) -> dict:
    """Compute raw (un-normalized) values for a single port."""
    raw: dict = {
        "locode": port.locode,
        "import_value_usd": 0.0,
        "export_value_usd": 0.0,
        "top_products": [],
        "congestion_index": 0.5,
        "vessel_count": 0,
        "throughput_teu_m": 0.0,
        "import_trend": 0.0,
        "export_trend": 0.0,
        "has_real_data": False,
    }

    # --- Trade flow ---
    trade_df = trade_data.get(port.locode)
    if trade_df is not None and not trade_df.empty:
        raw["has_real_data"] = True

        imports = trade_df[trade_df["flow"] == "M"]
        exports = trade_df[trade_df["flow"] == "X"]

        raw["import_value_usd"] = float(imports["value_usd"].sum()) if not imports.empty else 0.0
        raw["export_value_usd"] = float(exports["value_usd"].sum()) if not exports.empty else 0.0

        # Top products by import value
        if not imports.empty:
            by_hs = (
                imports.groupby("hs_code")["value_usd"]
                .sum()
                .sort_values(ascending=False)
                .head(4)
            )
            raw["top_products"] = [
                {
                    "hs_code": hs,
                    "category": get_category(hs),
                    "value_usd": val,
                    "color": get_color(get_category(hs)),
                }
                for hs, val in by_hs.items()
            ]

        # Trend: slope of import values over time (positive = rising)
        if len(imports) >= 2:
            monthly = imports.groupby("date")["value_usd"].sum().sort_index()
            if len(monthly) >= 2:
                x = list(range(len(monthly)))
                y = monthly.values.tolist()
                n = len(x)
                slope = (n * sum(xi * yi for xi, yi in zip(x, y)) - sum(x) * sum(y)) / \
                        max(n * sum(xi**2 for xi in x) - sum(x)**2, 1)
                raw["import_trend"] = slope / max(abs(raw["import_value_usd"]) / n, 1)

    # --- AIS congestion ---
    from data.ais_feed import compute_congestion_index, get_vessel_count
    raw["congestion_index"] = compute_congestion_index(port.locode, ais_data)
    raw["vessel_count"] = get_vessel_count(port.locode, ais_data, "cargo")

    # --- World Bank throughput ---
    from data.worldbank_feed import get_teu_for_country
    raw["throughput_teu_m"] = get_teu_for_country(port.country_iso3, wb_data, port.locode)

    return raw


def _build_result(
    port: Port,
    raw: dict,
    weights: dict,
    import_min: float,
    import_max: float,
    export_min: float,
    export_max: float,
) -> PortDemandResult:
    """Build a PortDemandResult from raw values and normalization bounds."""

    # --- Trade flow component ---
    if import_max > import_min:
        import_norm = (raw["import_value_usd"] - import_min) / (import_max - import_min)
    else:
        import_norm = 0.5

    if export_max > export_min:
        export_norm = (raw["export_value_usd"] - export_min) / (export_max - export_min)
    else:
        export_norm = 0.5

    trade_flow_component = 0.6 * import_norm + 0.4 * export_norm
    # Apply small trend bonus/penalty
    trend_adjustment = max(-0.08, min(0.08, raw["import_trend"]))
    trade_flow_component = max(0.0, min(1.0, trade_flow_component + trend_adjustment))

    # --- Congestion component (already [0,1]) ---
    congestion_component = raw["congestion_index"]

    # --- Throughput component ---
    # Normalize TEU within known global range (largest ports ~50M TEU/yr)
    MAX_TEU_M = 50.0
    throughput_component = min(1.0, raw["throughput_teu_m"] / MAX_TEU_M) if raw["throughput_teu_m"] > 0 else 0.5

    # --- Composite score ---
    demand_score = (
        weights["trade_flow"] * trade_flow_component
        + weights["congestion"] * congestion_component
        + weights["throughput"] * throughput_component
    )
    demand_score = max(0.0, min(1.0, demand_score))

    # --- Trend label ---
    d_trend = "Rising" if raw["import_trend"] > 0.02 else ("Falling" if raw["import_trend"] < -0.02 else "Stable")

    return PortDemandResult(
        locode=port.locode,
        port_name=port.name,
        region=port.region,
        country_iso3=port.country_iso3,
        demand_score=demand_score,
        demand_label=score_to_label(demand_score),
        demand_trend=d_trend,
        import_value_usd=raw["import_value_usd"],
        export_value_usd=raw["export_value_usd"],
        top_products=raw["top_products"],
        congestion_index=raw["congestion_index"],
        vessel_count=raw["vessel_count"],
        throughput_teu_m=raw["throughput_teu_m"],
        trade_flow_component=trade_flow_component,
        congestion_component=congestion_component,
        throughput_component=throughput_component,
        data_freshness=now_iso(),
        has_real_data=raw["has_real_data"],
    )


def get_port_result(locode: str, results: list[PortDemandResult]) -> PortDemandResult | None:
    for r in results:
        if r.locode == locode:
            return r
    return None
