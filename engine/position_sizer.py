"""engine/position_sizer.py — conviction-to-weight position sizer (R031).

The cascade scorer (``processing.disruption_cascade``) emits an
:class:`~processing.disruption_cascade.EquityIdea` per ticker carrying a
``direction`` (Bullish / Bearish / Neutral) and a ``conviction_score`` in
``[0, 1]``. That conviction is an **ordinal label** — a PM does not trade a
label, a PM trades *weights*. This module is the missing step: it turns ordinal
conviction + direction + **real return volatility** into signed target weights
via one of two published, deterministic schemes, with every constant surfaced.

Two modes, both pure
--------------------
* ``mode="vol_target"`` (default). The raw signed weight is
  ``sign × conviction / σ_ticker`` — higher conviction and *lower* vol earn a
  bigger position (an inverse-vol tilt scaled by conviction). The whole vector
  is then rescaled by a single scalar so the **book** volatility estimate hits
  ``book_vol_budget``. Per-name caps (``max_weight``) and a gross cap
  (``gross_cap``) are applied last.

* ``mode="kelly"`` — fractional Kelly. The raw signed weight is
  ``sign × kelly_fraction × edge / σ²`` where ``edge`` is a **documented,
  explicit** mapping from conviction to an expected annual excess return (see
  :data:`_KELLY_EDGE_FULL`). This is the textbook single-name Kelly fraction
  ``f* = edge / σ²`` shrunk by ``kelly_fraction`` (a 1/4-Kelly default is the
  usual practitioner haircut against estimation error). The same per-name and
  gross caps apply; the result is NOT additionally rescaled to a vol budget —
  Kelly already sets the level, the vol estimate is reported for context.

Volatility — REAL, look-ahead-free
----------------------------------
``σ_ticker`` is the annualized standard deviation of the ticker's column in the
``returns`` panel (daily σ × √252). The intended panel is
``processing.book_pnl.returns_panel(stock_data, tickers)`` — daily LOG returns
built from the look-ahead-free total-return close (``close × adj_factor``, R127)
so a split/large dividend in the window cannot inject a fake σ. The sizer never
fabricates a vol: **a name with no real return column (or a degenerate σ ≤ 0) is
SKIPPED**, listed in ``skipped`` with a reason. There is no synthetic fallback
here — if the panel is empty the book is empty.

Book-volatility estimate — real covariance when available
---------------------------------------------------------
The book vol used for the vol-target rescale is the **full quadratic form**
``sqrt(wᵀ Σ w)`` annualized, where ``Σ`` is the *sample covariance* of the
``returns`` panel over the names actually sized — so genuine cross-name
correlation is respected (two correlated longs cost more vol budget than two
independent ones). When the covariance cannot be formed (a single sized name, or
a non-positive estimate) the sizer falls back to a **documented diagonal
approximation** ``sqrt(Σ wᵢ² σᵢ²)`` (zero off-diagonal — treats names as
uncorrelated) and flags it in the provenance note. Which path was taken is
recorded in :attr:`SizedBook.used_covariance`.

Direction sign
--------------
Bullish → +1 (long), Bearish → −1 (short), Neutral → 0 (no position). A Neutral
idea is never sized; it does not consume gross budget.

Purity
------
No Streamlit, no globals, no I/O, no randomness. Deterministic given the same
``ideas`` + ``returns``. Empty ideas or an empty/None panel returns an empty
:class:`SizedBook` (no crash, no fabricated sizes).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Published constants — every load-bearing number is named and documented.
# ---------------------------------------------------------------------------

TRADING_DAYS_PER_YEAR: int = 252
"""Annualization factor for daily σ (daily σ × √252). Standard equity convention,
matching ``engine.portfolio_optimizer`` / ``processing.risk_lab``."""

# --- Kelly edge mapping ----------------------------------------------------
# Fractional-Kelly needs an *edge* — an expected annual excess return — per
# name. The cascade only gives ordinal conviction in [0, 1], so the mapping
# from conviction to edge must be stated explicitly. It is LINEAR and capped:
#
#     edge = _KELLY_EDGE_FULL × conviction_score      (annual, fractional)
#
# A full-conviction (1.0) idea is ASSUMED to carry an 8%/yr expected excess
# return; a 0.5-conviction idea, 4%/yr; and so on. This 8% figure is an
# explicit modeling ASSUMPTION, not an estimate from data — it is the single
# knob that turns the dimensionless conviction score into the return units
# Kelly requires. Documented and published so the assumption is auditable and
# a reviewer can dial it without touching the formula.
_KELLY_EDGE_FULL: float = 0.08

# Numerical floor on annualized σ used in any 1/σ or 1/σ² division, so a
# near-zero-vol name (e.g. a brand-new listing with two flat closes that
# slipped the min_obs gate) cannot blow the weight up to infinity. A name whose
# σ is at or below this floor is treated as having NO usable vol and is skipped
# (see ``size_from_conviction``), never sized off the floor — the floor only
# guards the arithmetic.
_MIN_ANNUAL_VOL: float = 1e-4

# Defaults (also the public signature defaults; named here so the documented
# value lives in one place and the config note can cite them).
_DEFAULT_BOOK_VOL_BUDGET: float = 0.12   # 12% annualized target book vol
_DEFAULT_KELLY_FRACTION: float = 0.25    # 1/4-Kelly practitioner haircut
_DEFAULT_MAX_WEIGHT: float = 0.15        # per-name cap (abs weight)
_DEFAULT_GROSS_CAP: float = 1.0          # Σ|wᵢ| cap (1.0 = fully invested)

VALID_MODES: tuple[str, ...] = ("vol_target", "kelly")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SizedPosition:
    """One sized position — a signed target weight plus its sizing inputs."""

    ticker: str
    direction: str               # "Bullish" | "Bearish"  (Neutral never sized)
    sign: int                    # +1 long / −1 short
    conviction_score: float      # [0, 1] — the cascade's ordinal conviction
    annual_vol: float            # annualized σ used (REAL, look-ahead-free)
    target_weight: float         # SIGNED final weight (after caps + scaling)


@dataclass(frozen=True)
class SizedBook:
    """A sized book — per-ticker signed target weights + book-level summary.

    Every number is reproducible from ``positions`` + ``constants``:
      * ``positions``       — the signed target weights, one per sized name.
      * ``est_book_vol``    — annualized book-vol estimate at the final weights
                              (full ``sqrt(wᵀΣw)`` when a covariance was usable,
                              else the diagonal approximation).
      * ``gross_exposure``  — Σ|wᵢ| (after the gross cap).
      * ``net_exposure``    — Σ wᵢ  (signed; longs − shorts).
      * ``mode`` / ``constants`` — the scheme + every published constant used.
      * ``skipped``         — ticker → reason for every idea NOT sized
                              (Neutral, no real vol, degenerate σ).
      * ``provenance``      — one-line honesty note: modeled sizing on REAL
                              conviction + REAL look-ahead-free vol.
    """

    positions: list[SizedPosition] = field(default_factory=list)
    est_book_vol: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    mode: str = "vol_target"
    used_covariance: bool = False
    constants: dict[str, float] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    provenance: str = ""

    def weights(self) -> dict[str, float]:
        """Convenience: ``{ticker: signed_target_weight}`` for the sized names."""
        return {p.ticker: p.target_weight for p in self.positions}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SIGN_BY_DIRECTION: dict[str, int] = {"Bullish": 1, "Bearish": -1, "Neutral": 0}


def _annual_vols(returns: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
    """``{ticker: annualized σ}`` for every ticker with a usable column.

    σ_annual = daily σ (population, ddof=0) × √252. A ticker absent from
    ``returns``, all-NaN, or with a non-finite / ≤ ``_MIN_ANNUAL_VOL`` σ is
    omitted from the result entirely — the caller treats a missing key as "no
    real vol" and skips the name rather than sizing it off a floor.
    """
    out: dict[str, float] = {}
    if returns is None or not isinstance(returns, pd.DataFrame) or returns.empty:
        return out
    for t in tickers:
        if t not in returns.columns:
            continue
        col = pd.to_numeric(returns[t], errors="coerce").dropna()
        if len(col) < 2:
            continue
        daily = float(col.std(ddof=0))
        ann = daily * math.sqrt(TRADING_DAYS_PER_YEAR)
        if not math.isfinite(ann) or ann <= _MIN_ANNUAL_VOL:
            continue
        out[t] = ann
    return out


def _book_vol(weights: dict[str, float], returns: pd.DataFrame) -> tuple[float, bool]:
    """Annualized book-vol estimate at ``weights`` → ``(est_vol, used_cov)``.

    Preferred: the full quadratic form ``sqrt(wᵀ Σ w) × √252`` where ``Σ`` is the
    *daily* sample covariance (ddof=0) of the sized names from ``returns`` — so
    genuine cross-name correlation is respected. ``used_cov`` is ``True`` here.

    Fallback (``used_cov`` False): a documented DIAGONAL approximation
    ``sqrt(Σ wᵢ² σᵢ²) × √252`` (zero off-diagonal, names treated as
    uncorrelated). Taken when fewer than two names overlap the panel, or the
    covariance estimate is non-finite / yields a non-positive variance.
    """
    names = [t for t in weights if abs(weights[t]) > 0.0]
    if not names:
        return 0.0, False
    ann = math.sqrt(TRADING_DAYS_PER_YEAR)

    have_panel = (
        returns is not None
        and isinstance(returns, pd.DataFrame)
        and not returns.empty
    )
    cov_names = [t for t in names if have_panel and t in returns.columns]

    # Full covariance path — needs >= 2 sized names present in the panel.
    if len(cov_names) >= 2:
        sub = returns[cov_names].apply(pd.to_numeric, errors="coerce").dropna(how="any")
        if len(sub) >= 2:
            cov = sub.cov(ddof=0).to_numpy()
            w = np.array([weights[t] for t in cov_names], dtype=float)
            daily_var = float(w @ cov @ w)
            if math.isfinite(daily_var) and daily_var > 0.0:
                return math.sqrt(daily_var) * ann, True

    # Diagonal fallback — sqrt(Σ wᵢ² σᵢ²), σ per name from the panel.
    vols = _annual_vols(returns, names) if have_panel else {}
    var = 0.0
    for t in names:
        sig = vols.get(t)
        if sig is None:
            continue
        var += (weights[t] ** 2) * (sig ** 2)
    return (math.sqrt(var) if var > 0.0 else 0.0), False


def _apply_caps(
    raw: dict[str, float], max_weight: float, gross_cap: float
) -> dict[str, float]:
    """Clamp each |weight| ≤ ``max_weight`` then scale Σ|w| ≤ ``gross_cap``.

    Per-name clamp first (preserves sign), then — only if the post-clamp gross
    still exceeds the cap — a single proportional down-scale of the whole
    vector. Down-scaling after the per-name clamp can only *shrink* weights, so
    the per-name cap is never re-violated. Order is deterministic.
    """
    capped = {
        t: math.copysign(min(abs(w), max_weight), w)
        for t, w in raw.items()
    }
    gross = sum(abs(w) for w in capped.values())
    if gross > gross_cap and gross > 0.0:
        scale = gross_cap / gross
        capped = {t: w * scale for t, w in capped.items()}
    return capped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def size_from_conviction(
    ideas: list,
    returns: pd.DataFrame,
    *,
    mode: str = "vol_target",
    book_vol_budget: float = _DEFAULT_BOOK_VOL_BUDGET,
    kelly_fraction: float = _DEFAULT_KELLY_FRACTION,
    max_weight: float = _DEFAULT_MAX_WEIGHT,
    gross_cap: float = _DEFAULT_GROSS_CAP,
) -> SizedBook:
    """Size a list of :class:`EquityIdea`-like objects into signed target weights.

    Parameters
    ----------
    ideas:
        Iterable of objects carrying ``ticker`` (str), ``direction``
        ("Bullish" / "Bearish" / "Neutral"), and ``conviction_score`` in
        ``[0, 1]`` — the cascade's :class:`EquityIdea`. ``None`` / empty → empty
        book.
    returns:
        Daily-returns panel (``DataFrame`` indexed by date, one column per
        ticker) — intended to be ``processing.book_pnl.returns_panel`` over the
        same tickers, i.e. REAL look-ahead-free total-return log-returns. The
        per-name σ and the book covariance both come from here. ``None`` /
        empty → empty book (no fabricated vols).
    mode:
        ``"vol_target"`` (inverse-vol × conviction, rescaled to
        ``book_vol_budget``) or ``"kelly"`` (fractional Kelly off the documented
        conviction→edge map). Any other value raises ``ValueError``.
    book_vol_budget:
        Target annualized book volatility for ``vol_target`` mode (default 12%).
        Ignored by ``kelly`` mode (Kelly sets its own level).
    kelly_fraction:
        Kelly haircut for ``kelly`` mode (default 1/4). Ignored by
        ``vol_target`` mode.
    max_weight:
        Per-name absolute-weight cap (default 0.15). Applied in both modes.
    gross_cap:
        Σ|wᵢ| cap (default 1.0). Applied in both modes, after the per-name cap.

    Returns
    -------
    SizedBook
        Per-ticker signed target weights + the realized book-vol estimate,
        gross/net exposure, the mode + constants, the skipped-name reasons, and
        a provenance note. Empty inputs → an empty book with the constants and
        provenance still populated.
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"position_sizer: unknown mode {mode!r}; expected one of {VALID_MODES}"
        )

    constants: dict[str, float] = {
        "book_vol_budget": float(book_vol_budget),
        "kelly_fraction": float(kelly_fraction),
        "max_weight": float(max_weight),
        "gross_cap": float(gross_cap),
        "kelly_edge_full": _KELLY_EDGE_FULL,
        "trading_days_per_year": float(TRADING_DAYS_PER_YEAR),
        "min_annual_vol": _MIN_ANNUAL_VOL,
    }

    def _empty(note: str, skipped: dict[str, str] | None = None) -> SizedBook:
        return SizedBook(
            positions=[], est_book_vol=0.0, gross_exposure=0.0, net_exposure=0.0,
            mode=mode, used_covariance=False, constants=constants,
            skipped=skipped or {}, provenance=note,
        )

    ideas = list(ideas or [])
    if not ideas:
        return _empty(
            "No ideas to size — empty book. Modeled sizing on real conviction "
            "+ real look-ahead-free vol; not investment advice."
        )

    # Resolve per-name σ from the REAL returns panel for every directional idea.
    directional = [
        i for i in ideas
        if _SIGN_BY_DIRECTION.get(str(getattr(i, "direction", "Neutral")), 0) != 0
        and str(getattr(i, "ticker", ""))
    ]
    tickers = [str(getattr(i, "ticker")) for i in directional]
    vols = _annual_vols(returns, tickers)

    skipped: dict[str, str] = {}
    for i in ideas:
        ticker = str(getattr(i, "ticker", ""))
        if not ticker:
            continue
        sign = _SIGN_BY_DIRECTION.get(str(getattr(i, "direction", "Neutral")), 0)
        if sign == 0:
            skipped.setdefault(ticker, "Neutral direction — no position taken.")
        elif ticker not in vols:
            skipped.setdefault(
                ticker,
                "No real return vol available (ticker absent from the panel or "
                "σ ≤ floor) — never sized off a fabricated vol.",
            )

    # Build raw SIGNED weights per mode for the names that survived.
    raw: dict[str, float] = {}
    meta: dict[str, tuple[str, int, float, float]] = {}  # ticker -> (dir, sign, conv, vol)
    for i in directional:
        ticker = str(getattr(i, "ticker"))
        sigma = vols.get(ticker)
        if sigma is None:
            continue
        direction = str(getattr(i, "direction"))
        sign = _SIGN_BY_DIRECTION[direction]
        conv = max(0.0, min(1.0, float(getattr(i, "conviction_score", 0.0) or 0.0)))
        if mode == "vol_target":
            # Inverse-vol tilt scaled by conviction: higher conviction + lower
            # vol → bigger raw position. Rescaled to the vol budget below.
            magnitude = conv / sigma
        else:  # kelly
            # Fractional single-name Kelly: f* = edge/σ², shrunk by kelly_fraction.
            edge = _KELLY_EDGE_FULL * conv
            magnitude = kelly_fraction * edge / (sigma * sigma)
        raw[ticker] = sign * magnitude
        meta[ticker] = (direction, sign, conv, sigma)

    if not raw:
        return _empty(
            "No idea had a real return vol to size against — empty book. "
            "Modeled sizing on real conviction + real look-ahead-free vol; "
            "not investment advice.",
            skipped=skipped,
        )

    if mode == "vol_target":
        # Scale the whole raw vector by ONE scalar so the book-vol estimate hits
        # the budget, THEN apply caps. (Capping can only lower the realized vol
        # below budget; that is the intended conservative direction.)
        pre_vol, _ = _book_vol(raw, returns)
        if pre_vol > 0.0:
            scale = book_vol_budget / pre_vol
            raw = {t: w * scale for t, w in raw.items()}

    final = _apply_caps(raw, max_weight, gross_cap)

    est_book_vol, used_cov = _book_vol(final, returns)
    gross = sum(abs(w) for w in final.values())
    net = sum(final.values())

    positions = [
        SizedPosition(
            ticker=t,
            direction=meta[t][0],
            sign=meta[t][1],
            conviction_score=round(meta[t][2], 4),
            annual_vol=round(meta[t][3], 6),
            target_weight=round(final[t], 6),
        )
        for t in sorted(final, key=lambda x: (-abs(final[x]), x))
    ]

    cov_note = (
        "real sample-covariance book vol"
        if used_cov else
        "diagonal (uncorrelated) book-vol approximation"
    )
    provenance = (
        f"Modeled {mode} sizing on REAL conviction + REAL look-ahead-free "
        f"return vol ({cov_note}); "
        + (
            f"rescaled to a {book_vol_budget:.0%} book-vol budget"
            if mode == "vol_target" else
            f"{kelly_fraction:g}-Kelly off an assumed "
            f"{_KELLY_EDGE_FULL:.0%}×conviction edge"
        )
        + f", per-name cap {max_weight:.0%}, gross cap {gross_cap:.0%}. "
        "Not investment advice, not a price target."
    )

    return SizedBook(
        positions=positions,
        est_book_vol=round(est_book_vol, 6),
        gross_exposure=round(gross, 6),
        net_exposure=round(net, 6),
        mode=mode,
        used_covariance=used_cov,
        constants=constants,
        skipped=skipped,
        provenance=provenance,
    )


__all__ = [
    "SizedPosition",
    "SizedBook",
    "size_from_conviction",
    "TRADING_DAYS_PER_YEAR",
    "VALID_MODES",
]
