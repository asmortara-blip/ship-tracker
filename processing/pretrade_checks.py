"""pretrade_checks.py — Pre-trade compliance gate for a proposed order (R108).

A pure DECISION engine that vets a proposed ``order_blotter.Order`` against the
current book + the covered universe BEFORE it could ever become a ticket. It is
**not an OMS** and writes nothing — no DB, no schema, no persistence. Every
check is a pure function of (order, book, stock_data) and the module **never
raises**: a check that errors degrades to a WARN "could not evaluate", not a
crash.

Checks run (each yields one ``CheckResult``)
--------------------------------------------
1. ``universe``      — BLOCK if the ticker is not in the covered universe
                       (``company_profiler.COMPANY_PROFILES`` /
                       ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE`` keys) or is
                       on an explicit caller ``restricted`` set.
2. ``single_name``   — project the POST-trade book weight for the ticker; WARN
                       as it approaches ``single_name_cap``, BLOCK once it
                       exceeds it. A trade that does NOT increase the name's
                       weight (a risk-reducing SELL) always PASSes, even if the
                       name stays concentrated — de-risking is never blocked.
3. ``sector``        — project the POST-trade weight of the ticker's SECTOR
                       (sector from ``company_profiler.COMPANY_PROFILES``); WARN
                       near / BLOCK over ``sector_cap``.
4. ``adv``           — order shares / average daily volume; WARN over
                       ``adv_participation_cap``. SKIPPED (PASS, documented) when
                       no real volume is available — we never fabricate an ADV.
5. ``sanctions``     — BLOCK if the ticker is in the sanctions/restricted set.
                       *** PLACEHOLDER ***: there is NO real equity-sanctions /
                       dark-fleet feed in this repo. The default set is EMPTY, so
                       this check passes for every tracked name until a caller
                       supplies a documented set via ``sanctions``. This is an
                       honest stub, NOT a sanctions screen — see
                       :data:`_PLACEHOLDER_SANCTIONS`.

The results aggregate into a ``PretradeVerdict``: ``blocked`` is True iff any
check BLOCKs; ``warnings`` counts the WARNs.

Post-trade projection (single-name + sector)
---------------------------------------------
The current book is marked to real closes (``book_pnl.mark_book``). The order's
signed notional (``+notional`` for BUY, ``-notional`` for SELL) is added to the
name's current market value, the book's total market value is adjusted by the
same signed amount, and the post-trade weight is ``new_name_mv / new_total_mv``
(clamped at 0). This is an exact projection on the priced sub-book; when the
book is entirely unpriced the projection is skipped with a documented WARN
rather than a fabricated weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from processing.book_pnl import mark_book, _latest_close
from processing.company_profiler import COMPANY_PROFILES
from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE

__all__ = [
    "CheckResult",
    "PretradeVerdict",
    "run_pretrade",
    "screen_blotter",
    "covered_universe",
]

# Statuses, ranked by severity for aggregation.
_PASS = "PASS"
_WARN = "WARN"
_BLOCK = "BLOCK"

# *** PLACEHOLDER sanctions / restricted set ***
# There is NO real equity-sanctions, OFAC, or dark-fleet feed wired into this
# repo. This set is intentionally EMPTY: the sanctions check is an honest stub
# that only fires when a caller passes a documented ``sanctions`` set. Do NOT
# read a clean sanctions result as "screened against sanctions" — it merely
# means the ticker is absent from a (here, empty) hand-maintained list.
_PLACEHOLDER_SANCTIONS: frozenset[str] = frozenset()

# Fraction of the cap at which we WARN before the hard BLOCK. e.g. with a 0.90
# soft ratio and a 0.15 single-name cap, projected weights in [0.135, 0.15]
# WARN and > 0.15 BLOCKs.
_SOFT_RATIO: float = 0.90


@dataclass(frozen=True)
class CheckResult:
    """One pre-trade check outcome.

    ``status`` is one of PASS / WARN / BLOCK. ``detail`` is a one-line,
    plain-language explanation safe to surface to a human reviewer.
    """

    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class PretradeVerdict:
    """The aggregate verdict for one proposed order.

    ``blocked`` is True iff any check BLOCKed (the order must not proceed).
    ``warnings`` counts WARN results (advisory; the order may proceed with
    review). ``results`` preserves every check in run order.
    """

    order: object                 # the order_blotter.Order vetted
    results: list = field(default_factory=list)   # list[CheckResult]
    blocked: bool = False
    warnings: int = 0


def covered_universe() -> frozenset[str]:
    """The set of tickers we cover: profiles ∪ exposure-matrix keys."""
    return frozenset(COMPANY_PROFILES.keys()) | frozenset(
        COMPANY_COMMODITY_EXPOSURE.keys()
    )


# ---------------------------------------------------------------------------
# Internal projection helpers
# ---------------------------------------------------------------------------

def _signed_notional(order) -> float:
    """+notional for a BUY, -notional for a SELL, 0.0 otherwise."""
    notional = abs(float(getattr(order, "notional", 0.0) or 0.0))
    side = str(getattr(order, "side", "") or "").strip().upper()
    if side == "BUY":
        return notional
    if side == "SELL":
        return -notional
    return 0.0


def _marked_mv_by_ticker(book, stock_data):
    """Return ``(mv_by_ticker, total_mv)`` from the priced sub-book.

    Aggregates real marked market value by ticker (a book may hold several lots
    of a name). ``total_mv`` is the priced market value. Never raises.
    """
    mv_by_ticker: dict[str, float] = {}
    total_mv = 0.0
    try:
        bm = mark_book(book, stock_data)
    except Exception:
        return ({}, 0.0)
    for p in getattr(bm, "positions", []) or []:
        if getattr(p, "priced", False) and getattr(p, "market_value", 0.0) > 0:
            tk = str(getattr(p, "ticker", ""))
            mv_by_ticker[tk] = mv_by_ticker.get(tk, 0.0) + float(p.market_value)
    total_mv = float(getattr(bm, "market_value", 0.0) or 0.0)
    return (mv_by_ticker, total_mv)


def _sector_of(ticker: str) -> str:
    """The covered sector label for a ticker (``""`` when unknown)."""
    prof = COMPANY_PROFILES.get(ticker) or {}
    return str(prof.get("sector", "") or "")


def _project_post_trade(order, book, stock_data):
    """Project post-trade book MV by ticker + the new total MV.

    Adds the order's signed notional to the ticker's current MV and to the book
    total. Returns ``(post_mv_by_ticker, post_total_mv, priced)`` where
    ``priced`` is False when the book had no priced MV to project against (the
    caller then degrades to a WARN rather than fabricating a weight).
    """
    mv_by_ticker, total_mv = _marked_mv_by_ticker(book, stock_data)
    ticker = str(getattr(order, "ticker", ""))
    signed = _signed_notional(order)

    # A SELL can take a name (and the book) negative in MV terms only if it
    # oversells; clamp the projected NAME mv at 0 (you cannot hold a negative
    # market value here — a short would be modeled as a separate lot upstream).
    post = dict(mv_by_ticker)
    post[ticker] = max(0.0, post.get(ticker, 0.0) + signed)
    post_total = total_mv + signed
    # If selling more than the whole book is worth, the projection is degenerate.
    priced = total_mv > 0 and post_total > 0
    return (post, post_total, priced)


def _grade(
    projected: float, cap: float, label: str, check: str, current: float = 0.0
) -> CheckResult:
    """Grade a projected weight against a cap: PASS / WARN / BLOCK.

    A trade that does NOT increase the weight (``projected <= current``) is
    risk-REDUCING and is never penalised, even if the name/sector remains over
    the cap — blocking a de-risking trade would be perverse. Only an increase
    that lands in the soft band WARNs, or over the cap BLOCKs.
    """
    soft = float(cap) * _SOFT_RATIO
    # Risk-reducing (or flat) trades pass regardless of the standing level.
    if projected <= float(current) + 1e-12:
        return CheckResult(
            check,
            _PASS,
            f"{label} does not increase post-trade ({projected:.1%}); "
            f"risk-reducing or flat, {cap:.0%} cap not engaged.",
        )
    if projected > float(cap):
        return CheckResult(
            check,
            _BLOCK,
            f"{label} projects to {projected:.1%} post-trade, over the "
            f"{cap:.0%} cap.",
        )
    if projected >= soft:
        return CheckResult(
            check,
            _WARN,
            f"{label} projects to {projected:.1%} post-trade, approaching the "
            f"{cap:.0%} cap.",
        )
    return CheckResult(
        check,
        _PASS,
        f"{label} projects to {projected:.1%} post-trade, within the "
        f"{cap:.0%} cap.",
    )


# ---------------------------------------------------------------------------
# Individual checks (each returns one CheckResult, each guards itself)
# ---------------------------------------------------------------------------

def _check_universe(order, restricted) -> CheckResult:
    try:
        ticker = str(getattr(order, "ticker", "") or "")
        if not ticker:
            return CheckResult("universe", _BLOCK, "Order has no ticker.")
        if ticker in (restricted or frozenset()):
            return CheckResult(
                "universe", _BLOCK,
                f"{ticker} is on the caller's restricted list.",
            )
        if ticker not in covered_universe():
            return CheckResult(
                "universe", _BLOCK,
                f"{ticker} is not in the covered universe "
                f"(no company profile / exposure record).",
            )
        return CheckResult("universe", _PASS, f"{ticker} is in the covered universe.")
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("universe", _WARN, f"Could not evaluate universe check: {exc}.")


def _check_single_name(order, book, stock_data, cap) -> CheckResult:
    try:
        ticker = str(getattr(order, "ticker", ""))
        post, post_total, priced = _project_post_trade(order, book, stock_data)
        if not priced:
            return CheckResult(
                "single_name", _WARN,
                f"Book is unpriced — cannot project {ticker} post-trade weight; "
                f"single-name cap not verified.",
            )
        pre, pre_total = _marked_mv_by_ticker(book, stock_data)
        current = (pre.get(ticker, 0.0) / pre_total) if pre_total > 0 else 0.0
        projected = post.get(ticker, 0.0) / post_total if post_total > 0 else 0.0
        return _grade(
            projected, cap, f"{ticker} single-name weight", "single_name", current
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("single_name", _WARN, f"Could not evaluate single-name cap: {exc}.")


def _check_sector(order, book, stock_data, cap) -> CheckResult:
    try:
        ticker = str(getattr(order, "ticker", ""))
        sector = _sector_of(ticker)
        if not sector:
            return CheckResult(
                "sector", _WARN,
                f"No sector mapping for {ticker}; sector cap not verified.",
            )
        post, post_total, priced = _project_post_trade(order, book, stock_data)
        if not priced:
            return CheckResult(
                "sector", _WARN,
                f"Book is unpriced — cannot project '{sector}' post-trade "
                f"weight; sector cap not verified.",
            )
        sector_mv = sum(
            mv for tk, mv in post.items() if _sector_of(tk) == sector
        )
        projected = sector_mv / post_total if post_total > 0 else 0.0
        pre, pre_total = _marked_mv_by_ticker(book, stock_data)
        pre_sector_mv = sum(
            mv for tk, mv in pre.items() if _sector_of(tk) == sector
        )
        current = (pre_sector_mv / pre_total) if pre_total > 0 else 0.0
        return _grade(
            projected, cap, f"'{sector}' sector weight", "sector", current
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("sector", _WARN, f"Could not evaluate sector cap: {exc}.")


def _avg_daily_volume(ticker, stock_data):
    """Average daily share volume from real cached data, or None.

    Reads the ``volume`` column of the normalised stock frame
    (``data.normalizer.STOCK_COLS``). Returns None when no real volume is
    available — the ADV check is then SKIPPED, never fabricated.
    """
    if not isinstance(stock_data, dict):
        return None
    frame = stock_data.get(ticker)
    if frame is None:
        return None
    try:
        if getattr(frame, "empty", True) or "volume" not in getattr(frame, "columns", []):
            return None
        import pandas as pd

        vol = pd.to_numeric(frame["volume"], errors="coerce").dropna()
        vol = vol[vol > 0]
        if vol.empty:
            return None
        return float(vol.tail(30).mean())
    except Exception:
        return None


def _check_adv(order, stock_data, cap) -> CheckResult:
    try:
        ticker = str(getattr(order, "ticker", ""))
        qty = abs(float(getattr(order, "quantity", 0.0) or 0.0))
        adv = _avg_daily_volume(ticker, stock_data)
        if adv is None or adv <= 0:
            return CheckResult(
                "adv", _PASS,
                f"No real volume for {ticker} — ADV-participation check "
                f"skipped (not fabricated).",
            )
        if qty <= 0:
            return CheckResult(
                "adv", _PASS,
                f"Order has no share quantity (notional-only) — ADV "
                f"participation not applicable.",
            )
        participation = qty / adv
        if participation > float(cap):
            return CheckResult(
                "adv", _WARN,
                f"Order is {participation:.1%} of {ticker} ADV "
                f"({adv:,.0f} sh), over the {cap:.0%} participation cap.",
            )
        return CheckResult(
            "adv", _PASS,
            f"Order is {participation:.1%} of {ticker} ADV, within the "
            f"{cap:.0%} cap.",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("adv", _WARN, f"Could not evaluate ADV participation: {exc}.")


def _check_sanctions(order, sanctions) -> CheckResult:
    """PLACEHOLDER sanctions veto — NOT a real sanctions feed (see module doc)."""
    try:
        ticker = str(getattr(order, "ticker", ""))
        sset = sanctions if sanctions is not None else _PLACEHOLDER_SANCTIONS
        if ticker in sset:
            return CheckResult(
                "sanctions", _BLOCK,
                f"{ticker} is on the sanctions/restricted set (PLACEHOLDER "
                f"list — not a real sanctions feed).",
            )
        return CheckResult(
            "sanctions", _PASS,
            f"{ticker} is not on the (placeholder) sanctions set. NOTE: no real "
            f"sanctions/dark-fleet feed exists — this is not a sanctions screen.",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult("sanctions", _WARN, f"Could not evaluate sanctions veto: {exc}.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pretrade(
    order,
    book,
    *,
    stock_data=None,
    single_name_cap: float = 0.15,
    sector_cap: float = 0.35,
    adv_participation_cap: float = 0.10,
    restricted=None,
    sanctions=None,
) -> PretradeVerdict:
    """Vet one proposed ``order`` against the ``book`` + the covered universe.

    Runs the five checks (universe, single-name, sector, ADV, sanctions) and
    aggregates them into a ``PretradeVerdict``. Pure + deterministic + never
    raises — a check that errors degrades to a WARN, never a crash.

    Parameters
    ----------
    order:
        An ``order_blotter.Order`` (or any object with ``ticker`` / ``side`` /
        ``quantity`` / ``notional``).
    book:
        The current position ledger as ``book_pnl.mark_book`` consumes it (list
        of dicts). The proposed order is projected ON TOP of this book.
    stock_data:
        ``ticker -> DataFrame`` real cached data, used to mark the book (for the
        concentration projections) and to read volume (for ADV). Optional —
        when absent, projection checks WARN ("book unpriced") and ADV is
        skipped.
    single_name_cap, sector_cap:
        Post-trade weight caps (fractions). Projected weight > cap → BLOCK;
        within ``_SOFT_RATIO`` of the cap → WARN.
    adv_participation_cap:
        Max order-shares / ADV before a WARN. No BLOCK — ADV is advisory.
    restricted:
        Optional set/iterable of tickers to hard-BLOCK at the universe gate (a
        caller-supplied trading-restriction list, distinct from sanctions).
    sanctions:
        Optional set/iterable for the sanctions veto. Defaults to the EMPTY
        PLACEHOLDER set — see the module docstring; this is NOT a real feed.

    Returns
    -------
    PretradeVerdict
    """
    stock_data = stock_data or {}
    restricted = frozenset(str(t) for t in (restricted or []))
    sanctions_set = (
        frozenset(str(t) for t in sanctions) if sanctions is not None else None
    )

    results = [
        _check_universe(order, restricted),
        _check_single_name(order, book, stock_data, single_name_cap),
        _check_sector(order, book, stock_data, sector_cap),
        _check_adv(order, stock_data, adv_participation_cap),
        _check_sanctions(order, sanctions_set),
    ]

    blocked = any(r.status == _BLOCK for r in results)
    warnings = sum(1 for r in results if r.status == _WARN)
    return PretradeVerdict(
        order=order, results=results, blocked=blocked, warnings=warnings
    )


def screen_blotter(orders, book, **kw) -> list[PretradeVerdict]:
    """Run :func:`run_pretrade` over every order in a blotter.

    Convenience wrapper — same keyword arguments as ``run_pretrade``. Returns
    one ``PretradeVerdict`` per order, in input order.
    """
    return [run_pretrade(o, book, **kw) for o in (orders or [])]
