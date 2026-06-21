"""Coherent, cascade-grounded Stress-VaR / Expected-Shortfall (rec R009).

The platform's existing risk path is dishonest in two ways the F-theme set out
to fix:

* ``risk_lab.portfolio_var`` runs on a ``stable_hash`` SYNTHETIC return panel —
  fixed-correlation noise, not the book's real covariance.
* ``risk_lab.stress_test_scenario`` reads HARDCODED per-ticker multipliers
  (``overlay_multiplier("ticker:ZIM.return")`` → 1.18) — the "stress" ignores
  the actual headline disruption the cascade just scored.

This module grounds the risk number in the REAL cascade and the REAL return
covariance:

1. :func:`cascade_return_shocks` maps each scored ``EquityIdea``
   (``direction`` × ``conviction_score``) to an expected return shock — the
   disruption's cascade-implied tilt on that name over the stress horizon.
2. :func:`monte_carlo_book_es` draws book P&L from a multivariate normal
   centered on that shock vector with covariance from the book's real cached
   returns (``book_pnl.returns_panel``; a labeled diagonal-vol fallback when
   prices are dark), then reports VaR + **Expected Shortfall** — a *coherent*
   (sub-additive) tail measure — with an exact, additive **component-ES**
   decomposition so the PM sees which names own the tail.

Honesty guarantees:
  * The shock direction + magnitude come from the cascade, not a constant.
  * The covariance is the book's real one when ≥2 names have history.
  * ``basis`` labels which path was taken ("real-cov" vs "diagonal-vol").
  * ``max_shock`` is an explicit scenario-severity scalar, not a precision
    claim; a full-conviction, full-severity idea implies a ``max_shock`` move.
  * ES decomposition is the Euler/marginal split — components sum to ES
    exactly (pinned by a test), so nothing is hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

_LONG = {"bullish", "long", "buy"}
_SHORT = {"bearish", "short", "sell"}

# A full-conviction, full-severity cascade idea implies this expected return
# move over the stress horizon. A scenario-severity scalar, NOT a forecast —
# the disruption shifts the name's mean by (dir × conviction × severity ×
# MAX_SHOCK); everything else is the real return dispersion.
_DEFAULT_MAX_SHOCK = 0.20

# Diagonal-vol fallback (per-DAY stdev) when the real covariance is
# unavailable. ~3.2%/day ≈ a high-beta shipping equity; labeled in ``basis``.
_FALLBACK_DAILY_VOL = 0.032


@dataclass
class StressVaR:
    """Coherent Stress-VaR / ES of the book under a cascade-grounded shock.

    ``var_pct`` and ``es_pct`` are SIGNED book returns in the lower tail:
    ``es_pct <= var_pct`` always (the mean of the k worst draws can't exceed the
    k-th worst). They are usually negative (a loss), but under a strongly
    net-bullish cascade even the (1-confidence) tail can be a GAIN, so they are
    NOT clamped to ≤ 0 and the dollars carry the sign — the UI labels loss vs
    gain rather than fabricating downside. ``component_es_pct`` sums to
    ``es_pct`` exactly (Euler decomposition) — each name's share of the tail.
    """

    scenario_name: str
    confidence: float
    horizon_days: int
    n_sims: int
    portfolio_value: float
    mean_pnl_pct: float                 # expected book P&L under the shock
    var_pct: float                      # signed (1-confidence) tail boundary
    es_pct: float                       # signed mean of the tail; <= var_pct
    var_dollar: float                   # signed (var_pct * portfolio_value)
    es_dollar: float                    # signed (es_pct * portfolio_value)
    n_names: int
    basis: str                          # "real-cov" | "diagonal-vol" | "empty"
    component_es_pct: dict = field(default_factory=dict)
    shocks_pct: dict = field(default_factory=dict)


def _dir_sign(direction: str) -> int:
    d = (direction or "").strip().lower()
    if d in _LONG:
        return 1
    if d in _SHORT:
        return -1
    return 0


def cascade_return_shocks(
    ideas,
    *,
    severity: float = 1.0,
    max_shock: float = _DEFAULT_MAX_SHOCK,
) -> dict:
    """Map scored ideas to expected return shocks: dir × conviction × severity × max_shock.

    Bullish → positive, Bearish → negative, Neutral / blank → dropped (no
    directional tilt). The magnitude is the idea's own ``conviction_score``
    (already in [0, 1]) scaled by the scenario ``severity`` and the
    ``max_shock`` ceiling. Last write wins on a duplicate ticker.
    """
    sev = max(0.0, float(severity))
    cap = abs(float(max_shock))
    out: dict[str, float] = {}
    for idea in ideas or []:
        ticker = str(getattr(idea, "ticker", "") or "")
        sign = _dir_sign(str(getattr(idea, "direction", "") or ""))
        if not ticker or sign == 0:
            continue
        conv = float(getattr(idea, "conviction_score", 0.0) or 0.0)
        conv = max(0.0, min(1.0, conv))
        out[ticker] = float(np.clip(sign * conv * sev * cap, -cap, cap))
    return out


def _covariance(stock_data, tickers, horizon_days: int):
    """Horizon-scaled covariance for ``tickers`` → (cov matrix, basis label).

    Real sample covariance from cached daily log-returns scaled by the horizon
    when ≥2 names have history; otherwise a diagonal fallback at a documented
    per-day vol (labeled "diagonal-vol"). Variance scales linearly in time
    under the iid assumption, so daily-cov × horizon_days.
    """
    h = max(1, int(horizon_days))
    n = len(tickers)
    try:
        from processing.book_pnl import returns_panel
        panel = returns_panel(stock_data, tickers)
    except Exception:
        panel = None

    if panel is not None and not panel.empty and panel.shape[1] >= 2:
        # Reindex to the requested ticker order; names absent from the panel
        # get the fallback variance on the diagonal and zero cross-covariance.
        cov = np.full((n, n), 0.0)
        present = [t for t in tickers if t in panel.columns]
        sub = panel[present].cov().to_numpy() * h
        idx = {t: i for i, t in enumerate(tickers)}
        for a, ta in enumerate(present):
            for b, tb in enumerate(present):
                cov[idx[ta], idx[tb]] = sub[a, b]
        fallback_var = (_FALLBACK_DAILY_VOL ** 2) * h
        for t in tickers:
            if t not in present:
                cov[idx[t], idx[t]] = fallback_var
        return cov, ("real-cov" if len(present) >= 2 else "diagonal-vol")

    # No usable history at all → diagonal fallback for every name.
    cov = np.eye(n) * (_FALLBACK_DAILY_VOL ** 2) * h
    return cov, "diagonal-vol"


def monte_carlo_book_es(
    weights: dict,
    ideas,
    stock_data,
    *,
    confidence: float = 0.95,
    horizon_days: int = 5,
    n_sims: int = 10_000,
    severity: float = 1.0,
    max_shock: float = _DEFAULT_MAX_SHOCK,
    portfolio_value: float = 1_000_000.0,
    scenario_name: str = "Cascade stress",
    seed: int = 0,
) -> StressVaR:
    """Monte-Carlo the book's P&L under the cascade-grounded shock → VaR + ES.

    Draws ``n_sims`` horizon returns ~ N(μ, Σ) where μ is the per-name cascade
    shock vector (:func:`cascade_return_shocks`; 0 for held names with no
    active idea — pure market risk) and Σ is the book's real horizon covariance
    (:func:`_covariance`). Book return per draw = Σ wᵢ·rᵢ. VaR is the
    (1-confidence) percentile loss; ES is the mean book return in that tail.

    Component-ES is the Euler split: componentᵢ = wᵢ · mean over the tail draws
    of rᵢ. The components sum to ``es_pct`` EXACTLY (each name's true share of
    the tail), so a single ES number never hides that several names share one
    bet. Returns a zeroed :class:`StressVaR` (basis="empty") for an empty book.
    """
    tickers = [str(t) for t in (weights or {})]
    n = len(tickers)
    conf = min(max(float(confidence), 0.50), 0.999)
    if n == 0:
        return StressVaR(
            scenario_name=scenario_name, confidence=conf,
            horizon_days=int(horizon_days), n_sims=0,
            portfolio_value=float(portfolio_value),
            mean_pnl_pct=0.0, var_pct=0.0, es_pct=0.0,
            var_dollar=0.0, es_dollar=0.0, n_names=0, basis="empty",
        )

    w = np.array([float(weights[t]) for t in tickers], dtype=float)
    shocks = cascade_return_shocks(ideas, severity=severity, max_shock=max_shock)
    mu = np.array([float(shocks.get(t, 0.0)) for t in tickers], dtype=float)
    cov, basis = _covariance(stock_data, tickers, horizon_days)

    rng = np.random.default_rng(int(seed))
    draws = rng.multivariate_normal(mu, cov, size=int(max(1, n_sims)))  # (S, n)
    book = draws @ w                                                    # (S,)

    # Tail = the k smallest book outcomes, k = round((1-conf)*S) (>= 1). Taking
    # an explicit k-set (not a percentile + <= mask) guarantees a non-empty tail
    # so the Euler identity holds EXACTLY by construction — no dead fallback that
    # could pair a nonzero ES with a zeroed component vector. VaR is the tail
    # boundary (the k-th smallest); ES is the mean of the k smallest.
    k = max(1, int(round((1.0 - conf) * draws.shape[0])))
    order = np.argsort(book)
    tail_idx = order[:k]
    var_pct = float(book[order[k - 1]])
    es_pct = float(book[tail_idx].mean())

    # Euler/marginal component ES: wᵢ · mean over the tail draws of rᵢ. Sums to
    # es_pct EXACTLY (sign-agnostic linearity).
    comp = w * draws[tail_idx].mean(axis=0)
    component_es_pct = {t: float(c) for t, c in zip(tickers, comp)}

    # Signed dollars: var_pct/es_pct are SIGNED book returns. Under a net-bullish
    # cascade the tail can be a GAIN (positive) — do NOT abs() it into a phantom
    # 'loss'. The UI labels the sign (loss vs gain); honesty over convention.
    pv = float(portfolio_value)
    return StressVaR(
        scenario_name=scenario_name, confidence=conf,
        horizon_days=int(horizon_days), n_sims=int(draws.shape[0]),
        portfolio_value=pv,
        mean_pnl_pct=float(book.mean()),
        var_pct=var_pct, es_pct=es_pct,
        var_dollar=var_pct * pv, es_dollar=es_pct * pv,
        n_names=n, basis=basis,
        component_es_pct=component_es_pct,
        shocks_pct={t: float(shocks.get(t, 0.0)) for t in tickers},
    )


# ── Per-driver tail attribution (rec R065) ──────────────────────────────────
# A single ES number hides that several names share ONE bet (e.g. three liners
# all long the same Suez chokepoint). Re-bucketing the exact per-name component
# ES by each name's dominant cascade driver tells the PM which DRIVER actually
# owns the tail. Because it only re-groups the per-name components (a partition
# of the book), the driver buckets still sum to ES exactly.

def idea_driver_map(ideas) -> dict:
    """``{ticker -> dominant_driver_key}`` from scored ideas (blank if absent)."""
    out: dict[str, str] = {}
    for idea in ideas or []:
        ticker = str(getattr(idea, "ticker", "") or "")
        if not ticker:
            continue
        out[ticker] = str(getattr(idea, "dominant_driver_key", "") or "")
    return out


def component_es_by_driver(
    component_es_pct: dict, ticker_driver: dict, *, default: str = "market"
) -> dict:
    """Re-bucket per-name component ES by cascade driver. Sums to ES exactly.

    Names with no driver (held but no active cascade idea — pure market risk)
    roll into ``default``. The result is a partition of ``component_es_pct``, so
    ``sum(result.values()) == sum(component_es_pct.values())`` always.
    """
    td = ticker_driver or {}
    out: dict[str, float] = {}
    for ticker, contrib in (component_es_pct or {}).items():
        driver = (str(td.get(ticker, "") or "").strip() or default)
        out[driver] = out.get(driver, 0.0) + float(contrib)
    return out
