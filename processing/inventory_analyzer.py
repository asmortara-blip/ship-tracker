"""
Inventory Cycle Analyzer

The inventory-to-sales (I:S) ratio is a key leading indicator for container shipping:
- Low I:S (lean inventories): Companies need to restock → import surge → shipping demand UP
- High I:S (overstocked): Imports slow as companies draw down stocks → shipping demand DOWN
- Trend direction matters as much as absolute level

Typical I:S ratio historical range: 1.20 (lean) to 1.60 (overstocked)
Pre-COVID normal: ~1.40-1.45
Post-COVID distortion: spiked to 1.55+ in 2022-2023 as retailers overstocked
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from loguru import logger


@dataclass
class InventoryCycleSignal:
    """The current state of the inventory restocking cycle."""
    phase: str                    # "RESTOCK" | "DRAWDOWN" | "NEUTRAL" | "BUILDING"
    phase_description: str
    is_ratio_current: float       # Latest total business I:S ratio
    is_ratio_retail: float        # Latest retail I:S ratio
    is_ratio_vs_5yr_avg: float    # Current vs 5-year average (positive = above avg = overstocked)
    trend_direction: str          # "Falling" | "Rising" | "Stable"
    trend_pct_6m: float           # 6-month % change in I:S ratio
    shipping_implication: str     # What this means for container demand
    score: float                  # [0, 1] bullish score for shipping demand
    consumer_sentiment: float | None   # Latest UMich sentiment (leading indicator)
    new_orders_trend: str | None       # Manufacturing new orders direction


# Long-run "normal" I:S ratio (pre-pandemic baseline)
_NORMAL_IS_RATIO = 1.42
_LEAN_THRESHOLD = 1.32      # Below this = lean inventory = restocking coming
_OVERSTOCK_THRESHOLD = 1.52  # Above this = overstocked = import slowdown


def analyze_inventory_cycle(macro_data: dict[str, pd.DataFrame]) -> InventoryCycleSignal | None:
    """Derive the current inventory cycle phase from FRED data.

    Returns None if insufficient data is available.
    """
    is_df = macro_data.get("ISRATIO")
    if is_df is None or is_df.empty or len(is_df) < 12:
        logger.debug("Insufficient ISRATIO data for inventory analysis")
        return None

    is_retail_df = macro_data.get("MRTSIR44X722USS")
    sentiment_df = macro_data.get("UMCSENT")
    new_orders_df = macro_data.get("AMTMNO")

    # Current I:S ratio
    is_values = is_df["value"].dropna()
    current_is = float(is_values.iloc[-1])

    # 5-year average comparison
    five_yr = is_values.tail(60).mean()  # ~60 months = 5 years
    vs_avg = (current_is - five_yr) / five_yr

    # 6-month trend
    six_month_ago = is_values.tail(7).iloc[0] if len(is_values) >= 7 else is_values.iloc[0]
    trend_pct_6m = (current_is - float(six_month_ago)) / float(six_month_ago) if float(six_month_ago) > 0 else 0.0
    trend_dir = "Rising" if trend_pct_6m > 0.01 else ("Falling" if trend_pct_6m < -0.01 else "Stable")

    # Retail I:S
    current_retail_is = 0.0
    if is_retail_df is not None and not is_retail_df.empty:
        retail_values = is_retail_df["value"].dropna()
        if not retail_values.empty:
            current_retail_is = float(retail_values.iloc[-1])

    # Consumer sentiment
    consumer_sent = None
    if sentiment_df is not None and not sentiment_df.empty:
        sent_values = sentiment_df["value"].dropna()
        if not sent_values.empty:
            consumer_sent = float(sent_values.iloc[-1])

    # New orders trend
    new_orders_trend = None
    if new_orders_df is not None and not new_orders_df.empty:
        no_values = new_orders_df["value"].dropna()
        if len(no_values) >= 3:
            recent = no_values.tail(3)
            slope = float(recent.iloc[-1]) - float(recent.iloc[0])
            new_orders_trend = "Rising" if slope > 0 else ("Falling" if slope < 0 else "Stable")

    # Determine phase
    phase, phase_desc, implication, score = _classify_phase(
        current_is, trend_dir, trend_pct_6m, consumer_sent, new_orders_trend
    )

    return InventoryCycleSignal(
        phase=phase,
        phase_description=phase_desc,
        is_ratio_current=current_is,
        is_ratio_retail=current_retail_is,
        is_ratio_vs_5yr_avg=vs_avg,
        trend_direction=trend_dir,
        trend_pct_6m=trend_pct_6m,
        shipping_implication=implication,
        score=score,
        consumer_sentiment=consumer_sent,
        new_orders_trend=new_orders_trend,
    )


def _classify_phase(
    is_ratio: float,
    trend: str,
    trend_pct: float,
    sentiment: float | None,
    new_orders: str | None,
) -> tuple[str, str, str, float]:
    """Return (phase, description, implication, score)."""

    # Sentiment bonus: high consumer confidence → restocking confidence
    sentiment_adj = 0.0
    if sentiment is not None:
        # Normal UMich range: 60-100. Above 85 = confident, below 65 = pessimistic
        sentiment_adj = (sentiment - 75) / 100  # small adjustment [-0.15, +0.15]

    if is_ratio <= _LEAN_THRESHOLD and trend == "Falling":
        # Best case: lean AND falling → restock wave imminent
        return (
            "RESTOCK",
            f"Inventories critically lean (I:S {is_ratio:.2f}) and still falling",
            "Retailers must restock. Expect significant import surge in coming 4-8 weeks. "
            "Container demand likely to rise sharply on Trans-Pacific and Asia-Europe lanes.",
            min(1.0, 0.85 + sentiment_adj),
        )
    elif is_ratio <= _LEAN_THRESHOLD:
        # Lean but stable/rising
        return (
            "RESTOCK",
            f"Inventories lean (I:S {is_ratio:.2f}), below historical normal",
            "Lean inventories support continued import demand. Shipping rates likely to remain firm.",
            min(1.0, 0.72 + sentiment_adj),
        )
    elif is_ratio >= _OVERSTOCK_THRESHOLD and trend == "Rising":
        # Worst case: overstocked AND rising → demand will slow
        return (
            "DRAWDOWN",
            f"Inventories elevated (I:S {is_ratio:.2f}) and still rising",
            "Companies drawing down excess stock. Import volumes likely to decline. "
            "Container demand weakness expected for 2-4 months.",
            max(0.0, 0.25 + sentiment_adj),
        )
    elif is_ratio >= _OVERSTOCK_THRESHOLD:
        # Overstocked but not worsening
        return (
            "DRAWDOWN",
            f"Inventories elevated (I:S {is_ratio:.2f}), above normal range",
            "Above-normal inventories suppress import demand. Shipping volumes may soften.",
            max(0.0, 0.35 + sentiment_adj),
        )
    elif trend == "Falling" and is_ratio > _LEAN_THRESHOLD:
        # Normalizing from above: approaching lean territory
        return (
            "BUILDING",
            f"Inventories normalizing downward (I:S {is_ratio:.2f}, {trend_pct*100:+.1f}% 6m)",
            "Inventories trending toward lean territory. Restocking cycle could begin within 1-2 quarters.",
            min(1.0, 0.60 + sentiment_adj),
        )
    else:
        return (
            "NEUTRAL",
            f"Inventories near historical normal (I:S {is_ratio:.2f})",
            "Inventory levels balanced. Shipping demand follows underlying economic growth.",
            min(1.0, 0.50 + sentiment_adj),
        )


def get_inventory_score_for_engine(macro_data: dict[str, pd.DataFrame]) -> float:
    """Return a [0,1] score for use in the decision engine macro calculation."""
    signal = analyze_inventory_cycle(macro_data)
    if signal is None:
        return 0.5
    return signal.score
