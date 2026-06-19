"""processing/sized_book_risk.py — signed long/short VaR for a conviction book (R255).

``engine.position_sizer.size_from_conviction`` is the one place the platform
produces a *genuine* long/short book: a :class:`~engine.position_sizer.SizedBook`
whose per-name target weights are **signed** (Bullish → +, Bearish → −) with a
distinct ``gross_exposure`` (Σ|wᵢ|) and ``net_exposure`` (Σ wᵢ). But that
SizedBook is never handed to a risk function. Every existing risk path assumes a
LONG-ONLY convex book — ``processing.risk_lab.portfolio_var`` consumes a weights
dict but its real call sites feed it long-only weights, and the
``book_weights_detail`` path drops shorts entirely — so the one signed book the
platform can produce has no correct risk read.

This module is that read. It builds the book's historical return series with the
SIGNED weights **directly**:

    r_book,t = Σ_i w_i · r_i,t        (w_i signed; NO Σw = 1 renormalization)

so a short genuinely *nets* against a correlated long — a market-neutral pair
shows ~zero book VaR, the property a long-only renormalization can never express
(renormalizing to Σw = 1 would blow up a near-zero net book or silently re-sign
it). VaR + Expected-Shortfall are then taken on that one series via the same
``risk_lab`` historical / parametric estimators the rest of the platform uses, so
the percentile convention and the loss-negative sign match exactly.

Honesty-first
-------------
* The book return series is built from the REAL returns panel
  (``processing.book_pnl.returns_panel`` — look-ahead-free total-return
  log-returns). Nothing is fabricated: no ``rng.normal`` fallback.
* An empty book, an empty/None panel, or zero overlapping tickers yields an
  honest empty result (``basis="empty"``, zero VaR, zero observations) — never a
  made-up number and never a raise.
* ``basis`` stamps which path produced the read: ``"real"`` when an actual
  weighted series was scored, ``"empty"`` otherwise.

Purity
------
No Streamlit, no globals, no I/O, no randomness. Deterministic given the same
SizedBook + panel. Never raises on degenerate input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from processing.risk_lab import ewma_var, historical_var, parametric_var

VALID_METHODS: tuple[str, ...] = ("ewma", "historical", "parametric")


@dataclass(frozen=True)
class SizedBookRisk:
    """Signed long/short VaR / ES read for a conviction-sized book.

    ``var_pct`` / ``cvar_pct`` are *fractions* and **loss-negative** (≤ 0),
    matching :class:`processing.risk_lab.VaRResult` exactly — a 1-day 95% VaR of
    −0.018 means "lose 1.8% or worse 5% of the time". ``cvar_pct ≤ var_pct ≤ 0``.

    Exposure fields reconcile to the source SizedBook over the *scored* names:
      * ``gross_exposure``  = Σ|wᵢ|      (always ≥ 0)
      * ``net_exposure``    = Σ wᵢ       (signed; longs + shorts)
      * ``long_exposure``   = Σ wᵢ where wᵢ > 0   (≥ 0)
      * ``short_exposure``  = Σ wᵢ where wᵢ < 0   (signed, ≤ 0)
      so ``net = long + short`` and ``gross = long − short`` hold exactly.

    ``weights`` is the signed dict actually used to build the book series (only
    the names present in BOTH the book and the panel — the overlap that could be
    scored). ``basis`` is ``"real"`` when a real weighted series was scored, else
    ``"empty"``.
    """

    n_names: int
    gross_exposure: float
    net_exposure: float
    long_exposure: float
    short_exposure: float
    var_pct: float                       # ≤ 0  (loss-negative fraction)
    cvar_pct: float                      # ≤ var_pct ≤ 0
    n_observations: int
    basis: str                           # "real" | "empty"
    confidence: float = 0.95
    horizon_days: int = 1
    method: str = "historical"
    weights: dict[str, float] = field(default_factory=dict)
    provenance: str = ""


def _signed_weights(sized_book) -> dict[str, float]:
    """Pull the signed ``{ticker: weight}`` dict off a SizedBook-like object.

    Prefers the canonical ``SizedBook.weights()`` method; falls back to reading
    ``positions[*].target_weight`` and finally to a plain ``weights`` mapping —
    so a hand-built stand-in works too. Non-finite / zero weights are dropped
    (a zero-weight name contributes nothing and would only pad ``n_names``).
    """
    raw: dict = {}
    if sized_book is None:
        return {}
    wfn = getattr(sized_book, "weights", None)
    if callable(wfn):
        try:
            raw = dict(wfn() or {})
        except Exception:
            raw = {}
    if not raw:
        positions = getattr(sized_book, "positions", None)
        if positions:
            for p in positions:
                t = getattr(p, "ticker", None)
                w = getattr(p, "target_weight", None)
                if t is not None and w is not None:
                    raw[str(t)] = w
    if not raw and isinstance(getattr(sized_book, "weights", None), dict):
        raw = dict(sized_book.weights)
    out: dict[str, float] = {}
    for t, w in raw.items():
        try:
            wf = float(w)
        except (TypeError, ValueError):
            continue
        if math.isfinite(wf) and wf != 0.0:
            out[str(t)] = wf
    return out


def _empty(
    weights: dict[str, float],
    *,
    confidence: float,
    horizon_days: int,
    method: str,
    note: str,
) -> SizedBookRisk:
    return SizedBookRisk(
        n_names=0,
        gross_exposure=0.0,
        net_exposure=0.0,
        long_exposure=0.0,
        short_exposure=0.0,
        var_pct=0.0,
        cvar_pct=0.0,
        n_observations=0,
        basis="empty",
        confidence=float(confidence),
        horizon_days=int(horizon_days),
        method=method,
        weights=dict(weights),
        provenance=note,
    )


def sized_book_var(
    sized_book,
    returns_panel: pd.DataFrame,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    method: str = "ewma",
) -> SizedBookRisk:
    """Signed long/short VaR + Expected-Shortfall for a conviction-sized book.

    Builds the book's historical return SERIES with the SIGNED target weights
    **directly** — ``r_book,t = Σ_i w_i · r_{i,t}`` over the signed ``w_i``, with
    NO ``Σw = 1`` renormalization — so shorts net against correlated longs. The
    chosen VaR/ES estimator from :mod:`processing.risk_lab` is then run on that
    one series (so the percentile convention, the loss-negative sign, and the
    ``√horizon`` scaling are identical to every other VaR in the platform).

    Parameters
    ----------
    sized_book:
        A :class:`engine.position_sizer.SizedBook` (or any object exposing a
        signed ``weights()`` dict / ``positions[*].target_weight``). ``None`` /
        empty → honest empty result.
    returns_panel:
        Daily-returns ``DataFrame`` indexed by date, one column per ticker —
        intended to be ``processing.book_pnl.returns_panel`` over the book's
        tickers (REAL, look-ahead-free total-return log-returns). ``None`` /
        empty / no overlap with the book → honest empty result (no fabricated
        returns).
    confidence:
        VaR confidence (default 0.95 → 5% tail).
    horizon_days:
        Holding horizon; the 1-day figure is scaled by ``√horizon_days`` exactly
        as ``risk_lab`` does.
    method:
        ``"historical"`` (empirical percentile, default) or ``"parametric"``
        (delta-normal). Any other value falls back to ``"historical"``.

    Returns
    -------
    SizedBookRisk
        ``basis="real"`` with signed ``var_pct``/``cvar_pct`` when a real
        weighted series was scored; ``basis="empty"`` (zeros) otherwise.
    """
    method = method if method in VALID_METHODS else "ewma"
    weights = _signed_weights(sized_book)

    if not weights:
        return _empty(
            weights,
            confidence=confidence, horizon_days=horizon_days, method=method,
            note="Empty book — no signed weights to risk. Honest empty (no fabricated VaR).",
        )

    if (
        returns_panel is None
        or not isinstance(returns_panel, pd.DataFrame)
        or returns_panel.empty
    ):
        return _empty(
            weights,
            confidence=confidence, horizon_days=horizon_days, method=method,
            note="No real returns panel — cannot risk the signed book. Honest empty (no fabricated VaR).",
        )

    # Overlap: only names present in BOTH the book and the panel can be scored.
    common = [c for c in returns_panel.columns if c in weights]
    if not common:
        return _empty(
            weights,
            confidence=confidence, horizon_days=horizon_days, method=method,
            note="No book ticker overlaps the returns panel. Honest empty (no fabricated VaR).",
        )

    used = {t: float(weights[t]) for t in common}
    sub = returns_panel[common].apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if sub.empty:
        return _empty(
            used,
            confidence=confidence, horizon_days=horizon_days, method=method,
            note="No overlapping observations in the panel. Honest empty (no fabricated VaR).",
        )

    # SIGNED weighted book return series — the property long-only can't express.
    # No Σw = 1 renormalization: a short must net against a correlated long.
    w_vec = np.array([used[c] for c in common], dtype=float)
    book_returns = pd.Series(sub.to_numpy() @ w_vec, index=sub.index)

    estimator = {"parametric": parametric_var,
                 "ewma": ewma_var}.get(method, historical_var)
    var = estimator(
        book_returns,
        confidence=confidence,
        horizon_days=horizon_days,
        portfolio_value=0.0,
    )

    gross = float(sum(abs(w) for w in used.values()))
    net = float(sum(used.values()))
    long_exp = float(sum(w for w in used.values() if w > 0.0))
    short_exp = float(sum(w for w in used.values() if w < 0.0))

    # risk_lab returns an EMPTY VaRResult (zeros, n_observations=0) when the
    # series is too short / degenerate. Honour that: stamp "empty" so we never
    # report a zero VaR as if it were a real measured number.
    basis = "real" if var.n_observations > 0 else "empty"
    note = (
        f"Signed long/short {method} VaR on a REAL look-ahead-free returns panel "
        f"({len(common)} of {len(weights)} book names scored over {var.n_observations} obs); "
        f"book return series built with SIGNED weights (shorts net against longs, "
        f"no Σw=1 renormalization), {confidence:.0%} confidence, {horizon_days}d horizon. "
        "Not investment advice, not a price target."
        if basis == "real" else
        "Overlap too short / degenerate for a VaR estimate. Honest empty (no fabricated VaR)."
    )

    return SizedBookRisk(
        n_names=len(common),
        gross_exposure=round(gross, 6),
        net_exposure=round(net, 6),
        long_exposure=round(long_exp, 6),
        short_exposure=round(short_exp, 6),
        var_pct=var.var_pct,
        cvar_pct=var.cvar_pct,
        n_observations=var.n_observations,
        basis=basis,
        confidence=float(confidence),
        horizon_days=int(horizon_days),
        method=method,
        weights=used,
        provenance=note,
    )


__all__ = ["SizedBookRisk", "sized_book_var", "VALID_METHODS"]
