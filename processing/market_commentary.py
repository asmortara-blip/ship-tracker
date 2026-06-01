"""market_commentary.py — WSJ-style auto-generated market commentary engine.

Generates editorial-quality narrative paragraphs from live market data:
  1. Daily Market Wrap — headline + 2-3 paragraph summary
  2. Sector Commentary — per-sector outlook narrative
  3. Forward-Looking Analysis — risk/opportunity assessment
  4. Key Movers — biggest changes with editorial context
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger


def _safe_last(df: pd.DataFrame | pd.Series | None) -> float | None:
    if df is None:
        return None
    try:
        if isinstance(df, pd.DataFrame):
            if df.empty:
                return None
            # Freight frames have `rate_usd_per_feu`; FRED macro frames have `value`.
            if "rate_usd_per_feu" in df.columns:
                col = df["rate_usd_per_feu"]
            elif "value" in df.columns:
                col = df["value"]
            else:
                col = df.iloc[:, -1]
            vals = pd.to_numeric(col, errors="coerce").dropna()
            return float(vals.iloc[-1]) if len(vals) > 0 else None
        return float(pd.to_numeric(df, errors="coerce").dropna().iloc[-1])
    except Exception:
        return None


def _safe_pct_change(df: pd.DataFrame | pd.Series | None, periods: int = 1) -> float | None:
    if df is None:
        return None
    try:
        if isinstance(df, pd.DataFrame):
            if df.empty:
                return None
            if "rate_usd_per_feu" in df.columns:
                col = df["rate_usd_per_feu"]
            elif "value" in df.columns:
                col = df["value"]
            else:
                col = df.iloc[:, -1]
            vals = pd.to_numeric(col, errors="coerce").dropna()
        else:
            vals = pd.to_numeric(df, errors="coerce").dropna()
        if len(vals) < periods + 1:
            return None
        curr = float(vals.iloc[-1])
        prev = float(vals.iloc[-(periods + 1)])
        return (curr - prev) / abs(prev) * 100 if prev != 0 else None
    except Exception:
        return None


def generate_daily_wrap(
    stock_data: dict,
    freight_data: dict,
    macro_data: dict,
    port_results: list,
    insights: list,
) -> dict:
    """Generate a WSJ-style daily market wrap.

    Returns:
        headline: str — main headline
        subhead: str — deck/subheadline
        body: list[str] — paragraphs of editorial commentary
        key_movers: list[dict] — biggest movers with context
        market_tone: str — BULLISH/NEUTRAL/BEARISH
        timestamp: str
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %-d, %Y")

    # Gather market signals
    signals = {
        "stocks_up": 0,
        "stocks_down": 0,
        "stocks_total": 0,
        "biggest_gainer": None,
        "biggest_loser": None,
        "max_gain": -999,
        "max_loss": 999,
    }

    stock_movers = []
    for ticker, df in (stock_data or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
            continue
        close = df["close"].dropna()
        if len(close) < 2:
            continue
        curr = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        chg = (curr - prev) / abs(prev) * 100 if prev != 0 else 0

        signals["stocks_total"] += 1
        if chg > 0:
            signals["stocks_up"] += 1
        elif chg < 0:
            signals["stocks_down"] += 1

        stock_movers.append({"ticker": ticker, "price": curr, "change_pct": chg})

        if chg > signals["max_gain"]:
            signals["max_gain"] = chg
            signals["biggest_gainer"] = (ticker, curr, chg)
        if chg < signals["max_loss"]:
            signals["max_loss"] = chg
            signals["biggest_loser"] = (ticker, curr, chg)

    stock_movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    # Freight signals
    freight_changes = {}
    for route, data in (freight_data or {}).items():
        chg = _safe_pct_change(data, 1)
        if chg is not None:
            freight_changes[route] = chg

    avg_freight_chg = sum(freight_changes.values()) / len(freight_changes) if freight_changes else 0

    # Port demand
    port_scores = [getattr(p, "demand_score", 0.5) for p in (port_results or [])]
    avg_demand = sum(port_scores) / len(port_scores) if port_scores else 0.5

    # Insights
    n_insights = len(insights) if insights else 0
    hi_conv = sum(1 for i in (insights or []) if getattr(i, "score", 0) >= 0.70)

    # Macro
    vix = _safe_last(macro_data.get("VIXCLS")) if macro_data else None
    crude = _safe_last(macro_data.get("DCOILWTICO")) if macro_data else None
    dxy_chg = _safe_pct_change(macro_data.get("DEXCHUS"), 1) if macro_data else None

    # ── Determine market tone ────────────────────────────────────────────────
    bullish_signals = 0
    bearish_signals = 0

    if signals["stocks_up"] > signals["stocks_down"]:
        bullish_signals += 1
    else:
        bearish_signals += 1

    if avg_freight_chg > 0.5:
        bullish_signals += 1
    elif avg_freight_chg < -0.5:
        bearish_signals += 1

    if avg_demand > 0.6:
        bullish_signals += 1
    elif avg_demand < 0.45:
        bearish_signals += 1

    if hi_conv > 3:
        bullish_signals += 1

    if bullish_signals >= 3:
        tone = "BULLISH"
    elif bearish_signals >= 3:
        tone = "BEARISH"
    else:
        tone = "NEUTRAL"

    # ── Generate headline ────────────────────────────────────────────────────
    if tone == "BULLISH":
        if avg_freight_chg > 2:
            headline = "Freight Rates Surge as Global Shipping Demand Strengthens"
        elif signals["stocks_up"] > signals["stocks_down"] + 2:
            headline = "Shipping Equities Rally on Strong Volume and Rate Momentum"
        else:
            headline = "Shipping Markets Advance on Broad-Based Demand Signals"
    elif tone == "BEARISH":
        if avg_freight_chg < -2:
            headline = "Container Rates Slide as Overcapacity Pressures Mount"
        elif signals["stocks_down"] > signals["stocks_up"] + 2:
            headline = "Shipping Stocks Retreat Amid Softening Trade Expectations"
        else:
            headline = "Maritime Markets Pull Back on Mixed Economic Signals"
    else:
        headline = "Shipping Markets Trade Sideways as Mixed Signals Persist"

    # ── Generate subheadline ─────────────────────────────────────────────────
    subhead_parts = []
    if signals["biggest_gainer"]:
        t, p, c = signals["biggest_gainer"]
        subhead_parts.append(f"{t} leads gainers at +{c:.1f}%")
    if freight_changes:
        top_freight = max(freight_changes.items(), key=lambda x: abs(x[1]))
        sign = "+" if top_freight[1] > 0 else ""
        subhead_parts.append(f"{str(top_freight[0])[:15]} rates {sign}{top_freight[1]:.1f}%")
    if n_insights:
        subhead_parts.append(f"{hi_conv} high-conviction signals active")

    subhead = "; ".join(subhead_parts[:3]) if subhead_parts else "Markets await fresh catalysts"

    # ── Generate body paragraphs ─────────────────────────────────────────────
    body = []

    # Para 1: Equity overview
    if signals["stocks_total"] > 0:
        up_ratio = signals["stocks_up"] / signals["stocks_total"]
        if up_ratio > 0.7:
            p1 = (f"Shipping equities posted broad gains on {date_str}, "
                  f"with {signals['stocks_up']} of {signals['stocks_total']} tracked names closing higher. ")
        elif up_ratio < 0.3:
            p1 = (f"Selling pressure dominated shipping equities on {date_str}, "
                  f"as {signals['stocks_down']} of {signals['stocks_total']} tracked names declined. ")
        else:
            p1 = (f"Shipping equities traded mixed on {date_str}, "
                  f"with {signals['stocks_up']} advancing and {signals['stocks_down']} declining "
                  f"among {signals['stocks_total']} tracked names. ")

        if signals["biggest_gainer"]:
            t, p, c = signals["biggest_gainer"]
            p1 += f"{t} led the sector, gaining {c:.1f}% to ${p:.2f}. "
        if signals["biggest_loser"]:
            t, p, c = signals["biggest_loser"]
            p1 += f"{t} was the notable laggard, falling {abs(c):.1f}% to ${p:.2f}."

        body.append(p1.strip())

    # Para 2: Freight market context
    if freight_changes:
        rising = sum(1 for v in freight_changes.values() if v > 0.3)
        falling = sum(1 for v in freight_changes.values() if v < -0.3)
        total = len(freight_changes)

        if rising > falling:
            p2 = (f"Freight rates firmed across most tracked corridors, "
                  f"with {rising} of {total} routes showing positive momentum. ")
        elif falling > rising:
            p2 = (f"Freight rates softened on the day, "
                  f"with {falling} of {total} routes registering declines. ")
        else:
            p2 = f"Freight rates were largely unchanged across major trade lanes. "

        if avg_demand >= 0.6:
            p2 += (f"Port demand remains elevated at {avg_demand:.0%} across tracked facilities, "
                   f"supporting the near-term rate environment.")
        elif avg_demand < 0.45:
            p2 += (f"Subdued port demand of {avg_demand:.0%} continues to weigh on rate momentum, "
                   f"with overcapacity concerns lingering in key corridors.")
        else:
            p2 += (f"Port demand at {avg_demand:.0%} reflects mixed conditions, "
                   f"with selective strength in high-volume Asian hubs.")

        body.append(p2.strip())

    # Para 3: Macro context
    macro_parts = []
    if vix is not None:
        if vix > 25:
            macro_parts.append(f"volatility remains elevated with the VIX at {vix:.1f}")
        elif vix < 15:
            macro_parts.append(f"market volatility is subdued with the VIX at {vix:.1f}")
        else:
            macro_parts.append(f"the VIX sits at {vix:.1f}")

    if crude is not None:
        macro_parts.append(f"WTI crude at ${crude:.2f}/barrel")

    if macro_parts:
        p3 = (f"On the macro front, {', '.join(macro_parts)}. ")
        if n_insights > 0:
            p3 += (f"The platform's intelligence engine is tracking {n_insights} active signals, "
                   f"of which {hi_conv} carry high-conviction ratings.")
        body.append(p3.strip())

    # ── Key movers ───────────────────────────────────────────────────────────
    key_movers = []
    for m in stock_movers[:5]:
        direction = "gained" if m["change_pct"] > 0 else "declined"
        key_movers.append({
            "ticker": m["ticker"],
            "price": m["price"],
            "change_pct": m["change_pct"],
            "direction": direction,
            "summary": f'{m["ticker"]} {direction} {abs(m["change_pct"]):.1f}% to ${m["price"]:.2f}',
        })

    return {
        "headline": headline,
        "subhead": subhead,
        "body": body,
        "key_movers": key_movers,
        "market_tone": tone,
        "date": date_str,
        "timestamp": now.isoformat(),
        "stats": {
            "stocks_up": signals["stocks_up"],
            "stocks_down": signals["stocks_down"],
            "avg_freight_chg": avg_freight_chg,
            "avg_demand": avg_demand,
            "n_insights": n_insights,
            "hi_conv": hi_conv,
        },
    }


def generate_forward_outlook(
    insights: list,
    macro_data: dict,
    freight_data: dict,
) -> dict:
    """Generate forward-looking risk/opportunity assessment.

    Returns dict with opportunities, risks, and editorial narrative.
    """
    opportunities = []
    risks = []

    if insights:
        bullish = [i for i in insights if getattr(i, "action", "") in ("Prioritize", "Monitor")
                   and getattr(i, "score", 0) >= 0.65]
        bearish = [i for i in insights if getattr(i, "action", "") in ("Caution", "Avoid")]

        for ins in bullish[:3]:
            opportunities.append({
                "title": getattr(ins, "title", ""),
                "score": getattr(ins, "score", 0),
                "category": getattr(ins, "category", ""),
            })
        for ins in bearish[:3]:
            risks.append({
                "title": getattr(ins, "title", ""),
                "score": getattr(ins, "score", 0),
                "category": getattr(ins, "category", ""),
            })

    # Macro risks
    vix = _safe_last(macro_data.get("VIXCLS")) if macro_data else None
    if vix is not None and vix > 25:
        risks.append({
            "title": f"Elevated market volatility (VIX: {vix:.1f})",
            "score": 0.7,
            "category": "MACRO",
        })

    yield_spread = _safe_last(macro_data.get("T10Y2Y")) if macro_data else None
    if yield_spread is not None and yield_spread < 0:
        risks.append({
            "title": f"Inverted yield curve ({yield_spread:.2f}%) signals recession risk",
            "score": 0.8,
            "category": "MACRO",
        })

    # Narrative
    parts = []
    if opportunities:
        parts.append(
            f"Looking ahead, {len(opportunities)} high-conviction opportunities have been identified, "
            f"led by signals in the {opportunities[0]['category'].lower().replace('_', ' ')} space. "
        )
    if risks:
        risk_cats = set(r["category"] for r in risks)
        parts.append(
            f"Key risks center on {', '.join(c.lower().replace('_', ' ') for c in risk_cats)} factors, "
            f"with {len(risks)} active caution signals warranting close monitoring."
        )
    if not parts:
        parts.append("Forward outlook data is currently limited. Monitor for developing signals.")

    return {
        "opportunities": opportunities,
        "risks": risks,
        "narrative": " ".join(parts),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
