"""Overlay the user's book onto the disruption cascade + commodity exposure (R008).

The Portfolio tab never touched the research engine: its VaR/Sharpe/MaxDD/BDI-corr
were pure ``rng.normal`` and its positions were never related to the cascade that
drives the rest of the platform. This pure module is the research-to-PM bridge —
it joins the marked book (``processing.book_pnl.mark_book``) to:

- per-company commodity exposure (``exposure_matrix.company_commodity_weights``),
- the per-name cascade ideas (``disruption_cascade.score_equity_ideas`` — passed
  IN as ``ideas`` so this module stays pure and cheap; it does not run the heavy
  stress chain itself),
- book concentration (HHI / effective-N).

HONESTY: weights come from REAL marked market values; if the book is entirely
unpriced we fall back to an equal weight and flag it (``is_real=False``) rather
than fabricate prices. Uncovered names (no cascade idea) are COUNTED, never
back-filled with a fake Neutral. Coverage is weight-based, so a tiny uncovered
lot doesn't read as a big blind spot. An empty book yields honest emptiness
(``{}`` / zeros), not an even split.
"""
from __future__ import annotations

from dataclasses import dataclass

from processing.book_pnl import mark_book
from processing.cargo_analyzer import HS_CATEGORIES
from processing.exposure_matrix import company_commodity_weights

__all__ = [
    "BookNameCascade",
    "BookCascadeOverlay",
    "book_weights",
    "book_weights_detail",
    "book_commodity_exposure",
    "book_cascade_overlay",
    "book_concentration",
]


@dataclass(frozen=True)
class BookNameCascade:
    """One book name's cascade contribution."""

    ticker: str
    weight: float                 # book weight [0, 1]
    direction: str                # "Bullish" | "Bearish" | "Neutral"
    conviction_score: float       # [0, 1]
    cascade_contribution: float   # the idea's own cascade magnitude
    weighted_cascade: float       # weight × cascade_contribution


@dataclass(frozen=True)
class BookCascadeOverlay:
    """The book's net read against the cascade."""

    names: list                   # list[BookNameCascade], covered names only
    net_tilt: str                 # "Bullish" | "Bearish" | "Neutral"
    bullish_wt: float             # book weight tilted bullish
    bearish_wt: float             # book weight tilted bearish
    weighted_conviction: float    # Σ weight × conviction over covered names
    coverage: float               # covered weight / total weight [0, 1]
    n_covered: int
    n_uncovered: int
    top_contributors: list        # top BookNameCascade by |weighted_cascade|


def book_weights_detail(positions, stock_data) -> tuple[dict, bool]:
    """Return ``(weights, is_real)``.

    ``is_real=True`` weights are priced market-value shares that sum to 1.0. When
    the whole book is unpriced (prices dark) we fall back to an equal weight over
    the book's tickers and ``is_real=False`` — never a fabricated price.
    """
    bm = mark_book(positions, stock_data)
    # Aggregate market value BY TICKER — a book may hold several lots of the same
    # name, and a naive {p.ticker: ...} dict would collapse them (weights would
    # then sum to < 1.0 and the book would be silently mis-scaled).
    mv_by_ticker: dict[str, float] = {}
    for p in getattr(bm, "positions", []):
        if getattr(p, "priced", False) and getattr(p, "market_value", 0.0) > 0:
            mv_by_ticker[p.ticker] = mv_by_ticker.get(p.ticker, 0.0) + float(p.market_value)
    mv = float(getattr(bm, "market_value", 0.0) or 0.0)
    if mv_by_ticker and mv > 0:
        return ({t: v / mv for t, v in mv_by_ticker.items()}, True)
    tickers = sorted({getattr(p, "ticker", None) for p in getattr(bm, "positions", [])
                      if getattr(p, "ticker", None)})
    if not tickers:
        return ({}, False)
    w = 1.0 / len(tickers)
    return ({t: w for t in tickers}, False)


def book_weights(positions, stock_data) -> dict:
    """Book weights by real marked market value (equal-weight fallback when dark)."""
    return book_weights_detail(positions, stock_data)[0]


