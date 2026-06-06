"""Regime-conditional evaluation — does the edge survive in DISRUPTED regimes?

Models that look good on the full sample routinely degrade around the regimes
that matter most — wars, canal closures, weather shocks, strikes. Reporting a
single full-sample Sharpe hides that. This splits a realized return series by
regime and reports per-regime Sharpe / hit-rate, so a strategy that only works
in calm tape is exposed.

The regime-label series is supplied by the caller (SSI stress, a vol proxy, a
canal-status series, …); ``volatility_regime`` derives a high/low-volatility
label from the returns themselves as a self-contained default. Honest: it is a
descriptive split of REAL returns — no fabrication, no look-ahead beyond the
rolling window the caller chooses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


def volatility_regime(returns, *, window: int = 21, quantile: float = 0.7) -> pd.Series:
    """Label each period ``"high-vol"`` / ``"low-vol"`` by whether its trailing
    rolling-std is at/above the series' ``quantile`` of that rolling-std.

    A simple, self-contained stress proxy when the caller has no external regime
    series. Periods before the window fills are dropped (NaN rolling-std).
    """
    r = pd.Series(returns).astype(float)
    vol = r.rolling(int(window)).std()
    thresh = vol.quantile(quantile)
    labels = vol.apply(
        lambda v: ("high-vol" if (np.isfinite(v) and v >= thresh) else "low-vol")
        if np.isfinite(v) else np.nan)
    return labels


@dataclass(frozen=True)
class RegimeStats:
    """Per-regime return stats."""

    regime: str
    n: int
    mean_pct: float
    sharpe: float
    hit_rate: float


def evaluate_by_regime(
    returns,
    regime_labels,
    *,
    periods_per_year: int = 252,
    rf: float = 0.0,
) -> dict:
    """Split ``returns`` by aligned ``regime_labels`` → per-regime stats.

    Returns ``{regime: RegimeStats}``. Inner-joins + drops NaNs so a label series
    shorter than the returns (e.g. a rolling regime) aligns cleanly.
    """
    r = pd.Series(returns).astype(float)
    g = pd.Series(regime_labels)
    aligned = pd.concat([r.rename("ret"), g.rename("regime")], axis=1, join="inner").dropna()
    out: dict[str, RegimeStats] = {}
    if aligned.empty:
        return out
    for regime, sub in aligned.groupby("regime"):
        x = sub["ret"].to_numpy()
        n = int(len(x))
        if n == 0:
            continue
        mean = float(x.mean())
        std = float(x.std(ddof=0))
        sharpe = (mean - rf) / std * math.sqrt(periods_per_year) if std > 0 else 0.0
        out[str(regime)] = RegimeStats(
            regime=str(regime), n=n,
            mean_pct=round(mean * 100.0, 4),
            sharpe=round(sharpe, 4),
            hit_rate=round(float((x > 0).mean()), 4),
        )
    return out


def survives_in_stress(
    returns,
    regime_labels=None,
    *,
    stress_regimes=("high-vol",),
    min_sharpe: float = 0.0,
    periods_per_year: int = 252,
    rf: float = 0.0,
) -> dict:
    """Does the edge clear ``min_sharpe`` in EVERY stress regime present?

    ``regime_labels=None`` derives a volatility regime from the returns. Returns
    ``{"by_regime": {...}, "survives_in_stress": bool, "verdict": str}``.
    """
    if regime_labels is None:
        regime_labels = volatility_regime(returns)
    stats = evaluate_by_regime(
        returns, regime_labels, periods_per_year=periods_per_year, rf=rf)
    stressed = [s for name, s in stats.items() if name in stress_regimes]
    survives = bool(stressed) and all(s.sharpe > min_sharpe for s in stressed)
    if not stressed:
        verdict = "No stress-regime periods observed — cannot assess survival."
    elif survives:
        verdict = (
            "Edge survives the stress regime(s): "
            + ", ".join(f"{s.regime} Sharpe {s.sharpe:+.2f}" for s in stressed) + ".")
    else:
        verdict = (
            "Edge does NOT clear in stress: "
            + ", ".join(f"{s.regime} Sharpe {s.sharpe:+.2f}" for s in stressed)
            + " — likely a calm-tape strategy.")
    return {"by_regime": stats, "survives_in_stress": survives, "verdict": verdict}
