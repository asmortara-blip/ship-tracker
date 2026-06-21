"""order_blotter.py — Idea→target deltas turned into proposed orders (R108).

A *minimal, in-memory* order blotter: the bridge between the research engine's
ranked cascade ideas (``disruption_cascade.EquityIdea``) and a pre-trade
compliance gate (``processing.pretrade_checks``). It is a DECISION engine, not
an OMS — there is **no persistence, no DB table, no schema**. Each call returns
a fresh ``list[Order]`` derived deterministically from the inputs.

What it does
------------
Given the ranked ideas and the current book, it computes, per name, the gap
between the idea's *implied target weight* and the name's *current book weight*,
and emits a proposed ``Order`` to close that gap:

  * Bullish idea  → target weight > 0; if under-weight vs target → **BUY**.
  * Bearish idea  → target weight < 0 (a short tilt); if the current long
    weight exceeds the (negative/zero) target → **SELL**.
  * Neutral idea  → target weight 0; **no order** unless the book already holds
    the name above the no-trade band (then a trim SELL toward flat).

Sizing (documented, deliberately simple)
----------------------------------------
The target weight is conviction-scaled within a documented ``max_weight`` cap::

    target_magnitude = conviction_score * max_weight        # in [0, max_weight]
    target_weight    = +target_magnitude  (Bullish)
                     = -target_magnitude  (Bearish)
                     =  0.0               (Neutral)

The signed weight delta vs the current book weight becomes a notional::

    delta_weight  = target_weight - current_weight
    notional      = delta_weight * nav

A trade is only proposed when ``abs(delta_weight) >= no_trade_band`` (a
documented dead-band that suppresses churn). ``quantity`` is filled from the
notional **only when a real latest close is available** in ``stock_data``
(``notional / price``); otherwise ``quantity`` is left 0.0 and the order
carries its dollar ``notional`` alone — we never fabricate a share count off a
missing price. ``side`` is BUY when ``delta_weight > 0`` (we need to add
exposure), SELL when ``delta_weight < 0``.

Pure + deterministic: same ideas + book + nav → same orders. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from processing.book_exposure import book_weights
from processing.book_pnl import _latest_close

__all__ = ["Order", "build_blotter"]


@dataclass(frozen=True)
class Order:
    """One proposed order. Pure data — not a ticket, not persisted.

    ``quantity`` is shares (0.0 when no real price was available to convert the
    notional). ``notional`` is the signed-magnitude dollar size of the trade
    (always >= 0; the ``side`` carries the direction). ``reason`` records the
    idea mapping for traceability.
    """

    ticker: str
    side: str                 # "BUY" | "SELL"
    quantity: float = 0.0     # shares; 0.0 when price unavailable
    notional: float = 0.0     # >= 0 dollar size; direction is in `side`
    reason: str = ""


def _target_weight(direction: str, conviction: float, max_weight: float) -> float:
    """Map an idea direction + conviction to a signed target book weight.

    Bullish → +conviction*max_weight, Bearish → -conviction*max_weight,
    Neutral / anything else → 0.0. Conviction is clamped to [0, 1].
    """
    conv = max(0.0, min(1.0, float(conviction or 0.0)))
    mag = conv * float(max_weight)
    d = str(direction or "").strip().lower()
    if d.startswith("bull"):
        return mag
    if d.startswith("bear"):
        return -mag
    return 0.0


def build_blotter(
    ideas,
    current_book,
    target_weights=None,
    *,
    nav: float = 1_000_000.0,
    stock_data=None,
    max_weight: float = 0.10,
    no_trade_band: float = 0.005,
) -> list[Order]:
    """Turn cascade ideas (or explicit target weights) into proposed orders.

    Parameters
    ----------
    ideas:
        ``list[disruption_cascade.EquityIdea]`` (or any objects exposing
        ``ticker`` / ``direction`` / ``conviction_score``). Used to derive a
        signed target weight per name. Ignored for names overridden in
        ``target_weights``. ``None`` / empty is tolerated.
    current_book:
        The position ledger as ``mark_book`` consumes it — a list of dicts with
        at least ``ticker``/``shares``/``avg_cost``. Used (with ``stock_data``)
        to compute the *current* book weight per name.
    target_weights:
        Optional ``{ticker: signed_weight}`` that OVERRIDES the idea-derived
        target for those tickers (an explicit-target escape hatch). Other names
        still use the idea mapping.
    nav:
        Book NAV used to turn a weight delta into a dollar notional. Documented
        default; pass the real marked NAV when available.
    stock_data:
        Optional ``ticker -> DataFrame`` used to (a) compute current weights and
        (b) convert a notional into a share quantity via the latest real close.
        When a name's price is missing, ``quantity`` stays 0.0 (notional only).
    max_weight:
        Per-name cap on the conviction-scaled target magnitude (default 0.10).
    no_trade_band:
        Minimum ``abs(delta_weight)`` to propose a trade — a churn-suppressing
        dead-band (default 0.5% of NAV).

    Returns
    -------
    list[Order]
        Proposed orders, one per name that needs a non-trivial rebalance,
        sorted by descending notional (largest trades first). Deterministic.
    """
    stock_data = stock_data or {}
    current_weights = book_weights(current_book, stock_data) or {}
    overrides = dict(target_weights or {})

    # Union of names that either have an idea, an explicit target, or a current
    # holding — every name that could need an order.
    idea_by_ticker = {}
    for i in ideas or []:
        tk = getattr(i, "ticker", None)
        if tk:
            idea_by_ticker[str(tk)] = i
    names = set(idea_by_ticker) | set(overrides) | set(current_weights)

    orders: list[Order] = []
    for ticker in sorted(names):
        cur_w = float(current_weights.get(ticker, 0.0))

        if ticker in overrides:
            tgt_w = float(overrides[ticker])
            reason = f"explicit target weight {tgt_w:+.3f}"
        else:
            idea = idea_by_ticker.get(ticker)
            if idea is None:
                # Held but no idea + no override → leave it (no view).
                continue
            direction = str(getattr(idea, "direction", "Neutral") or "Neutral")
            conviction = float(getattr(idea, "conviction_score", 0.0) or 0.0)
            tgt_w = _target_weight(direction, conviction, max_weight)
            reason = (
                f"{direction} idea (conviction {conviction:.2f}) "
                f"→ target weight {tgt_w:+.3f}"
            )

        delta_w = tgt_w - cur_w
        if abs(delta_w) < float(no_trade_band):
            continue

        side = "BUY" if delta_w > 0 else "SELL"
        notional = abs(delta_w) * float(nav)

        # Convert to shares only when a REAL latest close exists.
        price = _latest_close(stock_data, ticker)
        quantity = (notional / price) if (price and price > 0) else 0.0

        orders.append(
            Order(
                ticker=ticker,
                side=side,
                quantity=round(float(quantity), 4),
                notional=round(float(notional), 2),
                reason=reason,
            )
        )

    orders.sort(key=lambda o: -o.notional)
    return orders
