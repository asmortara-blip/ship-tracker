"""cargo_analyzer.py — Cargo type and product category deep-dive analysis.

Aggregates trade data by HS category, computes flow statistics, demand signals,
and seasonal patterns for the Cargo tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict

# ---------------------------------------------------------------------------
# HS category metadata (mirrors config.yaml hs_categories)
# ---------------------------------------------------------------------------

HS_CATEGORIES: dict[str, dict] = {
    "electronics": {
        "label": "Electronics",
        "codes": ["8471", "8517", "8542", "8541", "8536", "8544"],
    },
    "machinery": {
        "label": "Machinery",
        "codes": ["8413", "8479", "8431", "8483", "8501", "8503"],
    },
    "automotive": {
        "label": "Automotive",
        "codes": ["8703", "8708", "8716", "8701", "8702"],
    },
    "apparel": {
        "label": "Apparel",
        "codes": ["6109", "6110", "6204", "6203", "6101", "6102"],
    },
    "chemicals": {
        "label": "Chemicals",
        "codes": ["2902", "2903", "2905", "3901", "3902", "2907"],
    },
    "agriculture": {
        "label": "Agriculture",
        "codes": ["1001", "1201", "0901", "0902", "1005", "1507"],
    },
    "metals": {
        "label": "Metals & Steel",
        "codes": ["7208", "7209", "7210", "7213", "7214", "7225"],
    },
}

# ---------------------------------------------------------------------------
# Shipping characteristics per category
# ---------------------------------------------------------------------------

CARGO_CHARACTERISTICS: dict[str, dict] = {
    "electronics": {
        "shipping": "standard container",
        "seasonal_peak": 9,
        "yoy_growth": 6.2,
        "sensitivity": "high",
    },
    "machinery": {
        "shipping": "heavy-lift/OOG sometimes",
        "seasonal_peak": 3,
        "yoy_growth": 3.8,
        "sensitivity": "moderate",
    },
    "automotive": {
        "shipping": "RoRo or container",
        "seasonal_peak": 4,
        "yoy_growth": 2.1,
        "sensitivity": "high",
    },
    "apparel": {
        "shipping": "standard container",
        "seasonal_peak": 7,
        "yoy_growth": 1.5,
        "sensitivity": "moderate",
    },
    "chemicals": {
        "shipping": "ISO tank/specialized",
        "seasonal_peak": 2,
        "yoy_growth": 4.3,
        "sensitivity": "low",
    },
    "agriculture": {
        "shipping": "refrigerated/bulk",
        "seasonal_peak": 10,
        "yoy_growth": 5.7,
        "sensitivity": "moderate",
    },
    "metals": {
        "shipping": "bulk/break-bulk",
        "seasonal_peak": 5,
        "yoy_growth": -1.2,
        "sensitivity": "high",
    },
}

# Route-to-region mapping for dominant cargo inference
_ROUTE_REGIONS: dict[str, tuple[str, str]] = {
    "transpacific_eb":      ("Asia East",       "North America West"),
    "asia_europe":          ("Asia East",       "Europe"),
    "transpacific_wb":      ("North America West", "Asia East"),
    "transatlantic":        ("Europe",          "North America East"),
    "sea_transpacific_eb":  ("Southeast Asia",  "North America West"),
    "ningbo_europe":        ("Asia East",       "Europe"),
    "middle_east_to_europe":("Middle East",     "Europe"),
    "middle_east_to_asia":  ("Middle East",     "Asia East"),
    "south_asia_to_europe": ("South Asia",      "Europe"),
    "intra_asia_china_sea": ("Asia East",       "Southeast Asia"),
    "intra_asia_china_japan":("Asia East",      "Asia East"),
    "china_south_america":  ("Asia East",       "South America"),
    "europe_south_america": ("Europe",          "South America"),
    "med_hub_to_asia":      ("Europe",          "Asia East"),
    "north_africa_to_europe":("Africa",         "Europe"),
    "us_east_south_america":("North America East", "South America"),
    "longbeach_to_asia":    ("North America West", "Asia East"),
}

# Categories that dominate each origin-region pair (for route cargo mapping)
_REGION_DOMINANT_CARGO: dict[str, list[str]] = {
    "Asia East":          ["electronics", "machinery", "apparel", "metals"],
    "North America West": ["agriculture", "chemicals", "machinery"],
    "North America East": ["machinery",   "chemicals", "agriculture"],
    "Europe":             ["machinery",   "chemicals", "automotive"],
    "Southeast Asia":     ["electronics", "apparel",   "agriculture"],
    "Middle East":        ["chemicals",   "metals"],
    "South Asia":         ["apparel",     "agriculture"],
    "Africa":             ["agriculture", "metals"],
    "South America":      ["agriculture", "metals",    "chemicals"],
}


# ---------------------------------------------------------------------------
# Demand signal helpers
# ---------------------------------------------------------------------------

def _demand_signal(yoy_growth: float) -> tuple[str, str]:
    """Return (signal_label, signal_color) based on YoY growth."""
    if yoy_growth > 5.0:
        return "SURGING", "#10b981"
    if yoy_growth > 2.0:
        return "GROWING", "#3b82f6"
    if yoy_growth > -1.0:
        return "STABLE", "#64748b"
    return "DECLINING", "#ef4444"


def _key_insight(category: str, yoy_growth: float, signal: str) -> str:
    """Generate a 1-2 sentence insight string for a cargo category."""
    chars = CARGO_CHARACTERISTICS.get(category, {})
    sensitivity = chars.get("sensitivity", "moderate")
    shipping = chars.get("shipping", "standard container")
    peak_month = chars.get("seasonal_peak", 6)
    import calendar
    peak_name = calendar.month_abbr[peak_month]
    label = HS_CATEGORIES.get(category, {}).get("label", category.title())

    if signal == "SURGING":
        return (
            f"{label} flows are expanding rapidly ({yoy_growth:+.1f}% YoY), "
            f"driven by strong end-market demand. "
            f"Capacity on {shipping} vessels tightens heading into {peak_name} seasonal peak."
        )
    if signal == "GROWING":
        return (
            f"{label} trade is growing steadily at {yoy_growth:+.1f}% YoY. "
            f"Demand sensitivity is {sensitivity}; "
            f"watch for rate pressure around the {peak_name} seasonal peak."
        )
    if signal == "STABLE":
        return (
            f"{label} volumes are broadly stable ({yoy_growth:+.1f}% YoY). "
            f"Shipped predominantly as {shipping}, with limited near-term disruption risk."
        )
    return (
        f"{label} shipments are contracting ({yoy_growth:+.1f}% YoY). "
        f"Excess capacity likely on {shipping} routes; monitor for rate softening."
    )


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class CargoFlowAnalysis:
    hs_category: str               # "electronics", "machinery", etc.
    category_label: str            # "Electronics", "Machinery", etc.
    hs_codes: list[str]
    total_value_usd: float         # sum across all tracked ports
    top_origin_ports: list[tuple[str, float]]   # (locode, value) top 3
    top_dest_ports: list[tuple[str, float]]     # (locode, value) top 3
    top_routes: list[str]          # route_ids where this cargo dominates
    yoy_growth_pct: float          # from CARGO_CHARACTERISTICS
    seasonality_peak_month: int    # 1-12
    shipping_characteristics: str  # e.g. "standard container"
    demand_signal: str             # "SURGING" | "GROWING" | "STABLE" | "DECLINING"
    signal_color: str
    key_insight: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_cargo_flows(
    trade_data: dict,
    wb_data: dict | None = None,
) -> list[CargoFlowAnalysis]:
    """Return one CargoFlowAnalysis per HS category defined in config.

    Parameters
    ----------
    trade_data:
        Mapping of port_locode -> DataFrame with columns including at minimum:
        ``value_usd``, ``flow`` (Import/Export), ``reporter_iso3``,
        ``partner_iso3``, ``hs_category``.
        May be empty or contain None values if data has not yet been fetched.
    wb_data:
        Optional World Bank supplemental data (unused in current version;
        reserved for future macro enrichment).
    """
    results: list[CargoFlowAnalysis] = []

    for cat_key, cat_meta in HS_CATEGORIES.items():
        chars = CARGO_CHARACTERISTICS.get(cat_key, {})
        yoy_growth = chars.get("yoy_growth", 0.0)
        peak_month = chars.get("seasonal_peak", 6)
        shipping = chars.get("shipping", "standard container")

        # ── Aggregate trade values from real data ──────────────────────────
        origin_totals: dict[str, float] = defaultdict(float)
        dest_totals: dict[str, float] = defaultdict(float)
        total_value = 0.0

        has_real_data = False

        for locode, df in (trade_data or {}).items():
            if df is None or not hasattr(df, "empty") or df.empty:
                continue
            if "hs_category" not in df.columns or "value_usd" not in df.columns:
                continue

            cat_rows = df[df["hs_category"] == cat_key]
            if cat_rows.empty:
                continue

            has_real_data = True

            exports = cat_rows[cat_rows.get("flow", cat_rows.columns[0]) == "Export"] if "flow" in cat_rows.columns else cat_rows
            imports = cat_rows[cat_rows.get("flow", cat_rows.columns[0]) == "Import"] if "flow" in cat_rows.columns else cat_rows

            port_export_val = float(exports["value_usd"].sum()) if not exports.empty else 0.0
            port_import_val = float(imports["value_usd"].sum()) if not imports.empty else 0.0
            port_total = float(cat_rows["value_usd"].sum())

            total_value += port_total
            if port_export_val > 0:
                origin_totals[locode] += port_export_val
            if port_import_val > 0:
                dest_totals[locode] += port_import_val

        # ── Fallback benchmarks when no real data is available ─────────────
        if not has_real_data:
            benchmarks: dict[str, float] = {
                "electronics":  4_800_000_000,
                "machinery":    3_200_000_000,
                "automotive":   2_900_000_000,
                "apparel":      1_600_000_000,
                "chemicals":    2_100_000_000,
                "agriculture":  2_400_000_000,
                "metals":       1_800_000_000,
            }
            total_value = benchmarks.get(cat_key, 1_500_000_000)

            # Illustrative top origins/destinations based on known trade patterns
            illustrative: dict[str, tuple[list[tuple[str, float]], list[tuple[str, float]]]] = {
                "electronics":  (
                    [("CNSHA", 0.32), ("CNSZN", 0.24), ("TWKHH", 0.18)],
                    [("USLAX", 0.28), ("NLRTM", 0.22), ("USNYC", 0.16)],
                ),
                "machinery":    (
                    [("DEHAM", 0.28), ("CNSHA", 0.22), ("JPYOK", 0.18)],
                    [("USLAX", 0.24), ("SGSIN", 0.18), ("BRSAO", 0.14)],
                ),
                "automotive":   (
                    [("JPYOK", 0.30), ("KRPUS", 0.24), ("DEHAM", 0.20)],
                    [("USLAX", 0.26), ("USNYC", 0.20), ("BRSAO", 0.14)],
                ),
                "apparel":      (
                    [("CNSHA", 0.28), ("LKCMB", 0.20), ("CNSZN", 0.18)],
                    [("USNYC", 0.26), ("NLRTM", 0.22), ("USLAX", 0.18)],
                ),
                "chemicals":    (
                    [("NLRTM", 0.26), ("DEHAM", 0.22), ("AEJEA", 0.18)],
                    [("CNSHA", 0.22), ("SGSIN", 0.18), ("USLAX", 0.16)],
                ),
                "agriculture":  (
                    [("USSAV", 0.24), ("BRSAO", 0.22), ("USLGB", 0.18)],
                    [("CNSHA", 0.26), ("NLRTM", 0.18), ("AEJEA", 0.14)],
                ),
                "metals":       (
                    [("CNSHA", 0.30), ("CNNBO", 0.22), ("KRPUS", 0.16)],
                    [("NLRTM", 0.22), ("USLAX", 0.18), ("BRSAO", 0.14)],
                ),
            }
            raw_origins, raw_dests = illustrative.get(cat_key, ([], []))
            top_origins = [(locode, total_value * share) for locode, share in raw_origins]
            top_dests   = [(locode, total_value * share) for locode, share in raw_dests]
        else:
            top_origins = sorted(origin_totals.items(), key=lambda x: x[1], reverse=True)[:3]
            top_dests   = sorted(dest_totals.items(),   key=lambda x: x[1], reverse=True)[:3]

        # ── Map dominant routes ────────────────────────────────────────────
        dominant_routes: list[str] = []
        for route_id, (origin_region, dest_region) in _ROUTE_REGIONS.items():
            dominant = _REGION_DOMINANT_CARGO.get(origin_region, [])
            if cat_key in dominant:
                dominant_routes.append(route_id)
        top_routes = dominant_routes[:4]

        # ── Demand signal ─────────────────────────────────────────────────
        signal, color = _demand_signal(yoy_growth)
        insight = _key_insight(cat_key, yoy_growth, signal)

        results.append(
            CargoFlowAnalysis(
                hs_category=cat_key,
                category_label=cat_meta["label"],
                hs_codes=cat_meta["codes"],
                total_value_usd=total_value,
                top_origin_ports=top_origins,
                top_dest_ports=top_dests,
                top_routes=top_routes,
                yoy_growth_pct=yoy_growth,
                seasonality_peak_month=peak_month,
                shipping_characteristics=shipping,
                demand_signal=signal,
                signal_color=color,
                key_insight=insight,
            )
        )

    return results


def get_route_cargo_mix(route_id: str, trade_data: dict) -> dict[str, float]:
    """Return category -> share of total value (0-1) on the given route.

    Falls back to illustrative weights when real data is unavailable.
    """
    # Try to build from real data: find ports matching the route's origin
    origin_locode = None
    if route_id in _ROUTE_REGIONS:
        origin_region, _ = _ROUTE_REGIONS[route_id]
    else:
        origin_region = None

    cat_totals: dict[str, float] = defaultdict(float)
    has_data = False

    for locode, df in (trade_data or {}).items():
        if df is None or not hasattr(df, "empty") or df.empty:
            continue
        if "hs_category" not in df.columns or "value_usd" not in df.columns:
            continue
        has_data = True
        for cat_key in HS_CATEGORIES:
            cat_rows = df[df["hs_category"] == cat_key]
            if not cat_rows.empty:
                cat_totals[cat_key] += float(cat_rows["value_usd"].sum())

    if has_data and sum(cat_totals.values()) > 0:
        grand_total = sum(cat_totals.values())
        return {k: v / grand_total for k, v in cat_totals.items()}

    # Illustrative fallback weights per route
    _ROUTE_MIX_FALLBACK: dict[str, dict[str, float]] = {
        "transpacific_eb":       {"electronics": 0.38, "machinery": 0.22, "apparel": 0.18, "chemicals": 0.10, "metals": 0.07, "automotive": 0.05},
        "asia_europe":           {"electronics": 0.30, "machinery": 0.25, "apparel": 0.20, "chemicals": 0.12, "metals": 0.08, "agriculture": 0.05},
        "transpacific_wb":       {"agriculture": 0.40, "chemicals": 0.25, "machinery": 0.18, "metals": 0.10, "electronics": 0.07},
        "transatlantic":         {"machinery": 0.30, "chemicals": 0.25, "automotive": 0.20, "electronics": 0.15, "agriculture": 0.10},
        "sea_transpacific_eb":   {"electronics": 0.35, "apparel": 0.25, "agriculture": 0.18, "chemicals": 0.12, "machinery": 0.10},
        "ningbo_europe":         {"electronics": 0.32, "machinery": 0.24, "apparel": 0.20, "metals": 0.12, "chemicals": 0.12},
        "middle_east_to_europe": {"chemicals": 0.45, "metals": 0.25, "agriculture": 0.15, "machinery": 0.15},
        "middle_east_to_asia":   {"chemicals": 0.50, "metals": 0.30, "machinery": 0.20},
        "south_asia_to_europe":  {"apparel": 0.45, "agriculture": 0.30, "chemicals": 0.15, "metals": 0.10},
        "intra_asia_china_sea":  {"electronics": 0.35, "machinery": 0.25, "chemicals": 0.20, "metals": 0.20},
        "intra_asia_china_japan":{"electronics": 0.30, "machinery": 0.30, "automotive": 0.25, "metals": 0.15},
        "china_south_america":   {"electronics": 0.30, "machinery": 0.28, "apparel": 0.22, "chemicals": 0.12, "metals": 0.08},
        "europe_south_america":  {"machinery": 0.32, "chemicals": 0.28, "automotive": 0.22, "agriculture": 0.18},
        "med_hub_to_asia":       {"machinery": 0.30, "chemicals": 0.25, "automotive": 0.22, "agriculture": 0.23},
        "north_africa_to_europe":{"agriculture": 0.40, "metals": 0.30, "chemicals": 0.20, "machinery": 0.10},
        "us_east_south_america": {"machinery": 0.30, "agriculture": 0.28, "chemicals": 0.22, "automotive": 0.20},
        "longbeach_to_asia":     {"agriculture": 0.38, "chemicals": 0.26, "machinery": 0.20, "metals": 0.16},
    }
    mix = _ROUTE_MIX_FALLBACK.get(route_id, {})
    if not mix:
        # Generic fallback
        mix = {k: 1.0 / len(HS_CATEGORIES) for k in HS_CATEGORIES}
    # Normalise to sum to 1 in case of floating-point drift
    total = sum(mix.values())
    return {k: v / total for k, v in mix.items()} if total > 0 else mix


def get_seasonal_cargo_calendar() -> dict[int, list[str]]:
    """Return month (1-12) -> list of cargo categories peaking that month."""
    calendar: dict[int, list[str]] = {m: [] for m in range(1, 13)}
    for cat_key, chars in CARGO_CHARACTERISTICS.items():
        peak = chars.get("seasonal_peak", 1)
        if 1 <= peak <= 12:
            calendar[peak].append(cat_key)
    return calendar
