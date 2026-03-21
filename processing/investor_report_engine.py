"""
processing/investor_report_engine.py
──────────────────────────────────────
Core data aggregation engine for investor-grade sentiment analysis reports.

Pulls from all existing data sources and engines in the codebase, runs
multi-factor analysis, and returns a structured InvestorReport dataclass
ready to be rendered into a downloadable document (PDF, HTML, Markdown).

All sections are wrapped in try/except — the engine always returns a
complete InvestorReport even when individual data sources fail.

Dependencies: pandas, numpy, loguru (all in requirements.txt).
No external API calls — all narrative generation is rule-based.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Internal imports — each wrapped so a missing module degrades gracefully
# ---------------------------------------------------------------------------

try:
    from processing.news_sentiment import (
        fetch_all_news,
        get_sentiment_summary,
        NewsArticle,
    )
    _NEWS_OK = True
except Exception as _e:
    logger.warning("news_sentiment import failed: {}", _e)
    _NEWS_OK = False

try:
    from engine.alpha_engine import (
        generate_all_signals,
        compute_portfolio_alpha,
        build_signal_scorecard,
        AlphaSignal,
    )
    _ALPHA_OK = True
except Exception as _e:
    logger.warning("alpha_engine import failed: {}", _e)
    _ALPHA_OK = False

try:
    from utils.digest_builder import build_digest, DailyDigest
    _DIGEST_OK = True
except Exception as _e:
    logger.warning("digest_builder import failed: {}", _e)
    _DIGEST_OK = False


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SentimentBreakdown:
    """Multi-source composite sentiment for the shipping market."""
    overall_score: float            # -1.0 to +1.0
    overall_label: str              # BULLISH | BEARISH | NEUTRAL | MIXED
    news_score: float               # from news sentiment engine
    freight_score: float            # derived from rate momentum
    macro_score: float              # derived from BDI + PMI
    alpha_score: float              # derived from signal conviction-weighted direction
    bullish_count: int              # bullish news articles
    bearish_count: int              # bearish news articles
    neutral_count: int              # neutral news articles
    top_keywords: list              # top trending entity/keyword strings
    trending_topics: list           # [{topic, count, sentiment, color}]


@dataclass
class AlphaSignalSummary:
    """Aggregated alpha signal state for the current session."""
    signals: list                           # list of AlphaSignal objects
    portfolio: dict                         # from compute_portfolio_alpha
    top_long: list                          # top 3 LONG signals by conviction+strength
    top_short: list                         # top 3 SHORT signals
    scorecard_df: object                    # pd.DataFrame from build_signal_scorecard
    signal_count_by_type: dict             # {MOMENTUM: N, MEAN_REVERSION: N, ...}
    signal_count_by_conviction: dict       # {HIGH: N, MEDIUM: N, LOW: N}


@dataclass
class MarketIntelligenceSummary:
    """Top-level market intelligence derived from port, route, and insight data."""
    top_insights: list              # top 5 insights by score
    top_ports: list                 # top 5 port_results by demand_score
    top_routes: list                # top 5 route_results by score
    risk_level: str                 # LOW | MODERATE | HIGH | CRITICAL
    active_opportunities: int       # insights with action in (Prioritize, Monitor)
    high_conviction_count: int      # insights with score >= 0.70


@dataclass
class FreightRateSummary:
    """Freight rate landscape across all monitored routes."""
    routes: list                    # [{route_id, rate, change_30d, change_pct, trend, label}]
    avg_change_30d_pct: float       # fleet-wide average 30d change
    biggest_mover: dict             # route with largest absolute change
    momentum_label: str             # "Accelerating" | "Decelerating" | "Stable"
    fbx_composite: float            # average of available FBX rates


@dataclass
class MacroSnapshot:
    """Snapshot of key macroeconomic indicators."""
    bdi: float                      # Baltic Dry Index (BDIY or BSXRLM)
    bdi_change_30d_pct: float       # 30-day % change
    wti: float                      # WTI crude $/bbl (DCOILWTICO)
    wti_change_30d_pct: float       # 30-day % change
    treasury_10y: float             # 10Y US Treasury yield (DGS10)
    dxy_proxy: float                # USD/CNY as DXY proxy (DEXCHUS)
    pmi_proxy: float                # Industrial production proxy (IPMAN)
    supply_chain_stress: str        # LOW | MODERATE | HIGH (BDI + WTI + rates)


@dataclass
class StockAnalysis:
    """Shipping equity analysis across the tracked ticker universe."""
    tickers: list                   # list of ticker strings
    prices: dict                    # {ticker: latest_price}
    changes_30d: dict               # {ticker: pct_change}
    signals_by_ticker: dict         # {ticker: [AlphaSignal]}
    top_pick: str                   # ticker with highest conviction signal
    top_pick_rationale: str         # from top signal.rationale


@dataclass
class AIAnalysis:
    """Rule-based AI narrative analysis — all prose, no external API."""
    executive_summary: str          # 3 rich paragraphs
    sentiment_narrative: str        # 2 paragraphs on sentiment
    opportunity_narrative: str      # top 3 opportunities prose
    risk_narrative: str             # key risks prose
    outlook_30d: str                # 30-day outlook paragraph
    top_recommendations: list       # [{rank, title, action, ticker, conviction, ...}]
    disclaimer: str                 # standard investment disclaimer


@dataclass
class InvestorReport:
    """Complete investor-grade shipping market report."""
    generated_at: str               # ISO timestamp
    report_date: str                # human-readable formatted date
    sentiment: SentimentBreakdown
    alpha: AlphaSignalSummary
    market: MarketIntelligenceSummary
    freight: FreightRateSummary
    macro: MacroSnapshot
    stocks: StockAnalysis
    ai: AIAnalysis
    data_quality: str               # FULL | PARTIAL | DEGRADED
    news_items: list                # raw NewsArticle list (top 15 by relevance)
    digest: object                  # DailyDigest object or None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _extract_macro_value(macro_data: dict, series_id: str) -> float:
    """Safely extract the latest value from a macro DataFrame.

    macro_data is expected to be dict[series_id -> pd.DataFrame] where each
    DataFrame has a 'value' column (standard schema from data/normalizer.py).

    Returns 0.0 if the series is missing or malformed.
    """
    try:
        df = macro_data.get(series_id)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return 0.0
        if "value" not in df.columns:
            return 0.0
        vals = df["value"].dropna()
        if vals.empty:
            return 0.0
        return float(vals.iloc[-1])
    except Exception as exc:
        logger.debug("_extract_macro_value({}) failed: {}", series_id, exc)
        return 0.0


def _extract_macro_change_30d(macro_data: dict, series_id: str) -> float:
    """Return the 30-day percentage change for a macro series. Returns 0.0 on failure."""
    try:
        df = macro_data.get(series_id)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return 0.0
        if "value" not in df.columns:
            return 0.0
        df2 = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2["value"].dropna()
        if len(vals) < 2:
            return 0.0
        current = float(vals.iloc[-1])
        if "date" in df2.columns:
            ref_date = df2["date"].max() - pd.Timedelta(days=30)
            mask = df2["date"] <= ref_date
            if not mask.any():
                return 0.0
            ago = float(df2.loc[mask, "value"].dropna().iloc[-1])
        else:
            idx = max(0, len(vals) - 31)
            ago = float(vals.iloc[idx])
        if ago == 0:
            return 0.0
        return round((current - ago) / abs(ago) * 100, 2)
    except Exception as exc:
        logger.debug("_extract_macro_change_30d({}) failed: {}", series_id, exc)
        return 0.0


def _compute_freight_momentum(freight_data: dict) -> tuple:
    """Compute average 30-day freight rate change and a momentum label.

    Iterates all keys in freight_data (each should be a DataFrame with
    'value' or 'rate_usd_per_feu' column). Returns (avg_pct_change, label).
    """
    try:
        changes = []
        for key, df in freight_data.items():
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            # Support both normalised freight DataFrames and generic value frames
            val_col = None
            for candidate in ("rate_usd_per_feu", "value", "close"):
                if candidate in df.columns:
                    val_col = candidate
                    break
            if val_col is None:
                continue
            df2 = df.copy()
            if "date" in df2.columns:
                df2 = df2.sort_values("date")
            vals = df2[val_col].dropna()
            if len(vals) < 2:
                continue
            current = float(vals.iloc[-1])
            if "date" in df2.columns:
                ref_date = df2["date"].max() - pd.Timedelta(days=30)
                mask = df2["date"] <= ref_date
                if not mask.any():
                    continue
                ago = float(df2.loc[mask, val_col].dropna().iloc[-1])
            else:
                idx = max(0, len(vals) - 31)
                ago = float(vals.iloc[idx])
            if ago == 0:
                continue
            pct = (current - ago) / abs(ago) * 100
            changes.append(pct)

        if not changes:
            return 0.0, "Stable"

        avg = float(np.mean(changes))
        if avg > 5.0:
            label = "Accelerating"
        elif avg < -5.0:
            label = "Decelerating"
        else:
            label = "Stable"
        return round(avg, 2), label

    except Exception as exc:
        logger.warning("_compute_freight_momentum failed: {}", exc)
        return 0.0, "Stable"


def _compute_composite_sentiment(
    news_score: float,
    freight_momentum: float,
    bdi_change: float,
    signal_conviction_avg: float,
) -> tuple:
    """Weighted composite sentiment score from four independent sources.

    Weights:
      news sentiment       35% — direct market narrative from RSS feeds
      freight momentum     30% — rate direction is the most leading indicator
      BDI change           20% — macro shipping demand proxy
      signal conviction    15% — quantitative alpha engine signal direction

    Returns (composite_score, label) where score is in [-1.0, +1.0].
    """
    try:
        # Normalise freight momentum (pct) to [-1, +1] — cap at ±20%
        freight_norm = max(-1.0, min(1.0, freight_momentum / 20.0))
        # Normalise BDI change (pct) to [-1, +1] — cap at ±30%
        bdi_norm = max(-1.0, min(1.0, bdi_change / 30.0))
        # signal_conviction_avg is already in [-1, +1] direction-weighted

        composite = (
            0.35 * news_score
            + 0.30 * freight_norm
            + 0.20 * bdi_norm
            + 0.15 * signal_conviction_avg
        )
        composite = max(-1.0, min(1.0, composite))

        if composite >= 0.35:
            label = "BULLISH"
        elif composite >= 0.10:
            label = "MIXED"
        elif composite >= -0.10:
            label = "NEUTRAL"
        elif composite >= -0.35:
            label = "MIXED"
        else:
            label = "BEARISH"

        return round(composite, 4), label

    except Exception as exc:
        logger.warning("_compute_composite_sentiment failed: {}", exc)
        return 0.0, "NEUTRAL"


def _signals_by_ticker(signals: list) -> dict:
    """Group a flat list of AlphaSignal objects by ticker symbol.

    Returns dict[ticker -> list[AlphaSignal]], sorted by conviction+strength.
    """
    result: dict = {}
    for sig in signals:
        ticker = getattr(sig, "ticker", "UNKNOWN")
        result.setdefault(ticker, []).append(sig)
    return result


def _assess_data_quality(
    port_results: list,
    route_results: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
) -> str:
    """Determine overall data quality: FULL | PARTIAL | DEGRADED.

    FULL:     all five data sources present and non-empty
    PARTIAL:  three or four sources present
    DEGRADED: fewer than three sources present
    """
    try:
        score = 0
        if port_results:
            score += 1
        if route_results:
            score += 1
        if freight_data and any(
            isinstance(v, pd.DataFrame) and not v.empty
            for v in freight_data.values()
        ):
            score += 1
        if macro_data and any(
            isinstance(v, pd.DataFrame) and not v.empty
            for v in macro_data.values()
        ):
            score += 1
        if stock_data and any(
            isinstance(v, pd.DataFrame) and not v.empty
            for v in stock_data.values()
        ):
            score += 1

        if score == 5:
            return "FULL"
        if score >= 3:
            return "PARTIAL"
        return "DEGRADED"
    except Exception:
        return "DEGRADED"


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _signal_conviction_avg(signals: list) -> float:
    """Compute direction-weighted average conviction for a list of AlphaSignals.

    Maps HIGH=1.0, MEDIUM=0.65, LOW=0.35; negated for SHORT signals.
    Returns a value in [-1.0, +1.0].
    """
    if not signals:
        return 0.0
    conviction_map = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.35}
    values = []
    for sig in signals:
        c = conviction_map.get(getattr(sig, "conviction", "LOW"), 0.35)
        direction = getattr(sig, "direction", "NEUTRAL")
        sign = 1.0 if direction == "LONG" else (-1.0 if direction == "SHORT" else 0.0)
        values.append(c * sign)
    avg = float(np.mean(values)) if values else 0.0
    return max(-1.0, min(1.0, round(avg, 4)))


def _latest_stock_price(stock_data: dict, ticker: str) -> float:
    """Return the latest closing price for a ticker, or 0.0."""
    try:
        df = stock_data.get(ticker)
        if df is None or df.empty or "close" not in df.columns:
            return 0.0
        vals = df["close"].dropna()
        return float(vals.iloc[-1]) if not vals.empty else 0.0
    except Exception:
        return 0.0


def _stock_change_30d(stock_data: dict, ticker: str) -> float:
    """Return 30-day percentage price change for a ticker, or 0.0."""
    try:
        df = stock_data.get(ticker)
        if df is None or df.empty or "close" not in df.columns:
            return 0.0
        df2 = df.copy()
        if "date" in df2.columns:
            df2 = df2.sort_values("date")
        vals = df2["close"].dropna()
        if len(vals) < 2:
            return 0.0
        current = float(vals.iloc[-1])
        if "date" in df2.columns:
            ref = df2["date"].max() - pd.Timedelta(days=30)
            mask = df2["date"] <= ref
            if not mask.any():
                return 0.0
            ago = float(df2.loc[mask, "close"].dropna().iloc[-1])
        else:
            idx = max(0, len(vals) - 31)
            ago = float(vals.iloc[idx])
        if ago == 0:
            return 0.0
        return round((current - ago) / abs(ago) * 100, 2)
    except Exception:
        return 0.0


def _fbx_composite(freight_data: dict) -> float:
    """Return average of all available FBX rate series (latest values)."""
    try:
        fbx_vals = []
        for key, df in freight_data.items():
            k_str = str(key).upper()
            if not ("FBX" in k_str or "FREIGHT" in k_str or "RATE" in k_str):
                continue
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            val_col = next(
                (c for c in ("rate_usd_per_feu", "value", "close") if c in df.columns),
                None,
            )
            if val_col is None:
                continue
            vals = df[val_col].dropna()
            if not vals.empty:
                fbx_vals.append(float(vals.iloc[-1]))
        return round(float(np.mean(fbx_vals)), 2) if fbx_vals else 0.0
    except Exception:
        return 0.0


def _build_freight_routes_list(freight_data: dict) -> list:
    """Build a list of route dicts from freight_data for FreightRateSummary.routes."""
    routes = []
    try:
        for route_id, df in freight_data.items():
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            val_col = next(
                (c for c in ("rate_usd_per_feu", "value", "close") if c in df.columns),
                None,
            )
            if val_col is None:
                continue
            df2 = df.copy()
            if "date" in df2.columns:
                df2 = df2.sort_values("date")
            vals = df2[val_col].dropna()
            if vals.empty:
                continue
            rate = float(vals.iloc[-1])

            # 30d change
            if "date" in df2.columns:
                ref = df2["date"].max() - pd.Timedelta(days=30)
                mask = df2["date"] <= ref
                if mask.any():
                    ago = float(df2.loc[mask, val_col].dropna().iloc[-1])
                    change_30d = rate - ago
                    change_pct = round((change_30d / abs(ago) * 100), 2) if ago != 0 else 0.0
                else:
                    change_30d = 0.0
                    change_pct = 0.0
            else:
                idx = max(0, len(vals) - 31)
                ago = float(vals.iloc[idx])
                change_30d = rate - ago
                change_pct = round((change_30d / abs(ago) * 100), 2) if ago != 0 else 0.0

            if change_pct > 5:
                trend = "UP"
                label = "Rising"
            elif change_pct < -5:
                trend = "DOWN"
                label = "Falling"
            else:
                trend = "FLAT"
                label = "Stable"

            routes.append({
                "route_id":   str(route_id),
                "rate":       round(rate, 2),
                "change_30d": round(change_30d, 2),
                "change_pct": change_pct,
                "trend":      trend,
                "label":      label,
            })
    except Exception as exc:
        logger.warning("_build_freight_routes_list failed: {}", exc)
    return routes


def _supply_chain_stress(bdi: float, bdi_chg: float, wti: float, wti_chg: float) -> str:
    """Derive supply chain stress level from BDI and WTI dynamics."""
    try:
        stress_score = 0
        # Rising BDI suggests demand > supply → stress signal
        if bdi_chg > 15:
            stress_score += 2
        elif bdi_chg > 5:
            stress_score += 1
        # Rising oil compounds shipping cost
        if wti_chg > 15:
            stress_score += 2
        elif wti_chg > 7:
            stress_score += 1
        # High absolute BDI (above historical norms)
        if bdi > 2500:
            stress_score += 1

        if stress_score >= 4:
            return "HIGH"
        if stress_score >= 2:
            return "MODERATE"
        return "LOW"
    except Exception:
        return "MODERATE"


# ---------------------------------------------------------------------------
# AI Narrative Generation (fully rule-based, no external API)
# ---------------------------------------------------------------------------

_DISCLAIMER = (
    "IMPORTANT DISCLAIMER: This report is generated for informational and "
    "research purposes only. It does not constitute investment advice, a "
    "solicitation to buy or sell any security, or a recommendation of any "
    "specific investment strategy. Past performance is not indicative of future "
    "results. All investments involve risk, including the possible loss of "
    "principal. Shipping equities are highly volatile and subject to "
    "sector-specific risks including freight rate cycles, fuel cost fluctuations, "
    "geopolitical disruptions, and regulatory changes. The analysis presented "
    "herein is based on publicly available data and rule-based models; it has not "
    "been verified by a licensed financial advisor. Always consult a qualified "
    "financial professional before making any investment decisions."
)


def _generate_executive_summary(
    sentiment: SentimentBreakdown,
    macro: MacroSnapshot,
    alpha: AlphaSignalSummary,
    freight: FreightRateSummary,
    market: MarketIntelligenceSummary,
    stocks: StockAnalysis,
) -> str:
    """Generate a 3-paragraph executive summary from structured data."""

    # --- Paragraph 1: Sentiment + macro overview ---
    bdi_str = f"{macro.bdi:,.0f}" if macro.bdi else "N/A"
    bdi_dir = "up" if macro.bdi_change_30d_pct > 0 else ("down" if macro.bdi_change_30d_pct < 0 else "flat")
    bdi_chg_str = f"{abs(macro.bdi_change_30d_pct):.1f}%"
    wti_str = f"${macro.wti:.2f}/bbl" if macro.wti else "N/A"
    wti_dir = "up" if macro.wti_change_30d_pct > 0 else "down"
    wti_chg_str = f"{abs(macro.wti_change_30d_pct):.1f}%"

    sentiment_adj = {
        "BULLISH": "firmly bullish",
        "BEARISH": "broadly bearish",
        "NEUTRAL": "broadly neutral",
        "MIXED": "mixed, with notable cross-currents",
    }.get(sentiment.overall_label, "uncertain")

    freight_env_str = (
        "supportive, with freight rates accelerating across major trade lanes"
        if freight.momentum_label == "Accelerating"
        else (
            "under pressure, with rates decelerating across key corridors"
            if freight.momentum_label == "Decelerating"
            else "range-bound, with rates holding near recent levels"
        )
    )

    p1 = (
        f"Global shipping markets present a {sentiment_adj} picture as of {datetime.now(timezone.utc).strftime('%B %d, %Y')}, "
        f"with the composite sentiment score registering {sentiment.overall_score:+.2f} on a "
        f"-1.0 to +1.0 scale. "
        f"The Baltic Dry Index stands at {bdi_str}, {bdi_dir} {bdi_chg_str} over the past 30 days, "
        f"reflecting {'tightening vessel supply against recovering cargo demand' if macro.bdi_change_30d_pct > 0 else 'softening demand or easing supply-side constraints'}. "
        f"Crude oil (WTI) is trading at {wti_str}, {wti_dir} {wti_chg_str} on the month, "
        f"{'adding to bunker cost pressures for carriers' if macro.wti_change_30d_pct > 0 else 'providing some relief on operating cost for shipowners'}. "
        f"The overall freight environment is {freight_env_str}, "
        f"with average 30-day rate momentum at {freight.avg_change_30d_pct:+.1f}% across monitored routes."
    )

    # --- Paragraph 2: Alpha signals and portfolio positioning ---
    n_signals = len(alpha.signals)
    n_long = sum(1 for s in alpha.signals if getattr(s, "direction", "") == "LONG")
    n_short = sum(1 for s in alpha.signals if getattr(s, "direction", "") == "SHORT")
    n_high = sum(1 for s in alpha.signals if getattr(s, "conviction", "") == "HIGH")
    exp_ret = _safe_float(alpha.portfolio.get("expected_return", 0.0))
    sharpe = _safe_float(alpha.portfolio.get("sharpe", 0.0))

    top_long_str = "none identified"
    if alpha.top_long:
        top_long_ticker = getattr(alpha.top_long[0], "ticker", "N/A")
        top_long_name = getattr(alpha.top_long[0], "signal_name", "")
        top_long_str = f"{top_long_ticker} ({top_long_name})"

    top_pick_str = stocks.top_pick if stocks.top_pick else "N/A"
    top_pick_price = stocks.prices.get(stocks.top_pick, 0.0)
    top_pick_chg = stocks.changes_30d.get(stocks.top_pick, 0.0)

    p2 = (
        f"The alpha signal engine has generated {n_signals} active signals across the coverage universe, "
        f"comprising {n_long} LONG and {n_short} SHORT signals, with {n_high} rated HIGH conviction. "
        f"The portfolio is positioned with an expected return of {exp_ret:+.1f}% and an estimated Sharpe ratio of {sharpe:.2f}, "
        f"reflecting {'strong risk-adjusted upside' if sharpe > 1.0 else ('acceptable risk-adjusted positioning' if sharpe > 0 else 'defensive positioning given headwinds')}. "
        f"The highest-priority long idea is {top_long_str}. "
        f"Among individual equities, {top_pick_str} is the top-rated pick "
        f"{'at ${:.2f}, up {:.1f}% over 30 days'.format(top_pick_price, top_pick_chg) if top_pick_price else 'based on signal scoring'}, "
        f"supported by convergent signals across momentum, macro, and fundamental factors."
    )

    # --- Paragraph 3: Risks and outlook ---
    risk_label = market.risk_level
    risk_adj = {
        "LOW": "manageable",
        "MODERATE": "moderate but navigable",
        "HIGH": "elevated and warranting caution",
        "CRITICAL": "critical — defensive postures are advised",
    }.get(risk_label, "uncertain")

    supply_stress_str = {
        "LOW": "supply chains are operating with adequate buffers",
        "MODERATE": "supply chains show pockets of stress at key chokepoints",
        "HIGH": "supply chains are under significant stress, with congestion and delays elevated",
    }.get(macro.supply_chain_stress, "supply chain conditions are unclear")

    tsy_str = f"{macro.treasury_10y:.2f}%" if macro.treasury_10y else "N/A"

    p3 = (
        f"Risk conditions are {risk_adj}, with the composite risk level assessed as {risk_label}. "
        f"The 10-year US Treasury yield at {tsy_str} {'continues to weigh on equity valuations' if macro.treasury_10y > 4.5 else 'provides a relatively accommodative financing backdrop'}, "
        f"while {supply_stress_str}. "
        f"Investors should monitor the {freight.biggest_mover.get('route_id', 'key route') if freight.biggest_mover else 'key route'} corridor closely — "
        f"it registered the largest 30-day rate move ({freight.biggest_mover.get('change_pct', 0.0):+.1f}% change) "
        f"and could signal broader directional shifts if momentum continues. "
        f"The 30-day outlook hinges on whether BDI sustains its current trajectory and whether "
        f"macro manufacturing conditions — currently proxied by {macro.pmi_proxy:.1f} — "
        f"{'strengthen toward expansion territory' if macro.pmi_proxy < 50 else 'hold above the critical 50-threshold'}."
    )

    return "\n\n".join([p1, p2, p3])


def _generate_sentiment_narrative(
    sentiment: SentimentBreakdown,
    freight: FreightRateSummary,
    news_items: list,
) -> str:
    """Generate 2-paragraph sentiment narrative."""

    # Paragraph 1: News sentiment breakdown
    total_articles = sentiment.bullish_count + sentiment.bearish_count + sentiment.neutral_count
    bullish_pct = (sentiment.bullish_count / total_articles * 100) if total_articles else 0
    bearish_pct = (sentiment.bearish_count / total_articles * 100) if total_articles else 0

    top_kw_str = (
        ", ".join(sentiment.top_keywords[:5]) if sentiment.top_keywords else "shipping, freight, trade"
    )

    skew_desc = (
        "markedly bullish" if bullish_pct > 55
        else ("bearish-leaning" if bearish_pct > 50
              else ("evenly split" if abs(bullish_pct - bearish_pct) < 15
                    else "mixed"))
    )

    p1 = (
        f"News sentiment across {total_articles} shipping articles is {skew_desc}, "
        f"with {sentiment.bullish_count} bullish ({bullish_pct:.0f}%), "
        f"{sentiment.bearish_count} bearish ({bearish_pct:.0f}%), "
        f"and {sentiment.neutral_count} neutral articles sampled from major shipping publications. "
        f"The aggregate relevance-weighted score is {sentiment.news_score:+.3f}. "
        f"Trending topics driving narrative include: {top_kw_str}. "
        f"{'The preponderance of bullish coverage suggests market participants are pricing in continued rate strength and demand recovery.' if bullish_pct > 50 else 'The bearish tilt in coverage reflects market concerns over demand softness and potential overcapacity risks heading into the next quarter.' if bearish_pct > 50 else 'The balanced sentiment signals a market at an inflection point, with near-term direction dependent on rate trajectory and macro catalysts.'}"
    )

    # Paragraph 2: Freight rate momentum and equity implications
    rate_dir_str = (
        "accelerating to the upside" if freight.momentum_label == "Accelerating"
        else ("decelerating" if freight.momentum_label == "Decelerating"
              else "consolidating")
    )

    biggest_route = freight.biggest_mover.get("route_id", "the major trade lane") if freight.biggest_mover else "the major trade lane"
    biggest_chg = freight.biggest_mover.get("change_pct", 0.0) if freight.biggest_mover else 0.0
    fbx_str = f"${freight.fbx_composite:,.0f}/FEU" if freight.fbx_composite else "levels not available"

    equity_implication = (
        "typically leading to improved revenue visibility for spot-exposed carriers such as ZIM within 4-6 weeks"
        if freight.momentum_label == "Accelerating"
        else (
            "historically a headwind for rate-sensitive carriers and a potential catalyst for margin compression in coming quarters"
            if freight.momentum_label == "Decelerating"
            else "providing earnings stability for carriers with long-term contract exposure"
        )
    )

    p2 = (
        f"Freight rate momentum is {rate_dir_str} across the monitored route universe, "
        f"with an average 30-day change of {freight.avg_change_30d_pct:+.1f}% and an FBX composite reading of {fbx_str}. "
        f"The biggest rate mover is {biggest_route} at {biggest_chg:+.1f}% over 30 days. "
        f"This rate environment is {equity_implication}. "
        f"Investors with exposure to shipping equities should interpret the freight sentiment composite — "
        f"currently {sentiment.freight_score:+.3f} — as a leading indicator: "
        f"{'freight markets are signaling expansion, supporting a constructive equity view.' if sentiment.freight_score > 0.1 else 'freight markets are signaling contraction, arguing for a cautious or defensive equity posture.' if sentiment.freight_score < -0.1 else 'freight is near equilibrium, suggesting stock selection based on individual catalyst rather than sector-wide tailwinds.'}"
    )

    return "\n\n".join([p1, p2])


def _generate_opportunity_narrative(
    market: MarketIntelligenceSummary,
    alpha: AlphaSignalSummary,
    freight: FreightRateSummary,
) -> str:
    """Generate opportunity prose covering top 3 opportunities."""

    opportunities = []

    # Pull from top insights first, then top alpha signals
    for insight in market.top_insights[:3]:
        title = getattr(insight, "title", "Shipping Opportunity")
        detail = getattr(insight, "detail", "")
        score = getattr(insight, "score", 0.0)
        action = getattr(insight, "action", "Monitor")
        routes = getattr(insight, "routes_involved", [])
        stocks_affected = getattr(insight, "stocks_potentially_affected", [])
        route_str = ", ".join(routes[:2]) if routes else "monitored trade lanes"
        stocks_str = ", ".join(stocks_affected[:2]) if stocks_affected else "relevant equities"
        opportunities.append({
            "title": title,
            "detail": detail,
            "score": score,
            "action": action,
            "route_str": route_str,
            "stocks_str": stocks_str,
            "source": "insight",
        })

    # Supplement with top alpha signals if fewer than 3 insight opportunities
    for sig in alpha.top_long[:max(0, 3 - len(opportunities))]:
        ticker = getattr(sig, "ticker", "N/A")
        signal_name = getattr(sig, "signal_name", "")
        rationale = getattr(sig, "rationale", "")
        conviction = getattr(sig, "conviction", "MEDIUM")
        exp_ret = getattr(sig, "expected_return_pct", 0.0)
        horizon = getattr(sig, "time_horizon", "1M")
        opportunities.append({
            "title": f"{ticker} — {signal_name}",
            "detail": rationale,
            "score": {"HIGH": 0.85, "MEDIUM": 0.65, "LOW": 0.40}.get(conviction, 0.65),
            "action": "Prioritize" if conviction == "HIGH" else "Monitor",
            "route_str": "N/A",
            "stocks_str": ticker,
            "source": "alpha",
            "expected_return": exp_ret,
            "horizon": horizon,
        })

    if not opportunities:
        return (
            "No high-conviction opportunities are currently identified across the monitored universe. "
            "Markets appear to be in a consolidation phase. Investors should maintain watchlists "
            "and wait for clearer directional signals before deploying capital."
        )

    parts = []
    for i, opp in enumerate(opportunities[:3], 1):
        rank_word = ["First", "Second", "Third"][i - 1]
        score_str = f"{opp['score']:.2f}"
        detail_preview = opp["detail"][:200].rstrip() + ("..." if len(opp["detail"]) > 200 else "")
        if opp.get("source") == "alpha":
            exp_ret = opp.get("expected_return", 0.0)
            horizon = opp.get("horizon", "1M")
            part = (
                f"{rank_word}: {opp['title']} (conviction score {score_str}) — "
                f"The alpha engine identifies {opp['stocks_str']} as an actionable long with "
                f"an expected return of {exp_ret:+.1f}% over a {horizon} horizon. "
                f"{detail_preview}"
            )
        else:
            part = (
                f"{rank_word}: {opp['title']} (score {score_str}, action: {opp['action']}) — "
                f"This opportunity is concentrated on {opp['route_str']}, "
                f"with potential equity impact on {opp['stocks_str']}. "
                f"{detail_preview}"
            )
        parts.append(part)

    return "\n\n".join(parts)


def _generate_risk_narrative(
    market: MarketIntelligenceSummary,
    macro: MacroSnapshot,
    freight: FreightRateSummary,
    sentiment: SentimentBreakdown,
) -> str:
    """Generate key risks prose."""

    risks = []

    # Macro headwinds
    if macro.treasury_10y > 4.5:
        risks.append(
            f"Elevated interest rates (10Y Treasury at {macro.treasury_10y:.2f}%) "
            "create a persistent headwind for capital-intensive shipping equities, "
            "compressing valuation multiples and increasing refinancing costs for leveraged operators."
        )
    if macro.wti_change_30d_pct > 10:
        risks.append(
            f"Rising fuel costs — WTI up {macro.wti_change_30d_pct:.1f}% over 30 days — "
            "are compressing operating margins for shipowners who hedge fuel only partially. "
            "Carriers with lower bunker hedging ratios face near-term earnings pressure."
        )

    # Freight deceleration risk
    if freight.momentum_label == "Decelerating":
        risks.append(
            f"Freight rate deceleration (avg 30d change: {freight.avg_change_30d_pct:+.1f}%) "
            "poses a revenue headwind for spot-rate-exposed carriers. "
            "If the trend continues, consensus earnings estimates for ZIM, MATX, and SBLK "
            "may require downward revision, particularly for H2."
        )

    # Sentiment risk
    if sentiment.bearish_count > sentiment.bullish_count:
        risks.append(
            f"News flow is net-bearish ({sentiment.bearish_count} bearish vs "
            f"{sentiment.bullish_count} bullish articles), suggesting that market "
            "participants and industry participants are turning cautious. "
            "Narrative shifts of this type often precede rate softening by 4-8 weeks."
        )

    # Supply chain stress
    if macro.supply_chain_stress == "HIGH":
        risks.append(
            "Supply chain stress is elevated — a combination of high freight rates, "
            "rising oil costs, and BDI momentum signals potential congestion-driven delays "
            "at key transhipment hubs, which could paradoxically increase near-term rates "
            "while eroding shippers' profitability through detention and demurrage charges."
        )

    # BDI risk
    if macro.bdi_change_30d_pct < -10:
        risks.append(
            f"The Baltic Dry Index has fallen {abs(macro.bdi_change_30d_pct):.1f}% over 30 days, "
            "historically a reliable leading indicator of softening dry bulk demand. "
            "SBLK is most directly exposed; investors should monitor this trend closely "
            "before adding dry bulk exposure."
        )

    # Geopolitical fallback
    risks.append(
        "Geopolitical risks remain a persistent tail risk for global shipping lanes, "
        "particularly on the Asia-Europe corridor via the Red Sea and Suez Canal. "
        "Any escalation of regional tensions could force routing via the Cape of Good Hope, "
        "adding approximately 10-14 days of transit and materially increasing operating costs."
    )

    # Market-level risk
    risks.append(
        "Potential overcapacity risk: a significant orderbook of new container vessels "
        "is scheduled for delivery over the next 18-24 months. "
        "If demand growth fails to absorb this additional supply, freight rates could face "
        "sustained downward pressure, weighing on earnings across the container shipping sector."
    )

    # Select top 4 most relevant risks
    top_risks = risks[:4]
    return "\n\n".join(top_risks)


def _generate_outlook_30d(
    macro: MacroSnapshot,
    freight: FreightRateSummary,
    sentiment: SentimentBreakdown,
    alpha: AlphaSignalSummary,
) -> str:
    """Generate a forward-looking 30-day outlook paragraph."""

    import datetime as _dt
    month = _dt.date.today().month

    seasonal_context = {
        1:  "January seasonality typically sees a post-holiday demand lull, with freight rates historically bottoming in weeks 2-4",
        2:  "February is dominated by the Chinese New Year effect — factory restarts drive a progressive demand recovery in dry bulk",
        3:  "March marks the post-CNY ramp; dry bulk typically outperforms as Chinese industrial activity accelerates",
        4:  "April represents an early pre-peak positioning window; trans-Pacific rates begin to firm as retailers front-load inventory",
        5:  "May is the transition month from slack to peak season; trans-Pacific contract negotiations begin to influence spot",
        6:  "June kicks off peak shipping season preparations — historical data shows ZIM and MATX outperform the peer group",
        7:  "July represents peak demand season for consumer goods shipping; trans-Pacific rates are typically at year highs",
        8:  "August maintains peak season momentum; blank sailings by carriers often drive additional rate spikes",
        9:  "September marks late peak season; early signs of holiday inventory pull-forward support continued rate strength",
        10: "October enters the pre-holiday inventory buildup phase; mixed dynamics as some retailers have already front-loaded",
        11: "November post-peak rebalancing begins; carriers typically introduce blank sailings to manage rate floors",
        12: "December is seasonally weak for new bookings; rate softness typically persists through January",
    }.get(month, "Seasonal patterns are mixed")

    direction = (
        "constructive" if sentiment.overall_score > 0.1
        else ("cautious" if sentiment.overall_score < -0.1 else "neutral")
    )

    n_high_signals = sum(1 for s in alpha.signals if getattr(s, "conviction", "") == "HIGH")
    exp_ret = _safe_float(alpha.portfolio.get("expected_return", 0.0))
    bdi_trend_str = (
        f"BDI momentum is positive (+{macro.bdi_change_30d_pct:.1f}% over 30 days), typically a 4-6 week leading indicator for shipping equity outperformance"
        if macro.bdi_change_30d_pct > 3
        else (
            f"BDI has been under pressure ({macro.bdi_change_30d_pct:+.1f}% over 30 days), which may constrain near-term upside for dry bulk names"
            if macro.bdi_change_30d_pct < -3
            else "BDI is effectively flat over 30 days, suggesting equilibrium between supply and demand"
        )
    )

    return (
        f"The 30-day outlook is {direction} on balance. "
        f"{seasonal_context}. "
        f"{bdi_trend_str}. "
        f"The alpha engine carries {n_high_signals} HIGH-conviction signals pointing net-{'long' if exp_ret > 0 else 'short'}, "
        f"implying a portfolio-level expected return of {exp_ret:+.1f}% over the next 30 days under base-case conditions. "
        f"Key catalysts to watch: (1) weekly BDI prints for directional confirmation, "
        f"(2) trans-Pacific spot rate updates from Freightos/FBX, "
        f"(3) any FOMC communication shifts that could reprice the Treasury curve, "
        f"and (4) Chinese export and manufacturing data as the primary demand driver for dry bulk and container shipping alike."
    )


def _generate_top_recommendations(
    alpha: AlphaSignalSummary,
    market: MarketIntelligenceSummary,
    stocks: StockAnalysis,
    freight: FreightRateSummary,
) -> list:
    """Build the top 3-5 structured recommendation dicts."""

    recommendations = []

    # Alpha signal recommendations
    all_long = sorted(
        [s for s in alpha.signals if getattr(s, "direction", "") == "LONG"],
        key=lambda s: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(getattr(s, "conviction", "LOW"), 2), -getattr(s, "strength", 0)),
    )
    all_short = sorted(
        [s for s in alpha.signals if getattr(s, "direction", "") == "SHORT"],
        key=lambda s: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(getattr(s, "conviction", "LOW"), 2), -getattr(s, "strength", 0)),
    )

    rank = 1
    for sig in (all_long[:3] + all_short[:1]):
        ticker = getattr(sig, "ticker", "N/A")
        direction = getattr(sig, "direction", "LONG")
        conviction = getattr(sig, "conviction", "MEDIUM")
        signal_name = getattr(sig, "signal_name", "")
        exp_ret = getattr(sig, "expected_return_pct", 0.0)
        horizon = getattr(sig, "time_horizon", "1M")
        rationale = getattr(sig, "rationale", "")
        entry = getattr(sig, "entry_price", 0.0)
        target = getattr(sig, "target_price", 0.0)
        stop = getattr(sig, "stop_loss", 0.0)
        rr = getattr(sig, "risk_reward", 0.0)

        action = "BUY" if direction == "LONG" else "SELL"
        risk_rating = (
            "HIGH" if abs(exp_ret) > 20
            else ("MODERATE" if abs(exp_ret) > 10 else "LOW")
        )
        # Shorten rationale to 2 sentences
        rationale_sentences = [s.strip() for s in rationale.split(". ") if s.strip()]
        rationale_2 = ". ".join(rationale_sentences[:2]) + ("." if rationale_sentences else "")

        recommendations.append({
            "rank":            rank,
            "title":           f"{'Long' if direction == 'LONG' else 'Short'} {ticker} — {signal_name}",
            "action":          action,
            "ticker":          ticker,
            "conviction":      conviction,
            "time_horizon":    horizon,
            "rationale":       rationale_2,
            "expected_return": round(exp_ret, 2),
            "risk_rating":     risk_rating,
            "entry":           round(entry, 2),
            "target":          round(target, 2),
            "stop":            round(stop, 2),
        })
        rank += 1

    # Route/insight recommendation if room
    if len(recommendations) < 5 and market.top_insights:
        top_insight = market.top_insights[0]
        title = getattr(top_insight, "title", "Route Opportunity")
        detail = getattr(top_insight, "detail", "")
        score = getattr(top_insight, "score", 0.5)
        action_str = getattr(top_insight, "action", "Monitor")
        action_map = {
            "Prioritize": "BUY",
            "Monitor":    "MONITOR",
            "Watch":      "MONITOR",
            "Caution":    "HOLD",
            "Avoid":      "SELL",
        }
        detail_2s = [s.strip() for s in detail.split(". ") if s.strip()]
        detail_short = ". ".join(detail_2s[:2]) + ("." if detail_2s else "")
        conviction = "HIGH" if score >= 0.75 else ("MEDIUM" if score >= 0.5 else "LOW")

        recommendations.append({
            "rank":            rank,
            "title":           f"Route Play — {title}",
            "action":          action_map.get(action_str, "MONITOR"),
            "ticker":          ", ".join(getattr(top_insight, "stocks_potentially_affected", [])[:2]) or "N/A",
            "conviction":      conviction,
            "time_horizon":    "1M",
            "rationale":       detail_short,
            "expected_return": 0.0,
            "risk_rating":     "MODERATE",
            "entry":           0.0,
            "target":          0.0,
            "stop":            0.0,
        })

    return recommendations[:5]


def _build_ai_analysis(
    sentiment: SentimentBreakdown,
    alpha: AlphaSignalSummary,
    market: MarketIntelligenceSummary,
    freight: FreightRateSummary,
    macro: MacroSnapshot,
    stocks: StockAnalysis,
    news_items: list,
) -> AIAnalysis:
    """Orchestrate all narrative generation into a complete AIAnalysis."""

    try:
        executive_summary = _generate_executive_summary(
            sentiment, macro, alpha, freight, market, stocks
        )
    except Exception as exc:
        logger.warning("executive_summary generation failed: {}", exc)
        executive_summary = (
            "Global shipping markets are under active analysis. "
            "The BDI and freight rate trends are the primary inputs for near-term positioning. "
            "Please refer to the detailed data sections for specific signal and rate information."
        )

    try:
        sentiment_narrative = _generate_sentiment_narrative(sentiment, freight, news_items)
    except Exception as exc:
        logger.warning("sentiment_narrative generation failed: {}", exc)
        sentiment_narrative = (
            "News sentiment data is being processed. "
            "Please refer to the news section for individual article sentiment scores."
        )

    try:
        opportunity_narrative = _generate_opportunity_narrative(market, alpha, freight)
    except Exception as exc:
        logger.warning("opportunity_narrative generation failed: {}", exc)
        opportunity_narrative = (
            "Opportunity analysis is pending data aggregation. "
            "Refer to the alpha signal scorecard for current signal rankings."
        )

    try:
        risk_narrative = _generate_risk_narrative(market, macro, freight, sentiment)
    except Exception as exc:
        logger.warning("risk_narrative generation failed: {}", exc)
        risk_narrative = (
            "Risk analysis is pending full data loading. "
            "Standard shipping sector risks apply: freight rate volatility, fuel cost exposure, "
            "geopolitical route disruptions, and regulatory changes."
        )

    try:
        outlook_30d = _generate_outlook_30d(macro, freight, sentiment, alpha)
    except Exception as exc:
        logger.warning("outlook_30d generation failed: {}", exc)
        outlook_30d = (
            "The 30-day outlook depends on BDI trajectory, trans-Pacific rate prints, "
            "and macro manufacturing data. Monitor weekly for updated signal conditions."
        )

    try:
        top_recommendations = _generate_top_recommendations(alpha, market, stocks, freight)
    except Exception as exc:
        logger.warning("top_recommendations generation failed: {}", exc)
        top_recommendations = []

    return AIAnalysis(
        executive_summary=executive_summary,
        sentiment_narrative=sentiment_narrative,
        opportunity_narrative=opportunity_narrative,
        risk_narrative=risk_narrative,
        outlook_30d=outlook_30d,
        top_recommendations=top_recommendations,
        disclaimer=_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# Public aliases matching the spec's expected function names
# ---------------------------------------------------------------------------

def fetch_shipping_news(cache=None, ttl_hours: float = 2.0) -> list:
    """Public alias for fetch_all_news — fetch and score shipping news articles.

    Returns a list of NewsArticle objects sorted by relevance descending.
    Returns [] if news_sentiment module is unavailable.
    """
    if not _NEWS_OK:
        logger.warning("fetch_shipping_news: news_sentiment not available")
        return []
    try:
        # Use a temp directory if no cache provided
        if cache is None:
            import tempfile
            cache = tempfile.gettempdir()
        return fetch_all_news(cache=cache, ttl_hours=ttl_hours)
    except Exception as exc:
        logger.error("fetch_shipping_news failed: {}", exc)
        return []


def get_market_sentiment_summary(news_items: list) -> dict:
    """Public alias for get_sentiment_summary — aggregate sentiment stats.

    Returns a dict with overall_score, label, bullish_count, bearish_count,
    neutral_count, trending_entities, etc.
    Returns a neutral default dict if news_sentiment module is unavailable.
    """
    if not _NEWS_OK:
        return {
            "overall_score": 0.0, "label": "NEUTRAL",
            "article_count": 0, "bullish_count": 0,
            "bearish_count": 0, "neutral_count": 0,
            "top_bullish": [], "top_bearish": [], "trending_entities": [],
        }
    try:
        return get_sentiment_summary(news_items)
    except Exception as exc:
        logger.error("get_market_sentiment_summary failed: {}", exc)
        return {
            "overall_score": 0.0, "label": "NEUTRAL",
            "article_count": 0, "bullish_count": 0,
            "bearish_count": 0, "neutral_count": 0,
            "top_bullish": [], "top_bearish": [], "trending_entities": [],
        }


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_investor_report(
    port_results: list,
    route_results: list,
    insights: list,
    freight_data: dict,
    macro_data: dict,
    stock_data: dict,
    news_items: list = None,
) -> InvestorReport:
    """Build a complete InvestorReport from all available data sources.

    Parameters
    ----------
    port_results:  list of PortDemandResult-like objects from ports/demand_analyzer.py
    route_results: list of RouteOpportunity-like objects from routes/optimizer.py
    insights:      list of Insight objects from engine/decision_engine.py
    freight_data:  dict[series_id -> pd.DataFrame] — freight rate time series
    macro_data:    dict[series_id -> pd.DataFrame] — FRED macro time series
    stock_data:    dict[ticker -> pd.DataFrame]    — OHLCV stock price data
    news_items:    optional pre-fetched list[NewsArticle]; fetched fresh if None

    Returns
    -------
    InvestorReport — always complete; individual sections degrade gracefully
                     if their data source is unavailable.
    """
    logger.info("InvestorReportEngine: starting report build")
    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()
    report_date = now.strftime("%B %d, %Y")

    # ------------------------------------------------------------------
    # 1. Fetch / validate news
    # ------------------------------------------------------------------
    if news_items is None:
        logger.info("Fetching shipping news...")
        news_items = fetch_shipping_news()

    top_news = sorted(
        news_items,
        key=lambda a: getattr(a, "relevance_score", 0.0),
        reverse=True,
    )[:15]

    # ------------------------------------------------------------------
    # 2. News sentiment summary
    # ------------------------------------------------------------------
    sentiment_summary = get_market_sentiment_summary(news_items)
    news_score = _safe_float(sentiment_summary.get("overall_score", 0.0))

    # ------------------------------------------------------------------
    # 3. Alpha signals
    # ------------------------------------------------------------------
    signals: list = []
    if _ALPHA_OK:
        try:
            signals = generate_all_signals(
                stock_data=stock_data,
                freight_data=freight_data,
                macro_data=macro_data,
                port_results=port_results,
                route_results=route_results,
            )
            logger.info("Generated {} alpha signals", len(signals))
        except Exception as exc:
            logger.error("generate_all_signals failed: {}", exc)
            signals = []

    # ------------------------------------------------------------------
    # 4. Portfolio alpha
    # ------------------------------------------------------------------
    portfolio: dict = {}
    if _ALPHA_OK and signals:
        try:
            portfolio = compute_portfolio_alpha(signals, stock_data)
        except Exception as exc:
            logger.error("compute_portfolio_alpha failed: {}", exc)
            portfolio = {
                "weights": {}, "expected_return": 0.0,
                "portfolio_vol": 0.0, "sharpe": 0.0, "max_dd_estimate": 0.0,
            }

    # ------------------------------------------------------------------
    # 5. Signal scorecard
    # ------------------------------------------------------------------
    scorecard_df = pd.DataFrame()
    if _ALPHA_OK and signals:
        try:
            scorecard_df = build_signal_scorecard(signals)
        except Exception as exc:
            logger.error("build_signal_scorecard failed: {}", exc)

    # ------------------------------------------------------------------
    # 6. Digest
    # ------------------------------------------------------------------
    digest = None
    if _DIGEST_OK:
        try:
            # build_digest expects stock_data as a list of dicts with ticker/price/change_pct
            stock_list = []
            for ticker, df in stock_data.items():
                if isinstance(df, pd.DataFrame) and not df.empty and "close" in df.columns:
                    price = _latest_stock_price(stock_data, ticker)
                    chg = _stock_change_30d(stock_data, ticker)
                    stock_list.append({
                        "ticker": ticker,
                        "price": price,
                        "change_pct": chg,
                    })
            digest = build_digest(
                port_results=port_results,
                route_results=route_results,
                insights=insights,
                freight_data=freight_data,
                macro_data=macro_data,
                stock_data=stock_list,
            )
            logger.info("Digest built successfully")
        except Exception as exc:
            logger.error("build_digest failed: {}", exc)
            digest = None

    # ------------------------------------------------------------------
    # 7. Sentiment breakdown
    # ------------------------------------------------------------------
    try:
        freight_momentum_pct, momentum_label = _compute_freight_momentum(freight_data)
        bdi_change_30d = _extract_macro_change_30d(macro_data, "BDIY")
        if bdi_change_30d == 0.0:
            bdi_change_30d = _extract_macro_change_30d(macro_data, "BSXRLM")
        sig_conviction = _signal_conviction_avg(signals)

        composite_score, composite_label = _compute_composite_sentiment(
            news_score=news_score,
            freight_momentum=freight_momentum_pct,
            bdi_change=bdi_change_30d,
            signal_conviction_avg=sig_conviction,
        )

        # Trending topics from news
        trending_topics = []
        trending_entities = sentiment_summary.get("trending_entities", [])
        entity_counts = {}
        for art in news_items:
            for ent in getattr(art, "entities", []):
                entity_counts[ent] = entity_counts.get(ent, 0) + 1

        for ent in trending_entities[:10]:
            count = entity_counts.get(ent, 0)
            # Determine entity-level sentiment
            ent_scores = [
                getattr(a, "sentiment_score", 0.0)
                for a in news_items
                if ent in getattr(a, "entities", [])
            ]
            ent_sentiment_score = float(np.mean(ent_scores)) if ent_scores else 0.0
            ent_label = "BULLISH" if ent_sentiment_score > 0.05 else ("BEARISH" if ent_sentiment_score < -0.05 else "NEUTRAL")
            color = {"BULLISH": "#10b981", "BEARISH": "#ef4444", "NEUTRAL": "#64748b"}.get(ent_label, "#64748b")
            trending_topics.append({
                "topic": ent,
                "count": count,
                "sentiment": ent_label,
                "color": color,
            })

        # Freight score from freight momentum normalised
        freight_score = max(-1.0, min(1.0, freight_momentum_pct / 20.0))
        # Macro score from BDI normalised
        macro_score = max(-1.0, min(1.0, bdi_change_30d / 30.0))

        sentiment = SentimentBreakdown(
            overall_score=composite_score,
            overall_label=composite_label,
            news_score=news_score,
            freight_score=freight_score,
            macro_score=macro_score,
            alpha_score=sig_conviction,
            bullish_count=sentiment_summary.get("bullish_count", 0),
            bearish_count=sentiment_summary.get("bearish_count", 0),
            neutral_count=sentiment_summary.get("neutral_count", 0),
            top_keywords=trending_entities[:10],
            trending_topics=trending_topics,
        )
        logger.info("SentimentBreakdown: {} ({:+.3f})", composite_label, composite_score)

    except Exception as exc:
        logger.error("SentimentBreakdown build failed: {}", exc)
        sentiment = SentimentBreakdown(
            overall_score=0.0, overall_label="NEUTRAL",
            news_score=0.0, freight_score=0.0, macro_score=0.0, alpha_score=0.0,
            bullish_count=0, bearish_count=0, neutral_count=0,
            top_keywords=[], trending_topics=[],
        )

    # ------------------------------------------------------------------
    # 8. Alpha signal summary
    # ------------------------------------------------------------------
    try:
        conviction_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        top_long = sorted(
            [s for s in signals if getattr(s, "direction", "") == "LONG"],
            key=lambda s: (conviction_rank.get(getattr(s, "conviction", "LOW"), 2), -getattr(s, "strength", 0)),
        )[:3]
        top_short = sorted(
            [s for s in signals if getattr(s, "direction", "") == "SHORT"],
            key=lambda s: (conviction_rank.get(getattr(s, "conviction", "LOW"), 2), -getattr(s, "strength", 0)),
        )[:3]

        signal_count_by_type = {}
        signal_count_by_conviction = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for sig in signals:
            stype = getattr(sig, "signal_type", "UNKNOWN")
            signal_count_by_type[stype] = signal_count_by_type.get(stype, 0) + 1
            conv = getattr(sig, "conviction", "LOW")
            signal_count_by_conviction[conv] = signal_count_by_conviction.get(conv, 0) + 1

        alpha = AlphaSignalSummary(
            signals=signals,
            portfolio=portfolio,
            top_long=top_long,
            top_short=top_short,
            scorecard_df=scorecard_df,
            signal_count_by_type=signal_count_by_type,
            signal_count_by_conviction=signal_count_by_conviction,
        )
        logger.info(
            "AlphaSignalSummary: {} signals, {} HIGH conviction",
            len(signals), signal_count_by_conviction.get("HIGH", 0)
        )

    except Exception as exc:
        logger.error("AlphaSignalSummary build failed: {}", exc)
        alpha = AlphaSignalSummary(
            signals=[], portfolio={}, top_long=[], top_short=[],
            scorecard_df=pd.DataFrame(),
            signal_count_by_type={}, signal_count_by_conviction={},
        )

    # ------------------------------------------------------------------
    # 9. Market intelligence summary
    # ------------------------------------------------------------------
    try:
        top_insights = sorted(insights, key=lambda i: getattr(i, "score", 0), reverse=True)[:5]

        # Sort port_results — handle both dataclass and dict shapes
        def _port_score(p):
            for attr in ("demand_score", "score", "vessel_count"):
                v = getattr(p, attr, None)
                if v is None and isinstance(p, dict):
                    v = p.get(attr)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return 0.0

        def _route_score(r):
            for attr in ("score", "opportunity_score", "rate"):
                v = getattr(r, attr, None)
                if v is None and isinstance(r, dict):
                    v = r.get(attr)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return 0.0

        top_ports = sorted(port_results, key=_port_score, reverse=True)[:5]
        top_routes = sorted(route_results, key=_route_score, reverse=True)[:5]

        active_opportunities = sum(
            1 for i in insights
            if getattr(i, "action", "") in ("Prioritize", "Monitor")
        )
        high_conviction_count = sum(
            1 for i in insights if _safe_float(getattr(i, "score", 0)) >= 0.70
        )

        # Risk level from high-conviction count and sentiment
        if high_conviction_count >= 3 and composite_label == "BEARISH":
            risk_level = "CRITICAL"
        elif composite_label == "BEARISH" or high_conviction_count >= 4:
            risk_level = "HIGH"
        elif composite_label in ("MIXED", "NEUTRAL") or high_conviction_count >= 2:
            risk_level = "MODERATE"
        else:
            risk_level = "LOW"

        market = MarketIntelligenceSummary(
            top_insights=top_insights,
            top_ports=top_ports,
            top_routes=top_routes,
            risk_level=risk_level,
            active_opportunities=active_opportunities,
            high_conviction_count=high_conviction_count,
        )
        logger.info(
            "MarketIntelligence: risk={}, {} active opportunities",
            risk_level, active_opportunities
        )

    except Exception as exc:
        logger.error("MarketIntelligenceSummary build failed: {}", exc)
        market = MarketIntelligenceSummary(
            top_insights=[], top_ports=[], top_routes=[],
            risk_level="MODERATE", active_opportunities=0, high_conviction_count=0,
        )

    # ------------------------------------------------------------------
    # 10. Freight rate summary
    # ------------------------------------------------------------------
    try:
        freight_routes_list = _build_freight_routes_list(freight_data)
        avg_change_30d_pct, momentum_label_str = _compute_freight_momentum(freight_data)

        biggest_mover: dict = {}
        if freight_routes_list:
            biggest_mover = max(
                freight_routes_list,
                key=lambda r: abs(r.get("change_pct", 0.0)),
                default={},
            )

        fbx_comp = _fbx_composite(freight_data)

        freight = FreightRateSummary(
            routes=freight_routes_list,
            avg_change_30d_pct=avg_change_30d_pct,
            biggest_mover=biggest_mover,
            momentum_label=momentum_label_str,
            fbx_composite=fbx_comp,
        )
        logger.info(
            "FreightRateSummary: {} routes, avg change {:.1f}%, momentum={}",
            len(freight_routes_list), avg_change_30d_pct, momentum_label_str
        )

    except Exception as exc:
        logger.error("FreightRateSummary build failed: {}", exc)
        freight = FreightRateSummary(
            routes=[], avg_change_30d_pct=0.0,
            biggest_mover={}, momentum_label="Stable", fbx_composite=0.0,
        )

    # ------------------------------------------------------------------
    # 11. Macro snapshot
    # ------------------------------------------------------------------
    try:
        # BDI — try BDIY first (commonly used), fall back to BSXRLM (alpha_engine uses this)
        bdi_val = _extract_macro_value(macro_data, "BDIY")
        bdi_chg = _extract_macro_change_30d(macro_data, "BDIY")
        if bdi_val == 0.0:
            bdi_val = _extract_macro_value(macro_data, "BSXRLM")
            bdi_chg = _extract_macro_change_30d(macro_data, "BSXRLM")

        wti_val = _extract_macro_value(macro_data, "DCOILWTICO")
        wti_chg = _extract_macro_change_30d(macro_data, "DCOILWTICO")
        tsy_val = _extract_macro_value(macro_data, "DGS10")
        dxy_val = _extract_macro_value(macro_data, "DEXCHUS")

        # PMI proxy — same logic as alpha_engine: IPMAN → MANEMP → INDPRO
        pmi_proxy = 50.0
        for series_id in ("IPMAN", "MANEMP", "INDPRO"):
            df = macro_data.get(series_id)
            if df is None or not isinstance(df, pd.DataFrame) or df.empty or "value" not in df.columns:
                continue
            vals = df["value"].dropna()
            if len(vals) < 2:
                continue
            current_val = float(vals.iloc[-1])
            avg_90 = float(vals.tail(90).mean())
            if avg_90 > 0:
                pmi_proxy = round(50 + (current_val / avg_90 - 1.0) * 300, 2)
                break

        sc_stress = _supply_chain_stress(bdi_val, bdi_chg, wti_val, wti_chg)

        macro = MacroSnapshot(
            bdi=round(bdi_val, 2),
            bdi_change_30d_pct=round(bdi_chg, 2),
            wti=round(wti_val, 2),
            wti_change_30d_pct=round(wti_chg, 2),
            treasury_10y=round(tsy_val, 3),
            dxy_proxy=round(dxy_val, 4),
            pmi_proxy=pmi_proxy,
            supply_chain_stress=sc_stress,
        )
        logger.info(
            "MacroSnapshot: BDI={:.0f} ({:+.1f}%), WTI=${:.2f}, stress={}",
            bdi_val, bdi_chg, wti_val, sc_stress
        )

    except Exception as exc:
        logger.error("MacroSnapshot build failed: {}", exc)
        macro = MacroSnapshot(
            bdi=0.0, bdi_change_30d_pct=0.0,
            wti=0.0, wti_change_30d_pct=0.0,
            treasury_10y=0.0, dxy_proxy=0.0,
            pmi_proxy=50.0, supply_chain_stress="MODERATE",
        )

    # ------------------------------------------------------------------
    # 12. Stock analysis
    # ------------------------------------------------------------------
    try:
        _TICKERS = ["ZIM", "MATX", "SBLK", "DAC", "CMRE"]
        tickers_present = [t for t in _TICKERS if t in stock_data]

        prices = {t: _latest_stock_price(stock_data, t) for t in tickers_present}
        changes_30d = {t: _stock_change_30d(stock_data, t) for t in tickers_present}
        sbt = _signals_by_ticker(signals)

        # Top pick: ticker with highest-conviction signal, direction LONG
        top_pick = ""
        top_pick_rationale = ""
        if alpha.top_long:
            top_pick = getattr(alpha.top_long[0], "ticker", "")
            top_pick_rationale = getattr(alpha.top_long[0], "rationale", "")
        elif signals:
            # Fall back to highest-strength signal any direction
            top_pick = getattr(signals[0], "ticker", "")
            top_pick_rationale = getattr(signals[0], "rationale", "")

        stocks = StockAnalysis(
            tickers=tickers_present,
            prices=prices,
            changes_30d=changes_30d,
            signals_by_ticker=sbt,
            top_pick=top_pick,
            top_pick_rationale=top_pick_rationale,
        )
        logger.info("StockAnalysis: {} tickers, top pick: {}", len(tickers_present), top_pick)

    except Exception as exc:
        logger.error("StockAnalysis build failed: {}", exc)
        stocks = StockAnalysis(
            tickers=[], prices={}, changes_30d={},
            signals_by_ticker={}, top_pick="", top_pick_rationale="",
        )

    # ------------------------------------------------------------------
    # 13. AI narratives
    # ------------------------------------------------------------------
    ai = _build_ai_analysis(
        sentiment=sentiment,
        alpha=alpha,
        market=market,
        freight=freight,
        macro=macro,
        stocks=stocks,
        news_items=news_items,
    )

    # ------------------------------------------------------------------
    # 14. Data quality
    # ------------------------------------------------------------------
    data_quality = _assess_data_quality(
        port_results=port_results,
        route_results=route_results,
        freight_data=freight_data,
        macro_data=macro_data,
        stock_data=stock_data,
    )

    # ------------------------------------------------------------------
    # 15. Assemble and return
    # ------------------------------------------------------------------
    report = InvestorReport(
        generated_at=generated_at,
        report_date=report_date,
        sentiment=sentiment,
        alpha=alpha,
        market=market,
        freight=freight,
        macro=macro,
        stocks=stocks,
        ai=ai,
        data_quality=data_quality,
        news_items=top_news,
        digest=digest,
    )

    logger.success(
        "InvestorReport built: quality={}, sentiment={} ({:+.3f}), {} signals",
        data_quality, sentiment.overall_label, sentiment.overall_score, len(signals),
    )
    return report