def book_commodity_exposure(positions, stock_data) -> dict:
    """Book-weighted commodity (HS-category) exposure vector.

    Σ_name weight × company_commodity_weights(name); since each company vector and
    the weights both sum to ~1.0, the result sums to ~1.0. Empty book → ``{}``.
    """
    weights = book_weights(positions, stock_data)
    if not weights:
        return {}
    agg = {cat: 0.0 for cat in HS_CATEGORIES}
    for ticker, wt in weights.items():
        for cat, v in (company_commodity_weights(ticker) or {}).items():
            agg[cat] = agg.get(cat, 0.0) + wt * float(v)
    return {k: round(v, 4) for k, v in agg.items() if v > 1e-9}


def book_cascade_overlay(positions, ideas, stock_data) -> BookCascadeOverlay:
    """Overlay the book's weights onto the cascade ideas.

    ``ideas`` is the output of ``disruption_cascade.score_equity_ideas`` (passed
    in to keep this module pure). A name with no idea is counted as uncovered —
    never back-filled with a fake Neutral. Net tilt is the sign of
    (bullish_wt − bearish_wt) with a small dead-band.
    """
    weights, _ = book_weights_detail(positions, stock_data)
    idea_by_ticker = {getattr(i, "ticker", None): i for i in (ideas or [])}
    total_wt = sum(weights.values())

    names: list[BookNameCascade] = []
    bullish_wt = bearish_wt = weighted_conviction = covered_wt = 0.0
    n_covered = n_uncovered = 0
    for ticker, wt in weights.items():
        idea = idea_by_ticker.get(ticker)
        if idea is None:
            n_uncovered += 1
            continue
        n_covered += 1
        covered_wt += wt
        chain = getattr(idea, "cascade_chain", None) or []
        conviction = float(getattr(idea, "conviction_score", 0.0) or 0.0)
        # Cascade magnitude = Σ link contributions; fall back to conviction when
        # the chain is empty (a macro-only idea with no route hops).
        mag = (sum(float(getattr(link, "contribution", 0.0) or 0.0) for link in chain)
               if chain else conviction)
        direction = str(getattr(idea, "direction", "Neutral") or "Neutral")
        dlow = direction.lower()
        if dlow.startswith("bull"):
            bullish_wt += wt
        elif dlow.startswith("bear"):
            bearish_wt += wt
        weighted_conviction += wt * conviction
        names.append(BookNameCascade(
            ticker=ticker, weight=round(wt, 4), direction=direction,
            conviction_score=round(conviction, 4),
            cascade_contribution=round(mag, 4), weighted_cascade=round(wt * mag, 6)))

    coverage = covered_wt / total_wt if total_wt > 0 else 0.0
    net = bullish_wt - bearish_wt
    dead_band = 0.05
    tilt = "Bullish" if net > dead_band else ("Bearish" if net < -dead_band else "Neutral")
    names.sort(key=lambda n: -abs(n.weighted_cascade))
    return BookCascadeOverlay(
        names=names, net_tilt=tilt,
        bullish_wt=round(bullish_wt, 4), bearish_wt=round(bearish_wt, 4),
        weighted_conviction=round(weighted_conviction, 4),
        coverage=round(coverage, 4), n_covered=n_covered, n_uncovered=n_uncovered,
        top_contributors=names[:5])


def book_concentration(positions, stock_data) -> dict:
    """Book concentration: HHI, effective number of names, top-name weight.

    Empty book → all zeros (honest emptiness)."""
    weights = book_weights(positions, stock_data)
    ws = list(weights.values())
    if not ws:
        return {"hhi": 0.0, "effective_n": 0.0, "top_name_pct": 0.0, "n_priced": 0}
    hhi = sum(w * w for w in ws)
    return {
        "hhi": round(hhi, 4),
        "effective_n": round(1.0 / hhi, 2) if hhi > 0 else 0.0,
        "top_name_pct": round(max(ws) * 100.0, 1),
        "n_priced": len(ws),
    }
