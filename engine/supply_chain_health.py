"""
Supply Chain Health Index (SCHI)

Produces a single composite score [0, 1] representing the overall health of
the global container shipping supply chain, synthesised from six sub-dimensions:

  1. Port Capacity         (weight 0.22)
  2. Freight Cost Pressure (weight 0.20)
  3. Macro Environment     (weight 0.18)
  4. Chokepoint Risk       (weight 0.15)
  5. Inventory Cycle       (weight 0.15)
  6. Seasonal Factors      (weight 0.10)

Score interpretation
--------------------
>= 0.70   Healthy    — normal or favourable shipping conditions
0.50-0.70 Recovering — below-average but improving or mixed
0.35-0.50 Stressed   — meaningful disruptions or headwinds present
< 0.35    Critical   — severe disruption; major cost and capacity impacts
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

# ---------------------------------------------------------------------------
# Default freight rates mirror the _DEFAULT_RATES table in data/freight_scraper.py
# (FBX index → neutral long-run USD/FEU rate).
# ---------------------------------------------------------------------------
_DEFAULT_RATES_BY_FBX: dict[str, float] = {
    "FBX01": 2500.0,   # Trans-Pacific EB
    "FBX02": 800.0,    # Trans-Pacific WB
    "FBX03": 1800.0,   # Asia-Europe
    "FBX11": 1200.0,   # Transatlantic
}

# Fallback default when no FBX index is known for a route
_GLOBAL_DEFAULT_RATE = 1500.0

# Dimension weights — must sum to 1.0
_WEIGHTS: dict[str, float] = {
    "port_capacity":         0.22,
    "freight_cost_pressure": 0.20,
    "macro_environment":     0.18,
    "chokepoint_risk":       0.15,
    "inventory_cycle":       0.15,
    "seasonal_factors":      0.10,
}

assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Dimension weights must sum to 1.0"


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class SupplyChainHealthReport:
    """Composite supply chain health output produced by compute_supply_chain_health().

    All scores are in [0, 1] where 1 = perfectly healthy, 0 = severely disrupted.
    """

    overall_score: float
    """Weighted composite of all six sub-dimensions."""

    overall_label: str
    """Human-readable health label: 'Healthy' | 'Recovering' | 'Stressed' | 'Critical'."""

    overall_color: str
    """Hex colour associated with the overall label (for UI badges/gauges)."""

    dimension_scores: dict[str, float]
    """Sub-dimension scores keyed by dimension name (same keys as _WEIGHTS)."""

    dimension_labels: dict[str, str]
    """Human-readable label for each sub-dimension score."""

    key_risks: list[str]
    """Top risk factors (up to 3 text descriptions)."""

    key_tailwinds: list[str]
    """Top positive factors (up to 3 text descriptions)."""

    week_over_week_change: float = 0.0
    """Week-over-week SCHI change. Placeholder — historical SCHI data not yet stored."""

    data_timestamp: str = ""
    """ISO-8601 timestamp when the report was generated."""


# ---------------------------------------------------------------------------
# Label / colour helpers
# ---------------------------------------------------------------------------

def _label_and_color(score: float) -> tuple[str, str]:
    """Return (label, hex_color) for a composite score."""
    if score >= 0.70:
        return "Healthy",    "#10b981"   # green
    if score >= 0.50:
        return "Recovering", "#3b82f6"   # blue
    if score >= 0.35:
        return "Stressed",   "#f59e0b"   # yellow / amber
    return     "Critical",   "#ef4444"   # red


def _dimension_label(score: float) -> str:
    """Human-readable label for a dimension score."""
    if score >= 0.70:
        return "Healthy"
    if score >= 0.50:
        return "Moderate"
    if score >= 0.35:
        return "Stressed"
    return "Critical"


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------

def _score_port_capacity(port_results: list) -> float:
    """Score = 1 - avg(demand_score) across all ports.

    High port demand signals congestion and capacity stress; inverting it gives
    a health score where 1 = ample capacity and 0 = severe congestion.
    """
    if not port_results:
        return 0.5
    scores = [getattr(p, "demand_score", 0.5) for p in port_results]
    avg_demand = sum(scores) / len(scores)
    return 1.0 - avg_demand


def _score_freight_cost_pressure(freight_data: dict) -> float:
    """Score = 1 - avg_normalised_rate across all routes with data.

    Each route's normalised rate = min(current / default, 2.0) / 2.0.
    This maps a rate at 2× the historical default to a stress score of 1.0,
    and a rate at or below the default to a stress score of 0.5.
    Inverting gives a *health* score.
    """
    if not freight_data:
        return 0.5

    normalised: list[float] = []
    for route_id, df in freight_data.items():
        try:
            if df is None or df.empty:
                continue
            current_rate = float(df["rate_usd_per_feu"].iloc[-1])
            if current_rate <= 0:
                continue

            # Determine the default reference rate for this route.
            # We look up the FBX index from the DataFrame if available,
            # otherwise fall back to the global default.
            fbx_index = ""
            if "index_name" in df.columns:
                fbx_index = str(df["index_name"].iloc[-1])
            default_rate = _DEFAULT_RATES_BY_FBX.get(fbx_index, _GLOBAL_DEFAULT_RATE)

            ratio = min(current_rate / default_rate, 2.0) / 2.0
            normalised.append(ratio)
        except Exception as exc:
            logger.debug(f"Freight cost scoring error for route {route_id}: {exc}")

    if not normalised:
        return 0.5

    avg_stress = sum(normalised) / len(normalised)
    return 1.0 - avg_stress


def _score_macro_environment(macro_data: dict) -> float:
    """Replicate the macro scoring logic from InsightScorer._macro_insight().

    Components: industrial production (PMI proxy) + BDI + fuel cost (inverse)
    + inventory cycle.  Weights mirror scorer.py.
    """
    if not macro_data:
        return 0.5

    try:
        from data.fred_feed import compute_bdi_score
        bdi_score = compute_bdi_score(macro_data)
    except Exception:
        bdi_score = 0.5

    try:
        ipman_df = macro_data.get("IPMAN")
        if ipman_df is not None and not ipman_df.empty:
            vals = ipman_df["value"].dropna()
            if not vals.empty:
                current = vals.iloc[-1]
                avg = vals.tail(90).mean()
                pmi_proxy = min(1.0, max(0.0, (current / avg - 0.9) / 0.2)) if avg > 0 else 0.5
            else:
                pmi_proxy = 0.5
        else:
            pmi_proxy = 0.5
    except Exception:
        pmi_proxy = 0.5

    try:
        wti_df = macro_data.get("DCOILWTICO")
        if wti_df is not None and not wti_df.empty:
            wti_val = float(wti_df["value"].dropna().iloc[-1])
            wti_norm = max(0.0, min(1.0, (wti_val - 40) / 80))
            fuel_inverse = 1.0 - wti_norm
        else:
            fuel_inverse = 0.5
    except Exception:
        fuel_inverse = 0.5

    try:
        from processing.inventory_analyzer import get_inventory_score_for_engine
        inventory_score = get_inventory_score_for_engine(macro_data)
    except Exception:
        inventory_score = 0.5

    macro_score = (
        0.30 * pmi_proxy
        + 0.28 * bdi_score
        + 0.22 * fuel_inverse
        + 0.20 * inventory_score
    )
    return float(max(0.0, min(1.0, macro_score)))


def _score_chokepoint_risk() -> float:
    """Score = 1 - (n_high_risk * 0.15 + n_moderate * 0.07), clamped to [0, 1].

    More high/critical chokepoints → lower health score.
    """
    try:
        from processing.risk_monitor import CHOKEPOINTS, get_high_risk_alerts

        high_risk_chokepoints = get_high_risk_alerts()
        n_high = len(high_risk_chokepoints)

        n_moderate = sum(
            1 for c in CHOKEPOINTS
            if c.risk_level == "MODERATE"
        )

        raw = 1.0 - (n_high * 0.15 + n_moderate * 0.07)
        return float(max(0.0, min(1.0, raw)))
    except Exception as exc:
        logger.debug(f"Chokepoint risk scoring error: {exc}")
        return 0.5


def _score_inventory_cycle(macro_data: dict) -> float:
    """Pass-through of get_inventory_score_for_engine().

    High I:S drawdown (lean inventories) → high restocking demand → bullish
    for shipping → high health score from the shipping demand perspective.
    """
    try:
        from processing.inventory_analyzer import get_inventory_score_for_engine
        return float(get_inventory_score_for_engine(macro_data))
    except Exception as exc:
        logger.debug(f"Inventory cycle scoring error: {exc}")
        return 0.5


def _score_seasonal_factors() -> float:
    """Score derived from currently-active seasonal signals.

    Each active signal contributes a score_adjustment derived from its
    direction and strength:
        bullish  → +strength  (positive adjustment)
        bearish  → -strength  (negative adjustment)
        neutral  → 0

    Final score = 0.5
                  + sum(positive adjustments) * 0.3
                  - sum(abs(negative adjustments)) * 0.3
    Clamped to [0.1, 0.9].
    """
    try:
        from processing.seasonal import get_active_seasonal_signals

        active = [s for s in get_active_seasonal_signals() if s.active_now]

        positive_sum = sum(
            s.strength for s in active if s.direction == "bullish"
        )
        negative_sum = sum(
            s.strength for s in active if s.direction == "bearish"
        )

        score = 0.5 + positive_sum * 0.3 - negative_sum * 0.3
        return float(max(0.1, min(0.9, score)))
    except Exception as exc:
        logger.debug(f"Seasonal factor scoring error: {exc}")
        return 0.5


# ---------------------------------------------------------------------------
# Risk / tailwind narrative builders
# ---------------------------------------------------------------------------

def _build_key_risks(
    dim_scores: dict[str, float],
    freight_data: dict,
) -> list[str]:
    """Return up to 3 risk-factor descriptions.

    Sources:
      - Bottom 2 dimension scores (lowest health → highest risk)
      - Worst freight route if its rate exceeds 2× its default
    """
    risks: list[str] = []

    _DIM_DISPLAY = {
        "port_capacity":         "Port Capacity",
        "freight_cost_pressure": "Freight Cost Pressure",
        "macro_environment":     "Macro Environment",
        "chokepoint_risk":       "Chokepoint Risk",
        "inventory_cycle":       "Inventory Cycle",
        "seasonal_factors":      "Seasonal Factors",
    }

    sorted_dims = sorted(dim_scores.items(), key=lambda kv: kv[1])
    for dim_key, score in sorted_dims[:2]:
        label = _DIM_DISPLAY.get(dim_key, dim_key)
        risks.append(
            f"{label} is under pressure (score {score:.0%})"
        )

    # Worst freight route
    worst_route_id: str | None = None
    worst_ratio: float = 0.0
    for route_id, df in (freight_data or {}).items():
        try:
            if df is None or df.empty:
                continue
            current_rate = float(df["rate_usd_per_feu"].iloc[-1])
            fbx_index = ""
            if "index_name" in df.columns:
                fbx_index = str(df["index_name"].iloc[-1])
            default_rate = _DEFAULT_RATES_BY_FBX.get(fbx_index, _GLOBAL_DEFAULT_RATE)
            ratio = current_rate / default_rate if default_rate > 0 else 1.0
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst_route_id = route_id
        except Exception:
            pass

    if worst_route_id and worst_ratio > 2.0:
        risks.append(
            f"Freight rates on {worst_route_id.replace('_', ' ').title()} "
            f"are {worst_ratio:.1f}× the long-run average"
        )

    return risks[:3]


def _build_key_tailwinds(
    dim_scores: dict[str, float],
) -> list[str]:
    """Return up to 3 tailwind descriptions.

    Sources:
      - Top 2 dimension scores (highest health)
      - Positive seasonal signal if active
    """
    tailwinds: list[str] = []

    _DIM_DISPLAY = {
        "port_capacity":         "Port Capacity",
        "freight_cost_pressure": "Freight Cost Pressure",
        "macro_environment":     "Macro Environment",
        "chokepoint_risk":       "Chokepoint Risk",
        "inventory_cycle":       "Inventory Cycle",
        "seasonal_factors":      "Seasonal Factors",
    }

    sorted_dims = sorted(dim_scores.items(), key=lambda kv: kv[1], reverse=True)
    for dim_key, score in sorted_dims[:2]:
        label = _DIM_DISPLAY.get(dim_key, dim_key)
        tailwinds.append(
            f"{label} is supportive (score {score:.0%})"
        )

    # Positive seasonal signal
    try:
        from processing.seasonal import get_active_seasonal_signals
        active_bullish = [
            s for s in get_active_seasonal_signals()
            if s.active_now and s.direction == "bullish"
        ]
        if active_bullish:
            strongest = max(active_bullish, key=lambda s: s.strength)
            tailwinds.append(
                f"Seasonal tailwind: {strongest.name} "
                f"(strength {strongest.strength:.0%})"
            )
    except Exception:
        pass

    return tailwinds[:3]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_supply_chain_health(
    port_results: list,
    freight_data: dict,
    macro_data: dict,
    route_results: list,
) -> SupplyChainHealthReport:
    """Compute the Supply Chain Health Index (SCHI) from all available signals.

    Parameters
    ----------
    port_results:
        list[PortDemandResult] from processing.demand_analyzer.
    freight_data:
        dict[route_id, DataFrame] from data.freight_scraper.fetch_fbx_rates().
    macro_data:
        dict[series_id, DataFrame] from data.fred_feed.
    route_results:
        list[RouteOpportunity] from routes.optimizer (informational; reserved
        for future dimensions).

    Returns
    -------
    SupplyChainHealthReport
        Fully populated report including composite score, dimension breakdown,
        key risks and tailwinds, and a generation timestamp.
    """
    from utils.helpers import now_iso

    # ------------------------------------------------------------------
    # 1. Compute each dimension score (exceptions → neutral 0.5)
    # ------------------------------------------------------------------
    dim_scores: dict[str, float] = {}

    try:
        dim_scores["port_capacity"] = _score_port_capacity(port_results)
    except Exception as exc:
        logger.warning(f"Port capacity scoring failed: {exc}")
        dim_scores["port_capacity"] = 0.5

    try:
        dim_scores["freight_cost_pressure"] = _score_freight_cost_pressure(freight_data)
    except Exception as exc:
        logger.warning(f"Freight cost pressure scoring failed: {exc}")
        dim_scores["freight_cost_pressure"] = 0.5

    try:
        dim_scores["macro_environment"] = _score_macro_environment(macro_data)
    except Exception as exc:
        logger.warning(f"Macro environment scoring failed: {exc}")
        dim_scores["macro_environment"] = 0.5

    try:
        dim_scores["chokepoint_risk"] = _score_chokepoint_risk()
    except Exception as exc:
        logger.warning(f"Chokepoint risk scoring failed: {exc}")
        dim_scores["chokepoint_risk"] = 0.5

    try:
        dim_scores["inventory_cycle"] = _score_inventory_cycle(macro_data)
    except Exception as exc:
        logger.warning(f"Inventory cycle scoring failed: {exc}")
        dim_scores["inventory_cycle"] = 0.5

    try:
        dim_scores["seasonal_factors"] = _score_seasonal_factors()
    except Exception as exc:
        logger.warning(f"Seasonal factor scoring failed: {exc}")
        dim_scores["seasonal_factors"] = 0.5

    # ------------------------------------------------------------------
    # 2. Weighted average → overall_score
    # ------------------------------------------------------------------
    overall_score = sum(
        _WEIGHTS[dim] * score
        for dim, score in dim_scores.items()
    )
    overall_score = float(max(0.0, min(1.0, overall_score)))

    # ------------------------------------------------------------------
    # 3. Label and colour
    # ------------------------------------------------------------------
    overall_label, overall_color = _label_and_color(overall_score)

    # ------------------------------------------------------------------
    # 4. Dimension labels
    # ------------------------------------------------------------------
    dimension_labels = {dim: _dimension_label(score) for dim, score in dim_scores.items()}

    # ------------------------------------------------------------------
    # 5. Key risks and tailwinds
    # ------------------------------------------------------------------
    key_risks = _build_key_risks(dim_scores, freight_data or {})
    key_tailwinds = _build_key_tailwinds(dim_scores)

    # ------------------------------------------------------------------
    # 6. Assemble and return the report
    # ------------------------------------------------------------------
    report = SupplyChainHealthReport(
        overall_score=overall_score,
        overall_label=overall_label,
        overall_color=overall_color,
        dimension_scores=dim_scores,
        dimension_labels=dimension_labels,
        key_risks=key_risks,
        key_tailwinds=key_tailwinds,
        week_over_week_change=0.0,   # placeholder — no historical SCHI stored yet
        data_timestamp=now_iso(),
    )

    logger.info(
        f"SCHI computed: {overall_score:.3f} ({overall_label}) | "
        f"dims={', '.join(f'{k}={v:.2f}' for k, v in dim_scores.items())}"
    )
    return report


# ---------------------------------------------------------------------------
# Integration note for app.py
# ---------------------------------------------------------------------------
# To integrate supply chain health into app.py, add after the insight scoring:
# from engine.supply_chain_health import compute_supply_chain_health
# sc_health = compute_supply_chain_health(port_results, freight_data, macro_data, route_results)
# Then pass sc_health to tab_overview render()
