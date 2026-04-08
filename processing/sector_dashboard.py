"""Shipping sector dashboard — comparative analysis across shipping sub-sectors.

Computes relative performance, valuation metrics, and analyst consensus
views for container, dry bulk, tanker, and LNG shipping segments.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from loguru import logger


# ── Sector definitions ───────────────────────────────────────────────────────
SECTORS = {
    "Container": {
        "tickers": ["ZIM", "MATX", "DAC", "CMRE"],
        "index": "SCFI",
        "description": "Container shipping lines and lessors",
        "key_driver": "Global consumer goods trade, e-commerce volumes",
    },
    "Dry Bulk": {
        "tickers": ["SBLK", "GOGL", "DSX"],
        "index": "BDI",
        "description": "Capesize, Panamax, and Supramax carriers",
        "key_driver": "Iron ore, coal, grain trade flows",
    },
    "Tanker": {
        "tickers": [],
        "index": "VLCC",
        "description": "Crude and product tanker operators",
        "key_driver": "OPEC production, refinery utilization, geopolitics",
    },
    "LNG/LPG": {
        "tickers": [],
        "index": "LNG",
        "description": "Liquefied gas carriers",
        "key_driver": "Energy transition, Asia LNG demand, European diversification",
    },
}


def _safe_pct(curr: float, prev: float) -> float | None:
    if prev == 0 or curr is None or prev is None:
        return None
    return (curr - prev) / abs(prev) * 100


def compute_sector_performance(stock_data: dict, freight_data: dict) -> list[dict]:
    """Compute sector-level performance metrics.

    Returns a list of sector dicts with performance, valuation, and momentum data.
    """
    results = []

    for sector_name, cfg in SECTORS.items():
        sector = {
            "name": sector_name,
            "description": cfg["description"],
            "key_driver": cfg["key_driver"],
            "index_name": cfg["index"],
            "tickers": cfg["tickers"],
        }

        # Compute average performance across sector tickers
        returns_1d = []
        returns_5d = []
        returns_30d = []
        prices = []

        for ticker in cfg["tickers"]:
            df = stock_data.get(ticker)
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            if "close" not in df.columns:
                continue

            close = df["close"].dropna()
            if len(close) < 2:
                continue

            curr = float(close.iloc[-1])
            prices.append({"ticker": ticker, "price": curr})

            prev_1d = float(close.iloc[-2]) if len(close) >= 2 else curr
            ret_1d = _safe_pct(curr, prev_1d)
            if ret_1d is not None:
                returns_1d.append(ret_1d)

            prev_5d = float(close.iloc[-6]) if len(close) >= 6 else curr
            ret_5d = _safe_pct(curr, prev_5d)
            if ret_5d is not None:
                returns_5d.append(ret_5d)

            prev_30d = float(close.iloc[-31]) if len(close) >= 31 else curr
            ret_30d = _safe_pct(curr, prev_30d)
            if ret_30d is not None:
                returns_30d.append(ret_30d)

        sector["stock_prices"] = prices
        sector["avg_return_1d"] = sum(returns_1d) / len(returns_1d) if returns_1d else None
        sector["avg_return_5d"] = sum(returns_5d) / len(returns_5d) if returns_5d else None
        sector["avg_return_30d"] = sum(returns_30d) / len(returns_30d) if returns_30d else None

        # Freight index performance
        idx_key = cfg["index"]
        freight_df = freight_data.get(idx_key)
        if freight_df is not None and isinstance(freight_df, pd.DataFrame) and not freight_df.empty:
            col = freight_df.columns[0]
            vals = freight_df[col].dropna()
            if len(vals) >= 2:
                sector["index_current"] = float(vals.iloc[-1])
                sector["index_prev"] = float(vals.iloc[-2])
                sector["index_chg_1d"] = _safe_pct(float(vals.iloc[-1]), float(vals.iloc[-2]))
            if len(vals) >= 31:
                sector["index_chg_30d"] = _safe_pct(float(vals.iloc[-1]), float(vals.iloc[-31]))
        else:
            sector["index_current"] = None
            sector["index_prev"] = None
            sector["index_chg_1d"] = None
            sector["index_chg_30d"] = None

        # Momentum signal
        ret_30 = sector.get("avg_return_30d")
        idx_30 = sector.get("index_chg_30d")
        if ret_30 is not None and ret_30 > 5:
            sector["momentum"] = "Strong"
        elif ret_30 is not None and ret_30 > 0:
            sector["momentum"] = "Positive"
        elif ret_30 is not None and ret_30 > -5:
            sector["momentum"] = "Weak"
        elif ret_30 is not None:
            sector["momentum"] = "Negative"
        else:
            sector["momentum"] = "N/A"

        # Sector outlook based on combined signals
        signals = []
        if ret_30 is not None:
            signals.append(ret_30)
        if idx_30 is not None:
            signals.append(idx_30)
        if signals:
            avg_signal = sum(signals) / len(signals)
            if avg_signal > 5:
                sector["outlook"] = "Bullish"
            elif avg_signal > 0:
                sector["outlook"] = "Neutral-Positive"
            elif avg_signal > -5:
                sector["outlook"] = "Neutral-Negative"
            else:
                sector["outlook"] = "Bearish"
        else:
            sector["outlook"] = "Insufficient Data"

        results.append(sector)

    return results


def compute_trade_flow_summary(trade_data: dict, port_results: list) -> dict:
    """Compute global trade flow summary with regional breakdown.

    Returns dict with regional volumes, growth rates, and narrative.
    """
    regions = {
        "Asia-Pacific": {"ports": [], "total_trade": 0, "export_pct": 0, "import_pct": 0},
        "Europe": {"ports": [], "total_trade": 0, "export_pct": 0, "import_pct": 0},
        "North America": {"ports": [], "total_trade": 0, "export_pct": 0, "import_pct": 0},
        "Middle East": {"ports": [], "total_trade": 0, "export_pct": 0, "import_pct": 0},
        "Other": {"ports": [], "total_trade": 0, "export_pct": 0, "import_pct": 0},
    }

    if not port_results:
        return {
            "regions": regions,
            "total_global_trade": 0,
            "dominant_region": "N/A",
            "narrative": "Insufficient data to compute trade flow summary.",
        }

    for pr in port_results:
        region = getattr(pr, "region", "Other") or "Other"
        # Map to broader regions
        if "Asia" in region or "Pacific" in region:
            r_key = "Asia-Pacific"
        elif "Europe" in region:
            r_key = "Europe"
        elif "America" in region:
            r_key = "North America"
        elif "Middle" in region or "Gulf" in region:
            r_key = "Middle East"
        else:
            r_key = "Other"

        port_name = getattr(pr, "port_name", "Unknown")
        export_val = getattr(pr, "export_value_usd", 0) or 0
        import_val = getattr(pr, "import_value_usd", 0) or 0
        throughput = getattr(pr, "throughput_teu_m", 0) or 0
        demand_score = getattr(pr, "demand_score", 0.5)

        regions[r_key]["ports"].append({
            "name": port_name,
            "demand_score": demand_score,
            "throughput_teu_m": throughput,
        })
        regions[r_key]["total_trade"] += export_val + import_val

    total_global = sum(r["total_trade"] for r in regions.values())
    for r in regions.values():
        if total_global > 0:
            r["share_pct"] = r["total_trade"] / total_global * 100
        else:
            r["share_pct"] = 0

    dominant = max(regions.items(), key=lambda x: x[1]["total_trade"])

    # Generate narrative
    active_regions = [(k, v) for k, v in regions.items() if v["ports"]]
    active_regions.sort(key=lambda x: x[1]["total_trade"], reverse=True)

    parts = []
    if active_regions:
        top = active_regions[0]
        parts.append(
            f"{top[0]} dominates global trade flows with {top[1]['share_pct']:.0f}% share "
            f"across {len(top[1]['ports'])} tracked ports."
        )
        if len(active_regions) > 1:
            second = active_regions[1]
            parts.append(
                f"{second[0]} follows at {second[1]['share_pct']:.0f}%, "
                f"tracking {len(second[1]['ports'])} ports."
            )

        # Demand context
        all_scores = [getattr(pr, "demand_score", 0.5) for pr in port_results]
        avg_demand = sum(all_scores) / len(all_scores) if all_scores else 0.5
        if avg_demand >= 0.65:
            parts.append("Overall port demand is elevated, suggesting robust trade volumes.")
        elif avg_demand >= 0.45:
            parts.append("Port demand is mixed, with selective strength in key corridors.")
        else:
            parts.append("Port demand remains subdued across most regions.")

    return {
        "regions": regions,
        "total_global_trade": total_global,
        "dominant_region": dominant[0] if dominant else "N/A",
        "narrative": " ".join(parts) if parts else "Trade flow data unavailable.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
