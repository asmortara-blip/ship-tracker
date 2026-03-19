"""trade_imbalance.py — Analyzes import/export imbalances between regions to generate
directional shipping signals.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.signals import SignalComponent
from ports.port_registry import PORTS_BY_LOCODE


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class TradeImbalanceSignal:
    origin_region: str
    dest_region: str
    route_id: str
    import_value_usd: float       # imports into dest region from origin
    export_value_usd: float       # exports from origin to dest region
    imbalance_ratio: float        # export/import ratio. >1 = more exports than imports
    imbalance_direction: str      # "export_heavy" / "import_heavy" / "balanced"
    shipping_implication: str     # human-readable interpretation
    opportunity_score: float      # [0,1] — how actionable is this imbalance?


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_trade_imbalances(
    trade_data: dict,
    route_results: list,
) -> list[TradeImbalanceSignal]:
    """Analyze import/export imbalances for each route and return signals.

    Parameters
    ----------
    trade_data:
        Mapping of port_locode -> DataFrame with columns:
        value_usd, flow (Import/Export), reporter_iso3, partner_iso3, hs_category
    route_results:
        List of ShippingRoute (or duck-typed objects with the same fields).

    Returns
    -------
    List of TradeImbalanceSignal, one per route.  Returns [] when trade_data
    is missing or empty.
    """
    if not trade_data:
        return []

    signals: list[TradeImbalanceSignal] = []

    for route in route_results:
        origin_locode: str = route.origin_locode
        dest_locode: str = route.dest_locode

        # Resolve port metadata
        origin_port = PORTS_BY_LOCODE.get(origin_locode)
        dest_port = PORTS_BY_LOCODE.get(dest_locode)

        if origin_port is None or dest_port is None:
            continue

        origin_iso3: str = origin_port.country_iso3
        dest_iso3: str = dest_port.country_iso3

        # -----------------------------------------------------------------
        # Aggregate trade values
        # -----------------------------------------------------------------
        export_value: float = 0.0
        import_value: float = 0.0

        for locode, df in trade_data.items():
            if df is None or df.empty:
                continue

            # Work on a copy to avoid SettingWithCopyWarning
            df = df.copy()

            # Normalise column names to lowercase for robustness
            df.columns = [c.lower() for c in df.columns]

            if "value_usd" not in df.columns or "flow" not in df.columns:
                continue
            if "reporter_iso3" not in df.columns:
                continue

            # --- Export legs: origin country reports exports to dest country ---
            if "partner_iso3" in df.columns:
                export_mask = (
                    (df["reporter_iso3"] == origin_iso3)
                    & (df["flow"].str.lower() == "export")
                    & (df["partner_iso3"] == dest_iso3)
                )
                export_value += float(df.loc[export_mask, "value_usd"].sum())

                # --- Import legs: dest country reports imports from origin country ---
                import_mask = (
                    (df["reporter_iso3"] == dest_iso3)
                    & (df["flow"].str.lower() == "import")
                    & (df["partner_iso3"] == origin_iso3)
                )
                import_value += float(df.loc[import_mask, "value_usd"].sum())
            else:
                # Fallback: no partner filter — use reporter_iso3 alone
                export_mask = (
                    (df["reporter_iso3"] == origin_iso3)
                    & (df["flow"].str.lower() == "export")
                )
                export_value += float(df.loc[export_mask, "value_usd"].sum())

                import_mask = (
                    (df["reporter_iso3"] == dest_iso3)
                    & (df["flow"].str.lower() == "import")
                )
                import_value += float(df.loc[import_mask, "value_usd"].sum())

        # -----------------------------------------------------------------
        # Derived metrics
        # -----------------------------------------------------------------
        imbalance_ratio: float = export_value / max(import_value, 1.0)

        if imbalance_ratio > 1.3:
            imbalance_direction = "export_heavy"
        elif imbalance_ratio < 0.77:
            imbalance_direction = "import_heavy"
        else:
            imbalance_direction = "balanced"

        origin_label = origin_port.name
        dest_label = dest_port.name

        if imbalance_direction == "export_heavy":
            shipping_implication = (
                f"High export pressure on {origin_label} → strong demand for "
                f"{origin_label}→{dest_label} capacity. "
                f"Return leg ({dest_label}→{origin_label}) likely underutilized."
            )
        elif imbalance_direction == "import_heavy":
            shipping_implication = (
                f"Import surge into {dest_label} → high demand for inbound "
                f"{origin_label}→{dest_label} lanes."
            )
        else:
            shipping_implication = (
                f"Balanced two-way trade on {origin_label}↔{dest_label} corridor."
            )

        raw_score = abs(imbalance_ratio - 1.0) * 0.5
        opportunity_score: float = min(max(raw_score, 0.0), 1.0)

        signals.append(
            TradeImbalanceSignal(
                origin_region=route.origin_region,
                dest_region=route.dest_region,
                route_id=route.id,
                import_value_usd=import_value,
                export_value_usd=export_value,
                imbalance_ratio=imbalance_ratio,
                imbalance_direction=imbalance_direction,
                shipping_implication=shipping_implication,
                opportunity_score=opportunity_score,
            )
        )

    return signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_top_imbalances(
    signals: list[TradeImbalanceSignal],
    n: int = 5,
) -> list[TradeImbalanceSignal]:
    """Return the top N signals sorted by opportunity_score descending."""
    return sorted(signals, key=lambda s: s.opportunity_score, reverse=True)[:n]


def imbalance_to_signal_component(sig: TradeImbalanceSignal) -> SignalComponent:
    """Convert a TradeImbalanceSignal to a SignalComponent for the scoring engine."""
    name = f"Trade Imbalance: {sig.origin_region}→{sig.dest_region}"
    value = sig.opportunity_score
    weight = 0.15
    label = sig.shipping_implication[:80]

    if sig.imbalance_direction == "export_heavy":
        direction = "bullish"
    elif sig.imbalance_direction == "import_heavy":
        direction = "bearish"
    else:
        direction = "neutral"

    return SignalComponent(
        name=name,
        value=value,
        weight=weight,
        label=label,
        direction=direction,
    )


def get_imbalance_summary(signals: list[TradeImbalanceSignal]) -> dict:
    """Return a summary dict for all imbalance signals.

    Keys
    ----
    total_routes   : int
    export_heavy   : int
    import_heavy   : int
    balanced       : int
    avg_opportunity: float
    top_route      : str
    """
    total = len(signals)
    export_heavy = sum(1 for s in signals if s.imbalance_direction == "export_heavy")
    import_heavy = sum(1 for s in signals if s.imbalance_direction == "import_heavy")
    balanced = sum(1 for s in signals if s.imbalance_direction == "balanced")
    avg_opportunity = (
        sum(s.opportunity_score for s in signals) / total if total > 0 else 0.0
    )

    if signals:
        top = max(signals, key=lambda s: s.opportunity_score)
        top_route = top.route_id
    else:
        top_route = ""

    return {
        "total_routes": total,
        "export_heavy": export_heavy,
        "import_heavy": import_heavy,
        "balanced": balanced,
        "avg_opportunity": avg_opportunity,
        "top_route": top_route,
    }
