"""company_profiler.py — Shipping company profiles with fundamental analysis.

Generates WSJ-style company profiles combining:
  1. Price performance and technicals
  2. Fundamental metrics (P/E, P/B, dividend yield)
  3. Fleet and operational data
  4. Peer comparison
  5. Analyst-style commentary
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger


# ── Company master data ──────────────────────────────────────────────────────
COMPANY_PROFILES = {
    "ZIM": {
        "name": "ZIM Integrated Shipping Services",
        "hq": "Haifa, Israel",
        "sector": "Container",
        "fleet_size": 147,
        "fleet_teu_capacity": 668000,
        "founded": 1945,
        "employees": 4800,
        "key_routes": ["Trans-Pacific", "Asia-USEC", "Intra-Asia"],
        "competitive_edge": "Asset-light model with high charter flexibility; niche cross-Pacific focus",
        "risk_factor": "High earnings cyclicality; concentrated route exposure",
    },
    "MATX": {
        "name": "Matson Inc",
        "hq": "Honolulu, Hawaii",
        "sector": "Container/Jones Act",
        "fleet_size": 26,
        "fleet_teu_capacity": 72000,
        "founded": 1882,
        "employees": 4200,
        "key_routes": ["Hawaii", "Alaska", "Guam", "China-USWC (CLX)"],
        "competitive_edge": "Jones Act monopoly on Hawaii/Alaska; premium China express service",
        "risk_factor": "Regulatory risk on Jones Act; limited international diversification",
    },
    "SBLK": {
        "name": "Star Bulk Carriers",
        "hq": "Athens, Greece",
        "sector": "Dry Bulk",
        "fleet_size": 128,
        "fleet_teu_capacity": 0,
        "dwt_capacity": 14200000,
        "founded": 2006,
        "employees": 2100,
        "key_routes": ["Capesize iron ore", "Panamax grain", "Supramax coal"],
        "competitive_edge": "Largest US-listed dry bulk fleet; scale advantages in chartering",
        "risk_factor": "Pure play on dry bulk cycle; BDI correlation risk",
    },
    "DAC": {
        "name": "Danaos Corporation",
        "hq": "Athens, Greece",
        "sector": "Container Leasing",
        "fleet_size": 71,
        "fleet_teu_capacity": 431000,
        "founded": 1972,
        "employees": 800,
        "key_routes": ["Global charter to major lines"],
        "competitive_edge": "Long-term charter backlog provides revenue visibility; low leverage",
        "risk_factor": "Counterparty concentration with major liner companies",
    },
    "CMRE": {
        "name": "Costamare Inc",
        "hq": "Athens, Greece",
        "sector": "Container/Dry Bulk",
        "fleet_size": 112,
        "fleet_teu_capacity": 490000,
        "founded": 1975,
        "employees": 600,
        "key_routes": ["Global charter (container + dry bulk)"],
        "competitive_edge": "Diversified across container and bulk; 50-year operating track record",
        "risk_factor": "Charter rate renewal risk; aging fleet segments",
    },
    "GOGL": {
        "name": "Golden Ocean Group",
        "hq": "Hamilton, Bermuda",
        "sector": "Dry Bulk",
        "fleet_size": 83,
        "fleet_teu_capacity": 0,
        "dwt_capacity": 12400000,
        "founded": 2004,
        "employees": 350,
        "key_routes": ["Capesize iron ore", "Panamax grain"],
        "competitive_edge": "Modern Capesize-heavy fleet; Fredriksen empire backing",
        "risk_factor": "Heavily exposed to iron ore/steel cycle and Chinese demand",
    },
    "DSX": {
        "name": "Diana Shipping",
        "hq": "Athens, Greece",
        "sector": "Dry Bulk",
        "fleet_size": 36,
        "fleet_teu_capacity": 0,
        "dwt_capacity": 4300000,
        "founded": 1999,
        "employees": 250,
        "key_routes": ["Panamax/Kamsarmax grain", "Capesize coal"],
        "competitive_edge": "Conservative chartering strategy; moderate leverage",
        "risk_factor": "Smaller fleet limits negotiating power; Greek tax exposure",
    },
}


# ─── R047: REAL Alpha Vantage fundamentals (cache-only, offline-safe) ────────
# A single localized block (this helper + one call site in
# compute_company_profiles). It NEVER triggers a live AV fetch on the hot path:
# it serves the cached OVERVIEW/INCOME only when one is already on disk, so a
# profile render can't blow Alpha Vantage's 25-requests/day free-tier quota.
# When the cache is dark (no key / never fetched) it returns {} and the profile
# keeps its hardcoded/assumed values. Every value it DOES return is real, and is
# flagged via the `*_provenance` keys so the UI can tell measured from assumed.

# Alpha Vantage OVERVIEW is cached for 24h; serve a vintage up to this old.
_AV_PROFILE_CACHE_TTL_H: float = 24.0


def _av_fundamentals(ticker: str) -> dict:
    """Return REAL Alpha Vantage fundamentals for *ticker*, CACHE-ONLY.

    Reads the already-cached OVERVIEW (and INCOME) for *ticker* and returns a
    small dict of measured figures (market cap, P/E, dividend yield, beta,
    EBITDA, YoY growth, price). Returns ``{}`` when nothing is cached, when no
    API key is configured, or on any error — so the caller falls back to the
    hardcoded/assumed profile. NEVER forces a live fetch (offline-safe).
    """
    try:
        from data.alphavantage_feed import (
            alphavantage_available,
            fetch_fundamentals,
            fetch_income,
        )
        from data.cache_manager import CacheManager

        if not alphavantage_available():
            return {}

        cache = CacheManager()
        out: dict = {}

        # OVERVIEW — only read when a fresh cache entry already exists. Passing
        # the same TTL fetch_fundamentals uses means is_cached() agreeing → the
        # subsequent fetch is a guaranteed cache hit, never a network call.
        ov_key = f"alphavantage_{ticker}_OVERVIEW"
        if cache.is_cached(ov_key, source="alphavantage", ttl_hours=_AV_PROFILE_CACHE_TTL_H):
            fund = fetch_fundamentals(ticker, cache_ttl_hours=_AV_PROFILE_CACHE_TTL_H, cache=cache)
            if fund is not None:
                if fund.market_cap > 0:
                    out["market_cap_bn"] = float(fund.market_cap)
                if fund.pe_ratio > 0:
                    out["pe_ratio"] = float(fund.pe_ratio)
                if fund.dividend_yield_pct > 0:
                    out["dividend_yield_pct"] = float(fund.dividend_yield_pct)
                if fund.beta != 0:
                    out["beta"] = float(fund.beta)
                if fund.revenue_growth_yoy_pct != 0:
                    out["revenue_growth_yoy_pct"] = float(fund.revenue_growth_yoy_pct)

        # INCOME — same cache-only discipline for the EBITDA figure.
        inc_key = f"alphavantage_{ticker}_INCOME_STATEMENT"
        if cache.is_cached(inc_key, source="alphavantage", ttl_hours=_AV_PROFILE_CACHE_TTL_H):
            income = fetch_income(ticker, cache_ttl_hours=_AV_PROFILE_CACHE_TTL_H, cache=cache)
            if income is not None and income.ebitda > 0:
                out["ebitda"] = float(income.ebitda)

        return out
    except Exception as exc:  # noqa: BLE001 — a feed/cache hiccup must never break a profile
        logger.debug("AV fundamentals unavailable for %s (using assumed): %s", ticker, exc)
        return {}


def compute_company_profiles(stock_data: dict) -> list[dict]:
    """Compute detailed profiles for all tracked shipping companies.

    Combines master data with live stock performance.
    """
    profiles = []

    for ticker, master in COMPANY_PROFILES.items():
        profile = {
            "ticker": ticker,
            **master,
        }

        # ─── R047: overlay REAL AV fundamentals when cached (else assumed) ───
        # Self-contained block: reads cache-only AV figures and stamps each one
        # 'real'. Keys absent from `av` keep the hardcoded/assumed profile and
        # are flagged 'assumed' via fundamentals_provenance. No live fetch.
        av = _av_fundamentals(ticker)
        fund_prov: dict[str, str] = {}
        for _field in ("market_cap_bn", "pe_ratio", "dividend_yield_pct",
                       "beta", "ebitda", "revenue_growth_yoy_pct"):
            if _field in av:
                profile[_field] = av[_field]
                fund_prov[_field] = "real"
            else:
                fund_prov[_field] = "assumed"
        profile["fundamentals_provenance"] = fund_prov
        profile["has_real_fundamentals"] = any(v == "real" for v in fund_prov.values())

        df = stock_data.get(ticker)
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty and "close" in df.columns:
            close = df["close"].dropna()
            if len(close) >= 2:
                current = float(close.iloc[-1])

                # %-CHANGE displays ride the look-ahead-free TOTAL-RETURN basis
                # (close * adj_factor, R127) so a split/large dividend in the
                # window doesn't show a fake ~-50%/+100% move; the price LEVEL,
                # 52w high/low, range_position + MAs below stay on the RAW close
                # (the real share price). adj_factor defaults to 1.0 → fixtures
                # unchanged.
                from data.normalizer import adjusted_close
                adj = adjusted_close(df).dropna()
                cur_adj = float(adj.iloc[-1])
                prev_1d_adj = float(adj.iloc[-2])
                chg_1d = (cur_adj - prev_1d_adj) / abs(prev_1d_adj) * 100 if prev_1d_adj != 0 else 0

                profile["price"] = current
                profile["change_1d"] = chg_1d

                if len(adj) >= 6:
                    prev_5d_adj = float(adj.iloc[-6])
                    profile["change_5d"] = (cur_adj - prev_5d_adj) / abs(prev_5d_adj) * 100 if prev_5d_adj != 0 else 0

                if len(adj) >= 31:
                    prev_30d_adj = float(adj.iloc[-31])
                    profile["change_30d"] = (cur_adj - prev_30d_adj) / abs(prev_30d_adj) * 100 if prev_30d_adj != 0 else 0

                # 52-week range
                recent = close.iloc[-min(252, len(close)):]
                profile["high_52w"] = float(recent.max())
                profile["low_52w"] = float(recent.min())
                h = profile["high_52w"]
                l = profile["low_52w"]
                profile["range_position"] = (current - l) / (h - l) * 100 if h != l else 50

                # Volatility — look-ahead-free total return (R127); display
                # metrics above stay on the raw price.
                from data.normalizer import adjusted_close
                returns = adjusted_close(df).dropna().pct_change().dropna()
                if len(returns) > 5:
                    profile["volatility_30d"] = float(returns.iloc[-30:].std() * np.sqrt(252) * 100) if len(returns) >= 30 else None

                # Moving averages
                if len(close) >= 50:
                    profile["ma_50"] = float(close.iloc[-50:].mean())
                    profile["above_ma_50"] = current > profile["ma_50"]
                if len(close) >= 200:
                    profile["ma_200"] = float(close.iloc[-200:].mean())
                    profile["above_ma_200"] = current > profile["ma_200"]

                # Technical signal
                above_50 = profile.get("above_ma_50", True)
                above_200 = profile.get("above_ma_200", True)
                chg_30 = profile.get("change_30d", 0)

                if above_50 and above_200 and chg_30 > 5:
                    profile["technical_signal"] = "Strong Buy"
                elif above_50 and chg_30 > 0:
                    profile["technical_signal"] = "Buy"
                elif not above_50 and not above_200 and chg_30 < -5:
                    profile["technical_signal"] = "Sell"
                elif not above_50:
                    profile["technical_signal"] = "Weak"
                else:
                    profile["technical_signal"] = "Hold"

        # Generate commentary
        profile["commentary"] = _generate_commentary(profile)

        profiles.append(profile)

    return profiles


def _generate_commentary(profile: dict) -> str:
    """Generate WSJ-style editorial commentary for a company."""
    parts = []
    ticker = profile["ticker"]
    name = profile["name"]
    sector = profile.get("sector", "shipping")

    price = profile.get("price")
    chg_30 = profile.get("change_30d")
    range_pos = profile.get("range_position")
    tech = profile.get("technical_signal", "Hold")

    if price:
        if chg_30 is not None:
            if chg_30 > 10:
                parts.append(
                    f"{name} ({ticker}) has surged {chg_30:.1f}% over the past month, "
                    f"reflecting strong {sector.lower()} market conditions."
                )
            elif chg_30 > 0:
                parts.append(
                    f"Shares of {name} ({ticker}) have edged higher by {chg_30:.1f}% "
                    f"over the past 30 sessions."
                )
            elif chg_30 > -10:
                parts.append(
                    f"{name} ({ticker}) has pulled back {abs(chg_30):.1f}% this month, "
                    f"underperforming amid softer {sector.lower()} sentiment."
                )
            else:
                parts.append(
                    f"Shares of {name} ({ticker}) are under significant pressure, "
                    f"declining {abs(chg_30):.1f}% over the past month."
                )

        if range_pos is not None:
            if range_pos > 80:
                parts.append(f"The stock trades near its 52-week high, in the {range_pos:.0f}th percentile of its range.")
            elif range_pos < 20:
                parts.append(f"At the {range_pos:.0f}th percentile of its 52-week range, valuation compression may present opportunity.")

    edge = profile.get("competitive_edge", "")
    risk = profile.get("risk_factor", "")
    if edge:
        parts.append(f"Key advantage: {edge}.")
    if risk:
        parts.append(f"Primary risk: {risk}.")

    return " ".join(parts) if parts else f"{name} profile data unavailable."
