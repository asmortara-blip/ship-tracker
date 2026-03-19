from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger


@dataclass
class CommodityShippingSignal:
    commodity_ticker: str
    commodity_name: str
    current_price: float
    price_change_30d: float
    direction: str
    shipping_hypothesis: str
    affected_routes: list[str]
    affected_stocks: list[str]
    signal_strength: float
    trade_idea: str


COMMODITY_SHIPPING_MAP: dict[str, dict] = {
    "DBA": {
        "name": "Agriculture Fund",
        "hypothesis": "Rising ag prices signal strong agricultural export demand. Bullish for grain/soy routes from South America and US Gulf to Asia.",
        "bullish_routes": ["china_south_america", "europe_south_america", "us_east_south_america"],
        "bullish_stocks": ["SBLK"],
        "bearish_stocks": [],
        "signal_direction_if_rising": "bullish",
    },
    "DBB": {
        "name": "Base Metals Fund",
        "hypothesis": "Base metal price surges indicate global manufacturing expansion, driving container and bulk demand on major trade lanes.",
        "bullish_routes": ["transpacific_eb", "asia_europe", "ningbo_europe"],
        "bullish_stocks": ["ZIM", "MATX", "DAC"],
        "bearish_stocks": [],
        "signal_direction_if_rising": "bullish",
    },
    "USO": {
        "name": "Oil Fund",
        "hypothesis": "High oil prices increase bunker fuel costs ~20-30% of shipping OpEx. Near-term bearish for shipping margins; long-term may reflect strong global demand.",
        "bullish_routes": [],
        "bullish_stocks": [],
        "bearish_stocks": ["ZIM", "MATX", "CMRE"],
        "signal_direction_if_rising": "bearish",
    },
    "XLB": {
        "name": "Materials Sector",
        "hypothesis": "Materials sector strength signals construction and industrial demand. Bullish for port throughput and container trade from manufacturing hubs.",
        "bullish_routes": ["transpacific_eb", "intra_asia_china_sea", "asia_europe"],
        "bullish_stocks": ["SBLK", "DAC", "CMRE"],
        "bearish_stocks": [],
        "signal_direction_if_rising": "bullish",
    },
}


def analyze_commodity_signals(stock_data: dict) -> list[CommodityShippingSignal]:
    signals = []
    for ticker, meta in COMMODITY_SHIPPING_MAP.items():
        df = stock_data.get(ticker)
        if df is None or df.empty or "close" not in df.columns:
            continue
        try:
            close = df["close"].dropna()
            if len(close) < 2:
                continue
            current_price = float(close.iloc[-1])
            price_30d_ago = float(close.iloc[-31]) if len(close) >= 31 else float(close.iloc[0])
            change_30d = (current_price - price_30d_ago) / price_30d_ago if price_30d_ago != 0 else 0.0

            rises_bullish = meta["signal_direction_if_rising"] == "bullish"
            if rises_bullish:
                direction = "bullish" if change_30d > 0.03 else ("bearish" if change_30d < -0.03 else "neutral")
            else:
                direction = "bearish" if change_30d > 0.03 else ("bullish" if change_30d < -0.03 else "neutral")

            signal_strength = min(1.0, abs(change_30d) / 0.15)

            if direction == "bullish":
                trade_idea = (
                    f"{ticker} up {change_30d:.1%} in 30d — {meta['hypothesis']} "
                    f"Consider long exposure on: {', '.join(meta.get('bullish_stocks', []))}"
                )
            elif direction == "bearish":
                trade_idea = (
                    f"{ticker} down {abs(change_30d):.1%} in 30d — {meta['hypothesis']} "
                    f"Watch margin compression on: {', '.join(meta.get('bearish_stocks', []))}"
                )
            else:
                trade_idea = f"{ticker} flat ({change_30d:+.1%}) — No clear directional signal. Monitor for breakout."

            signals.append(CommodityShippingSignal(
                commodity_ticker=ticker,
                commodity_name=meta["name"],
                current_price=current_price,
                price_change_30d=change_30d,
                direction=direction,
                shipping_hypothesis=meta["hypothesis"],
                affected_routes=meta.get("bullish_routes", []),
                affected_stocks=meta.get("bullish_stocks", []) + meta.get("bearish_stocks", []),
                signal_strength=signal_strength,
                trade_idea=trade_idea,
            ))
        except Exception as exc:
            logger.debug(f"Commodity signal failed for {ticker}: {exc}")
    return signals


def get_commodity_consensus(signals: list[CommodityShippingSignal]) -> dict:
    bullish = sum(1 for s in signals if s.direction == "bullish")
    bearish = sum(1 for s in signals if s.direction == "bearish")
    neutral = sum(1 for s in signals if s.direction == "neutral")
    total = len(signals) or 1
    if bullish > bearish:
        net = "BULLISH"
    elif bearish > bullish:
        net = "BEARISH"
    else:
        net = "NEUTRAL"
    strongest = max(signals, key=lambda s: s.signal_strength) if signals else None
    return {
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "net_signal": net,
        "strongest_signal": strongest,
    }
