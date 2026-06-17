"""Live-book risk on the PERSISTED position ledger (rec R125).

The OMS-risk-desk view: take the user's DURABLE book (``state.positions`` — not
the volatile ``st.session_state`` list, and not a hardcoded equal-weight
shipping universe) and run the two questions a risk desk asks every morning:

  1. **Book VaR / CVaR** — ``risk_lab.portfolio_var`` over the book's REAL
     marked weights × the REAL look-ahead-free daily-returns panel
     (``book_pnl.returns_panel``, R127 total-return basis).
  2. **Scenario stress** — ``risk_lab.stress_test_all_scenarios(weights)``:
     every BUILTIN scenario in ``state.scenarios`` applied to the LIVE book,
     per-scenario P&L sorted worst-loss-first.

What already exists (and is NOT re-done here):

  - ``tab_portfolio._render_risk_metrics`` (R008) already overlays the SESSION
    book onto the real returns panel for VaR/Sharpe/MaxDD/BDI-corr.
  - ``tab_risk_lab`` (R008/R009) already runs ``portfolio_var`` +
    ``stress_test_all_scenarios`` + coherent Stress-VaR/ES — but against a
    hardcoded equal-weight universe, NOT the user's actual book.

The genuine gap this fills: the **persisted** book driving the risk, plus
``stress_test_all_scenarios`` wired against that live book on the Portfolio tab.

HONESTY: weights are REAL marked market values (``book_exposure.book_weights``);
returns are the REAL panel. An empty book, an unrecognised user, or dark prices
yield an honest empty result (``is_real=False`` / ``var=None`` / ``[]``) — never
an ``rng.normal`` fabrication and never a fabricated price. Pure module: no
streamlit, no globals; the UI layer renders the dataclass this returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from processing.book_exposure import book_weights_detail
from processing.book_pnl import mark_book, returns_panel
from processing.risk_lab import (
    VaRResult,
    ScenarioStressResult,
    portfolio_var,
    stress_test_all_scenarios,
)
from processing.stress_var import StressVaR, monte_carlo_book_es

__all__ = [
    "PersistedBookRisk",
    "load_persisted_positions",
    "persisted_book_risk",
    "persisted_book_stress_var",
]


@dataclass(frozen=True)
class PersistedBookRisk:
    """The live-book risk read for one user's persisted book.

    ``var`` / ``cvar`` are ``None`` when no real returns panel could be built
    (empty book or dark prices) — the caller renders an honest empty-state
    rather than a fabricated number. ``scenarios`` is the per-BUILTIN-scenario
    P&L on the live book (already worst-loss-first from risk_lab), and is empty
    only when there are no weights to stress.
    """

    n_positions: int                       # rows in the persisted book
    n_priced: int                          # lots with a real mark
    weights: dict                          # ticker -> book weight [0, 1]
    weights_are_real: bool                 # True = marked MV; False = equal-wt fallback
    market_value: float                    # priced market value (sizes the VaR $)
    panel_tickers: list                    # tickers that survived into the returns panel
    panel_obs: int                         # observations in the returns panel
    panel_is_real: bool                    # True = real returns panel built
    var: Optional[VaRResult]               # book VaR/CVaR, or None when no panel
    scenarios: list = field(default_factory=list)  # list[ScenarioStressResult]


def load_persisted_positions(user_id: Optional[str]) -> list[dict]:
    """Load the user's currently-open persisted positions (``closed_at IS NULL``).

    Returns ``[]`` for a falsy ``user_id`` (the session-less CLI/demo path) or
    on any storage error — the caller falls through to an honest empty-state.
    Each dict carries the legacy session-state shape so it is a drop-in for the
    rest of the book pipeline.
    """
    if not user_id:
        return []
    try:
        from state import positions as pos_store

        return pos_store.load_positions(user_id)
    except Exception:
        from loguru import logger

        logger.debug("persisted_book_risk: load_positions failed")
        return []


def persisted_book_risk(
    positions: list[dict],
    stock_data,
    *,
    confidence: float = 0.95,
    horizon_days: int = 1,
    method: str = "historical",
) -> PersistedBookRisk:
    """Bridge the persisted book into ``portfolio_var`` + ``stress_test_all_scenarios``.

    Builds REAL marked weights (``book_exposure.book_weights_detail``) and the
    REAL daily-returns panel (``book_pnl.returns_panel``), then:

      • runs ``portfolio_var`` sized by the book's priced market value, and
      • runs ``stress_test_all_scenarios(weights)`` — every BUILTIN scenario's
        impact on the live book.

    ``positions`` is the persisted book (see :func:`load_persisted_positions`).
    An empty book → an honest empty :class:`PersistedBookRisk` (``var=None``,
    ``scenarios=[]``). Dark prices → equal-weight scenario stress (still real
    scenario shocks) but ``var=None`` since no return panel exists. Pure +
    deterministic.
    """
    if not positions:
        return PersistedBookRisk(
            n_positions=0, n_priced=0, weights={}, weights_are_real=False,
            market_value=0.0, panel_tickers=[], panel_obs=0, panel_is_real=False,
            var=None, scenarios=[],
        )

    # ── Real marked weights (equal-weight fallback only when prices are dark). ─
    weights, weights_are_real = book_weights_detail(positions, stock_data)
    bm = mark_book(positions, stock_data)
    market_value = float(getattr(bm, "market_value", 0.0) or 0.0)
    n_priced = int(getattr(bm, "n_priced", 0) or 0)

    if not weights:
        # Book with no resolvable tickers — nothing to weight or stress.
        return PersistedBookRisk(
            n_positions=len(positions), n_priced=n_priced, weights={},
            weights_are_real=False, market_value=market_value,
            panel_tickers=[], panel_obs=0, panel_is_real=False,
            var=None, scenarios=[],
        )

    # ── Real returns panel over the book's tickers (drops thin names). ────────
    panel = returns_panel(stock_data or {}, list(weights.keys()))
    panel_is_real = not panel.empty
    var: Optional[VaRResult] = None
    panel_tickers: list[str] = []
    panel_obs = 0
    if panel_is_real:
        panel_tickers = [c for c in panel.columns if c in weights]
        panel_obs = int(len(panel))
        # Restrict + renormalise weights over the surviving panel columns so the
        # VaR is a genuine convex book (Σw = 1), matching the R008 convention.
        w_sub = {t: float(weights[t]) for t in panel_tickers}
        s = sum(w_sub.values())
        if s > 0:
            w_sub = {t: v / s for t, v in w_sub.items()}
            # Size the dollar VaR by the priced market value (fall back to a
            # nominal $1 so var_pct is still meaningful when prices are partial).
            pv = market_value if market_value > 0 else 1.0
            var = portfolio_var(
                panel, w_sub,
                confidence=confidence, horizon_days=horizon_days,
                portfolio_value=pv, method=method,
            )

    # ── Per-BUILTIN-scenario stress on the live book (real scenario engine). ──
    # Size the stress by priced MV when available, else a $1M nominal so the
    # %-impact column is still correct (it is weight-driven, not value-driven).
    stress_pv = market_value if market_value > 0 else 1_000_000.0
    scenarios: list[ScenarioStressResult] = stress_test_all_scenarios(
        weights, portfolio_value=stress_pv,
    )

    return PersistedBookRisk(
        n_positions=len(positions), n_priced=n_priced, weights=weights,
        weights_are_real=weights_are_real, market_value=market_value,
        panel_tickers=panel_tickers, panel_obs=panel_obs,
        panel_is_real=panel_is_real, var=var, scenarios=scenarios,
    )


def persisted_book_stress_var(
    positions: list[dict],
    ideas,
    stock_data,
    *,
    confidence: float = 0.95,
    horizon_days: int = 5,
    n_sims: int = 10_000,
    severity: float = 1.0,
    seed: int = 0,
) -> Optional[StressVaR]:
    """Cascade-grounded Monte-Carlo Stress-VaR/ES on the PERSISTED book.

    The platform's coherent, cascade-grounded Stress-VaR/ES
    (:func:`processing.stress_var.monte_carlo_book_es`, R009) is otherwise run
    only on a hardcoded equal-weight universe (the Risk Lab tab), while the
    user's real book sees only the constant-multiplier scenario stress in
    :func:`persisted_book_risk`. This bridges the two halves that never met: it
    feeds the DURABLE book's REAL marked weights (``book_exposure.book_weights_detail``,
    sized by the priced market value) into ``monte_carlo_book_es`` under the REAL
    live disruption carried by the cascade ``ideas`` — so the user's actual book
    is finally stressed by the live shock, with exact per-name Euler component-ES
    attribution.

    ``ideas`` are the scored cascade equity ideas (the same objects the Book-vs-
    Cascade overlay uses); a held name with no active idea contributes pure market
    risk (zero shock). Returns ``None`` for an empty / unresolvable book (honest
    empty-state). Weights are REAL marked market values; dark prices fall back to
    the StressVaR engine's published diagonal-vol basis (never a fabricated
    price). Pure + deterministic (fixed ``seed``).
    """
    if not positions:
        return None
    weights, _weights_are_real = book_weights_detail(positions, stock_data)
    if not weights:
        return None

    # Size the dollar tail by the book's priced market value (nominal $1M only
    # when prices are dark, so the %-tail is still meaningful).
    bm = mark_book(positions, stock_data)
    market_value = float(getattr(bm, "market_value", 0.0) or 0.0)
    pv = market_value if market_value > 0 else 1_000_000.0

    return monte_carlo_book_es(
        weights, ideas, stock_data,
        confidence=confidence, horizon_days=horizon_days, n_sims=n_sims,
        severity=severity, portfolio_value=pv, seed=seed,
        scenario_name="Cascade stress (persisted book)",
    )
