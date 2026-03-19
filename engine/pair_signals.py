from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

try:
    from scipy.stats import pearsonr as _pearsonr
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False
    logger.warning("scipy not available; pair signal correlation disabled")


# ---------------------------------------------------------------------------
# Interesting shipping stock pairs to trade as spreads
# ---------------------------------------------------------------------------

SHIPPING_PAIRS: list[tuple[str, str]] = [
    ("ZIM", "MATX"),
    ("ZIM", "SBLK"),
    ("MATX", "DAC"),
    ("SBLK", "DAC"),
    ("ZIM", "CMRE"),
    ("MATX", "SBLK"),
    ("DAC", "CMRE"),
    ("ZIM", "DAC"),
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class PairSignal:
    """Pair-trading / spread-analysis result for two shipping stocks."""

    stock_a: str               # e.g. "ZIM"
    stock_b: str               # e.g. "MATX"
    spread_zscore: float       # z-score of current A/B log-price spread vs 90d mean
    spread_direction: str      # "LONG_A_SHORT_B" | "LONG_B_SHORT_A" | "NEUTRAL"
    mean_reversion_signal: str # "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL"
    correlation_90d: float     # Pearson r between 90d daily returns
    half_life_days: float      # Ornstein-Uhlenbeck mean-reversion half-life
    signal_strength: float     # [0, 1]
    trade_hypothesis: str      # human-readable explanation
    entry_triggered: bool      # True if |zscore| > 1.5 AND correlation > 0.4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _estimate_half_life(spread: pd.Series) -> float:
    """Estimate OU mean-reversion half-life via AR(1) regression on the spread.

    Fits: spread[t] = alpha + beta * spread[t-1] + epsilon
    Half-life = -log(2) / log(beta), clamped to [1, 90] days.
    Returns 45 (neutral) if estimation fails.
    """
    if len(spread) < 10:
        return 45.0

    y = spread.iloc[1:].values
    x = spread.iloc[:-1].values

    # OLS via closed-form to avoid sklearn dependency
    x_mean = x.mean()
    y_mean = y.mean()
    beta_num = np.sum((x - x_mean) * (y - y_mean))
    beta_den = np.sum((x - x_mean) ** 2)

    if beta_den == 0:
        return 45.0

    beta = beta_num / beta_den

    # beta must be in (0, 1) for mean reversion; negative or >= 1 = no reversion
    if beta <= 0 or beta >= 1:
        return 90.0

    try:
        hl = -math.log(2) / math.log(beta)
    except (ValueError, ZeroDivisionError):
        return 45.0

    return float(np.clip(hl, 1.0, 90.0))


def _zscore_to_strength(zscore: float) -> float:
    """Map |zscore| to a [0, 1] signal strength using a soft sigmoid."""
    abs_z = abs(zscore)
    # 0 at z=0, ~0.5 at z=1.5, ~1.0 at z=3+
    return float(min(abs_z / 3.0, 1.0))


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_pair(
    stock_a: str,
    stock_b: str,
    stock_data: dict[str, pd.DataFrame],
) -> PairSignal | None:
    """Compute spread z-score, correlation, and mean-reversion signal for a pair.

    Args:
        stock_a: Ticker for leg A (e.g. "ZIM").
        stock_b: Ticker for leg B (e.g. "MATX").
        stock_data: Mapping of ticker -> DataFrame with columns [date, close].

    Returns:
        PairSignal, or None if insufficient data for either stock.
    """
    df_a = stock_data.get(stock_a)
    df_b = stock_data.get(stock_b)

    if df_a is None or df_b is None or df_a.empty or df_b.empty:
        logger.debug(f"Pair {stock_a}/{stock_b}: missing data for one or both legs")
        return None

    try:
        s_a = df_a.set_index("date")["close"].sort_index()
        s_b = df_b.set_index("date")["close"].sort_index()
    except KeyError:
        logger.debug(f"Pair {stock_a}/{stock_b}: DataFrame missing 'date' or 'close' column")
        return None

    # Align on common dates and drop NaN
    combined = pd.concat([s_a, s_b], axis=1, keys=[stock_a, stock_b]).dropna()

    if len(combined) < 20:
        logger.debug(f"Pair {stock_a}/{stock_b}: only {len(combined)} common dates, skipping")
        return None

    price_a = combined[stock_a]
    price_b = combined[stock_b]

    # Log-price spread
    spread = np.log(price_a) - np.log(price_b)

    # 90-day rolling statistics (or full history if shorter)
    window = min(90, len(spread))
    spread_window = spread.iloc[-window:]
    mean_90d = spread_window.mean()
    std_90d = spread_window.std()

    current_spread = float(spread.iloc[-1])
    zscore = (current_spread - mean_90d) / std_90d if std_90d > 0 else 0.0

    # Daily returns for correlation (90-day window)
    ret_a = price_a.pct_change().dropna()
    ret_b = price_b.pct_change().dropna()
    ret_combined = pd.concat([ret_a, ret_b], axis=1, keys=[stock_a, stock_b]).dropna()
    ret_window = ret_combined.iloc[-window:]

    if _SCIPY_AVAILABLE and len(ret_window) >= 10:
        try:
            corr_r, _ = _pearsonr(ret_window[stock_a], ret_window[stock_b])
            correlation_90d = float(np.clip(corr_r, -1.0, 1.0))
        except Exception as exc:
            logger.debug(f"Pair {stock_a}/{stock_b}: pearsonr failed — {exc}")
            correlation_90d = 0.0
    else:
        correlation_90d = float(ret_window[stock_a].corr(ret_window[stock_b])) if len(ret_window) >= 5 else 0.0

    # Half-life via AR(1) on the spread series
    half_life_days = _estimate_half_life(spread)

    # Trade direction
    #   zscore > 0  → A is relatively overpriced → short A, long B
    #   zscore < 0  → B is relatively overpriced → long A, short B
    if zscore > 0.1:
        spread_direction = "LONG_B_SHORT_A"
        mean_reversion_signal = "OVERBOUGHT"
    elif zscore < -0.1:
        spread_direction = "LONG_A_SHORT_B"
        mean_reversion_signal = "OVERSOLD"
    else:
        spread_direction = "NEUTRAL"
        mean_reversion_signal = "NEUTRAL"

    # Entry trigger: meaningful z-score AND stocks are correlated enough
    entry_triggered = abs(zscore) > 1.5 and correlation_90d > 0.4

    signal_strength = _zscore_to_strength(zscore)

    # Human-readable hypothesis
    if spread_direction == "LONG_B_SHORT_A":
        action = f"{stock_a} is trading rich vs {stock_b} (z={zscore:+.2f}); " \
                 f"mean reversion favors long {stock_b}, short {stock_a}."
    elif spread_direction == "LONG_A_SHORT_B":
        action = f"{stock_b} is trading rich vs {stock_a} (z={zscore:+.2f}); " \
                 f"mean reversion favors long {stock_a}, short {stock_b}."
    else:
        action = f"{stock_a}/{stock_b} spread is near its 90-day mean (z={zscore:+.2f}); no clear edge."

    corr_note = f"90d correlation r={correlation_90d:.2f}, OU half-life ~{half_life_days:.1f}d."
    trade_hypothesis = f"{action} {corr_note}"

    return PairSignal(
        stock_a=stock_a,
        stock_b=stock_b,
        spread_zscore=round(zscore, 4),
        spread_direction=spread_direction,
        mean_reversion_signal=mean_reversion_signal,
        correlation_90d=round(correlation_90d, 4),
        half_life_days=round(half_life_days, 2),
        signal_strength=round(signal_strength, 4),
        trade_hypothesis=trade_hypothesis,
        entry_triggered=entry_triggered,
    )


# ---------------------------------------------------------------------------
# Portfolio-level helpers
# ---------------------------------------------------------------------------

def analyze_all_pairs(
    stock_data: dict[str, pd.DataFrame],
) -> list[PairSignal]:
    """Analyze all SHIPPING_PAIRS and return results sorted by |spread_zscore| desc."""
    results: list[PairSignal] = []

    for stock_a, stock_b in SHIPPING_PAIRS:
        signal = analyze_pair(stock_a, stock_b, stock_data)
        if signal is not None:
            results.append(signal)

    results.sort(key=lambda s: abs(s.spread_zscore), reverse=True)

    triggered = sum(1 for s in results if s.entry_triggered)
    logger.info(
        f"Pair analysis: {len(results)}/{len(SHIPPING_PAIRS)} pairs analyzed, "
        f"{triggered} entry signals triggered"
    )

    return results


def get_active_pair_trades(signals: list[PairSignal]) -> list[PairSignal]:
    """Return only pairs where entry_triggered is True."""
    return [s for s in signals if s.entry_triggered]
