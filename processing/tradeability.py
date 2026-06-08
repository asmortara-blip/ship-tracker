"""tradeability.py — a REAL-liquidity tradeability filter for equity ideas (R035).

A High-conviction idea in a thin, illiquid name is a trap: the edge cannot be
sized without moving the stock, and a short on it may be unborrowable. This
module gates / annotates every :class:`~processing.disruption_cascade.EquityIdea`
with three reads:

1. **Liquidity (dollar-ADV)** — the *real* average daily traded dollar value,
   ``mean(raw close × volume)`` over a trailing window. This is the one genuinely
   measured input here: ``volume`` is real in every normalized OHLCV frame
   (``data.normalizer.STOCK_COLS``). Per R127 the dollar value uses the **raw**
   ``close`` (NOT ``close × adj_factor``): dollar-volume is a point-in-time
   liquidity *fact* — what dollars actually changed hands at the prices that
   actually printed — not a return, so it is never split/dividend-adjusted.
   When a frame carries no real ``volume`` column the dollar-ADV is ``None`` and
   every downstream read degrades to an honest ``"unknown"`` rather than
   fabricating a number.

2. **Crowding** — how many of the *current* ideas share one
   ``dominant_driver_key`` (the chokepoint / commodity driver each idea's cascade
   runs through). Many ideas keyed to the same driver are not independent bets:
   they are one correlated exposure wearing many tickers, and they will gap
   together when that single driver resolves.

3. **Borrow feasibility (shorts only)** — a conservative, clearly-labeled
   **MODELED heuristic** keyed off the liquidity tier. There is NO real
   borrow / locate feed on this platform, so this is never presented as a live
   locate: it only says "a name this illiquid is *typically* hard or impossible
   to borrow", to stop a short idea from looking executable when it very likely
   is not.

These three combine into a single ``tradeable`` / ``caution`` / ``untradeable``
verdict plus a one-line reason and a modeled max-sensible size (a % of ADV
participation cap). Everything here is **pure** and **never raises**: None /
empty / malformed inputs yield a neutral ``"unknown"`` verdict, not a crash and
not a made-up figure. No Streamlit imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Liquidity tiers — keyed off REAL dollar-ADV (mean raw close × volume)
# ---------------------------------------------------------------------------
# Dollar-ADV cut-points (in USD) that bucket a name into a liquidity tier. These
# are deliberately conservative round numbers for US-listed shipping mid/small
# caps — a documented, published constant table, not a fitted threshold. Each
# tier carries:
#   * label              — the tier name surfaced in the UI badge
#   * max_participation  — the fraction of one day's ADV a position can take
#                          before it starts to move the stock (a modeled sizing
#                          cap, NOT a measured market-impact curve)
#   * verdict_floor      — the BEST tradeability verdict this tier can earn on
#                          liquidity grounds alone (crowding / borrow can only
#                          make it worse, never better)
# Checked high-ADV to low-ADV; the first tier whose floor the ADV clears wins.
#
# Verdict ordering (worst -> best): "untradeable" < "caution" < "tradeable".


@dataclass(frozen=True)
class LiquidityTier:
    """One liquidity bucket keyed off real dollar-ADV."""

    min_dollar_adv: float    # inclusive lower bound on mean(raw close × volume)
    label: str               # human-readable tier name
    max_participation: float  # fraction of ADV a position may take (sizing cap)
    verdict_floor: str       # best verdict this tier alone permits


# Ordered high-to-low. A name's tier is the first whose ``min_dollar_adv`` its
# real dollar-ADV meets or exceeds.
_LIQUIDITY_TIERS: tuple[LiquidityTier, ...] = (
    LiquidityTier(50_000_000.0, "Deep",     0.10, "tradeable"),
    LiquidityTier(10_000_000.0, "Liquid",   0.08, "tradeable"),
    LiquidityTier(2_000_000.0,  "Moderate", 0.05, "caution"),
    LiquidityTier(500_000.0,    "Thin",     0.03, "caution"),
    LiquidityTier(0.0,          "Illiquid", 0.02, "untradeable"),
)

# The tier used when dollar-ADV is unknown (no real volume). It is NOT one of
# the real tiers above — it carries the honest "unknown" label so the UI never
# implies a liquidity read it does not have.
_UNKNOWN_TIER = LiquidityTier(-1.0, "unknown", 0.0, "unknown")

# Borrow-feasibility flags, keyed off the same liquidity tiers. MODELED
# heuristic — there is NO real borrow feed, so this is a conservative proxy:
# a liquid name is generally borrowable, a thin name hard-to-borrow, an illiquid
# name likely unborrowable. Labeled as modeled wherever it is surfaced.
_BORROW_BY_TIER: dict[str, str] = {
    "Deep":     "borrowable",
    "Liquid":   "borrowable",
    "Moderate": "hard-to-borrow",
    "Thin":     "hard-to-borrow",
    "Illiquid": "likely-unborrowable",
    "unknown":  "borrow-unknown",
}

# A short idea whose borrow flag is one of these cannot be cleanly executed —
# the borrow read drags the verdict down regardless of liquidity tier.
_BORROW_UNTRADEABLE = frozenset({"likely-unborrowable"})
_BORROW_CAUTION = frozenset({"hard-to-borrow"})

# Crowding count at/above which an idea's exposure is judged crowded (many ideas
# leaning on the same single driver -> one correlated bet). Documented constant.
_CROWDING_CAUTION_COUNT: int = 4

DISCLAIMER = (
    "Dollar-ADV (liquidity) is computed from REAL traded volume × raw close — a "
    "point-in-time liquidity fact. The borrow-feasibility flag is a MODELED "
    "heuristic keyed off the liquidity tier, NOT a live locate (no real borrow "
    "feed exists). Max-size is a modeled % -of-ADV participation cap, not a "
    "measured market-impact estimate. Treat as a tradeability screen, not "
    "execution advice."
)

# Verdict ordering for taking the worse (more cautious) of two verdicts.
_VERDICT_RANK: dict[str, int] = {
    "untradeable": 0,
    "caution": 1,
    "tradeable": 2,
    "unknown": 3,   # neutral: never overrides a real verdict (see _worse_of)
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class Tradeability:
    """The tradeability read for one equity idea.

    ``verdict`` is one of ``"tradeable"`` / ``"caution"`` / ``"untradeable"`` /
    ``"unknown"`` (the last only when liquidity could not be measured). Every
    field is a visible input to that verdict so the read is self-documenting.
    """

    ticker: str
    verdict: str                       # tradeable | caution | untradeable | unknown
    reason: str                        # one-line plain-language justification
    dollar_adv: float | None = None    # real mean(raw close × volume), None if dark
    liquidity_tier: str = "unknown"    # _LIQUIDITY_TIERS label, or "unknown"
    max_size_usd: float | None = None  # modeled participation-cap dollar size
    max_participation: float = 0.0     # fraction-of-ADV cap used for max_size_usd
    crowding_count: int = 1            # # ideas sharing this idea's driver key
    crowding_key: str = ""             # the shared dominant_driver_key
    is_crowded: bool = False           # crowding_count >= _CROWDING_CAUTION_COUNT
    direction: str = ""                # idea direction (for the borrow read)
    is_short: bool = False             # direction == "Bearish"
    borrow_flag: str = ""              # MODELED borrow heuristic (shorts only; "" for longs)


# ---------------------------------------------------------------------------
# 1. Liquidity — REAL dollar-ADV
# ---------------------------------------------------------------------------

def dollar_adv(
    ticker: str,
    stock_data: dict | None,
    *,
    window: int = 30,
) -> float | None:
    """Real average daily traded **dollar** value for *ticker*.

    Returns ``mean(raw close × volume)`` over the trailing ``window`` rows of
    *ticker*'s OHLCV frame — the genuine average daily dollar value that changed
    hands. Per R127 this uses the **raw** ``close`` (never ``close × adj_factor``):
    dollar-volume is a point-in-time liquidity fact, not a return, so it is not
    split/dividend-adjusted.

    Returns ``None`` — not 0, not a guess — when there is no real volume to
    measure: the ticker is absent, the frame is empty / malformed, the ``volume``
    column is missing, or every usable row has zero or non-finite volume. Pure;
    never raises.
    """
    if not stock_data or not ticker:
        return None
    df = stock_data.get(ticker)
    if df is None:
        return None
    try:
        if getattr(df, "empty", True):
            return None
        if "close" not in df.columns or "volume" not in df.columns:
            # No real volume column -> liquidity is genuinely dark. Honest None.
            return None
        close = pd.to_numeric(df["close"], errors="coerce")
        volume = pd.to_numeric(df["volume"], errors="coerce")
        dollar = close * volume
        # Drop rows where either side is missing/non-finite, then take the
        # trailing window. A zero-volume day is a real (no-trade) observation
        # and is KEPT so a frequently-untraded name reads as genuinely thin;
        # only non-finite (NaN) rows are dropped.
        dollar = dollar[dollar.notna()]
        if dollar.empty:
            return None
        w = max(1, int(window))
        trailing = dollar.tail(w)
        if trailing.empty:
            return None
        # If the entire usable history is zero traded dollars there is no real
        # liquidity signal at all -> None (vs a real but tiny ADV which is a
        # legitimately-thin, kept number).
        value = float(trailing.mean())
        if value <= 0.0:
            return None
        return value
    except Exception:  # pragma: no cover - defensive
        logger.debug("tradeability.dollar_adv failed for {}", ticker)
        return None


def _tier_for_adv(adv: float | None) -> LiquidityTier:
    """Return the :class:`LiquidityTier` for a dollar-ADV (``None`` -> unknown)."""
    if adv is None:
        return _UNKNOWN_TIER
    for tier in _LIQUIDITY_TIERS:
        if adv >= tier.min_dollar_adv:
            return tier
    # _LIQUIDITY_TIERS ends at min_dollar_adv=0.0, so any finite adv>0 matches;
    # this is reached only for a negative adv (shouldn't happen) -> illiquid.
    return _LIQUIDITY_TIERS[-1]


# ---------------------------------------------------------------------------
# 2. Crowding — shared dominant driver
# ---------------------------------------------------------------------------

def crowding_score(ideas) -> dict[str, int]:
    """How crowded each idea's driver is — ``{ticker: count}``.

    The crowding count for a ticker is **how many ideas in the list share that
    ticker's ``dominant_driver_key``** (the chokepoint / commodity / congestion
    driver the cascade runs through). A high count means several "different"
    ideas are really one correlated exposure: they all hinge on the same single
    driver and will move together when it resolves.

    The key is :attr:`EquityIdea.dominant_driver_key`. Ideas with an empty
    driver key (no cascade) are each counted as a crowd of 1 — an unkeyed idea
    is not crowding any group. Pure; never raises; ``None`` / empty -> ``{}``.

    Returns ``{ticker: int}`` — the count *includes the idea itself*, so a lone
    idea on a driver reads 1, two ideas sharing a driver each read 2, etc.
    """
    out: dict[str, int] = {}
    if not ideas:
        return out
    # Tally how many ideas carry each non-empty driver key.
    key_counts: dict[str, int] = {}
    rows: list[tuple[str, str]] = []
    for idea in ideas:
        ticker = str(getattr(idea, "ticker", "") or "")
        if not ticker:
            continue
        key = str(getattr(idea, "dominant_driver_key", "") or "")
        rows.append((ticker, key))
        if key:
            key_counts[key] = key_counts.get(key, 0) + 1
    for ticker, key in rows:
        # An empty / unkeyed driver is a crowd of one (itself); a real key gets
        # the full tally of ideas that share it.
        out[ticker] = key_counts.get(key, 1) if key else 1
    return out


# ---------------------------------------------------------------------------
# 3. Borrow feasibility — MODELED heuristic (shorts only)
# ---------------------------------------------------------------------------

def borrow_feasibility(ticker: str, dollar_adv: float | None) -> str:
    """A conservative, MODELED short-borrow flag keyed off liquidity.

    Returns one of ``"borrowable"`` / ``"hard-to-borrow"`` /
    ``"likely-unborrowable"`` / ``"borrow-unknown"`` (the last when liquidity is
    dark). This is a **modeled heuristic**, NOT a live locate — there is no real
    borrow feed on this platform. The mapping is purely a function of the
    liquidity tier (a deep name is generally borrowable; a thin name is hard to
    borrow; an illiquid name is likely unborrowable), erring conservative so a
    short idea never looks more executable than it is.

    Pure; never raises. The ``ticker`` argument is accepted for signature
    symmetry and future per-name overrides; the flag today derives solely from
    ``dollar_adv``'s tier.
    """
    tier = _tier_for_adv(dollar_adv)
    return _BORROW_BY_TIER.get(tier.label, "borrow-unknown")


# ---------------------------------------------------------------------------
# Verdict combination
# ---------------------------------------------------------------------------

def _worse_of(a: str, b: str) -> str:
    """Return the more cautious of two verdicts.

    ``"unknown"`` is neutral: it never drags a real verdict down, and a real
    verdict always wins over ``"unknown"``. Among the real verdicts the lower
    rank (more cautious) wins.
    """
    if a == "unknown":
        return b
    if b == "unknown":
        return a
    return a if _VERDICT_RANK.get(a, 3) <= _VERDICT_RANK.get(b, 3) else b


# ---------------------------------------------------------------------------
# 4. Combined assessment
# ---------------------------------------------------------------------------

def assess_tradeability(idea, stock_data: dict | None, ideas=None) -> Tradeability:
    """Combine liquidity, crowding and borrow into one tradeability read for *idea*.

    The pipeline:

    1. **Liquidity** — real dollar-ADV -> a :class:`LiquidityTier` (label +
       participation cap + verdict floor). A dark frame yields ``None`` ADV and
       an ``"unknown"`` verdict (honest, not fabricated).
    2. **Max size** — the tier's participation cap × the dollar-ADV, the modeled
       largest position the name can absorb in roughly one day without moving
       (a sizing cap, not a market-impact model). ``None`` when ADV is unknown.
    3. **Crowding** — how many of ``ideas`` share this idea's
       ``dominant_driver_key``; at/above :data:`_CROWDING_CAUTION_COUNT` the
       verdict is dragged to at most ``"caution"``.
    4. **Borrow (shorts only)** — for a Bearish idea the MODELED borrow flag can
       drag the verdict to ``"caution"`` (hard-to-borrow) or ``"untradeable"``
       (likely-unborrowable). Longs ignore borrow entirely.

    The final verdict is the WORST of the liquidity-tier floor, the crowding
    cap, and the borrow cap. Pure; never raises; a ``None`` idea (or one whose
    fields are missing) degrades to a neutral ``"unknown"`` Tradeability rather
    than crashing.
    """
    if idea is None:
        return Tradeability(
            ticker="",
            verdict="unknown",
            reason="No idea supplied — tradeability unknown.",
        )

    ticker = str(getattr(idea, "ticker", "") or "")
    direction = str(getattr(idea, "direction", "") or "")
    is_short = direction == "Bearish"

    try:
        adv = dollar_adv(ticker, stock_data)
    except Exception:  # pragma: no cover - dollar_adv is already guarded
        adv = None

    tier = _tier_for_adv(adv)
    liquidity_verdict = tier.verdict_floor
    max_participation = tier.max_participation
    max_size_usd = (
        round(adv * max_participation, 2)
        if adv is not None and max_participation > 0
        else None
    )

    # --- Crowding -----------------------------------------------------------
    crowding_key = str(getattr(idea, "dominant_driver_key", "") or "")
    crowding_count = 1
    if ideas:
        try:
            crowding_count = crowding_score(ideas).get(ticker, 1)
        except Exception:  # pragma: no cover - crowding_score is guarded
            crowding_count = 1
    is_crowded = crowding_count >= _CROWDING_CAUTION_COUNT
    crowding_verdict = "caution" if is_crowded else "unknown"

    # --- Borrow (shorts only) ----------------------------------------------
    borrow_flag = ""
    borrow_verdict = "unknown"
    if is_short:
        borrow_flag = borrow_feasibility(ticker, adv)
        if borrow_flag in _BORROW_UNTRADEABLE:
            borrow_verdict = "untradeable"
        elif borrow_flag in _BORROW_CAUTION:
            borrow_verdict = "caution"

    # --- Combine: the worst (most cautious) of the three reads --------------
    verdict = liquidity_verdict
    verdict = _worse_of(verdict, crowding_verdict)
    verdict = _worse_of(verdict, borrow_verdict)

    # --- One-line reason ----------------------------------------------------
    reason = _build_reason(
        verdict=verdict,
        tier=tier,
        adv=adv,
        max_size_usd=max_size_usd,
        is_crowded=is_crowded,
        crowding_count=crowding_count,
        crowding_key=crowding_key,
        is_short=is_short,
        borrow_flag=borrow_flag,
    )

    return Tradeability(
        ticker=ticker,
        verdict=verdict,
        reason=reason,
        dollar_adv=(round(adv, 2) if adv is not None else None),
        liquidity_tier=tier.label,
        max_size_usd=max_size_usd,
        max_participation=max_participation,
        crowding_count=crowding_count,
        crowding_key=crowding_key,
        is_crowded=is_crowded,
        direction=direction,
        is_short=is_short,
        borrow_flag=borrow_flag,
    )


def _fmt_usd(value: float | None) -> str:
    """Compact USD formatter — $1.2M / $850K / $420 — for reason strings."""
    if value is None:
        return "unknown"
    v = float(value)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _build_reason(
    *,
    verdict: str,
    tier: LiquidityTier,
    adv: float | None,
    max_size_usd: float | None,
    is_crowded: bool,
    crowding_count: int,
    crowding_key: str,
    is_short: bool,
    borrow_flag: str,
) -> str:
    """Assemble the one-line plain-language reason for a verdict."""
    if adv is None:
        liq = "Liquidity unknown — no real volume to measure dollar-ADV"
    else:
        liq = (
            f"{tier.label} liquidity ({_fmt_usd(adv)}/day ADV; "
            f"max modeled size {_fmt_usd(max_size_usd)} at "
            f"{tier.max_participation:.0%} of ADV)"
        )
    parts = [liq]
    if is_crowded:
        key_txt = crowding_key or "an unspecified driver"
        parts.append(
            f"crowded: {crowding_count} ideas share the '{key_txt}' driver "
            f"(one correlated bet)"
        )
    if is_short and borrow_flag:
        parts.append(f"short borrow {borrow_flag} (modeled, not a live locate)")
    body = "; ".join(parts) + "."

    if verdict == "untradeable":
        head = "Untradeable as a High-conviction size"
    elif verdict == "caution":
        head = "Tradeable with caution"
    elif verdict == "tradeable":
        head = "Tradeable"
    else:
        head = "Tradeability unknown"
    return f"{head} — {body}"


# ---------------------------------------------------------------------------
# 5. Convenience — screen a whole idea list
# ---------------------------------------------------------------------------

def screen_ideas(ideas, stock_data: dict | None) -> list[Tradeability]:
    """Assess every idea in *ideas*, returning a list of :class:`Tradeability`.

    Order is preserved 1:1 with the input. Crowding is computed across the
    *whole* list (so each idea sees the real crowd around its driver). Pure;
    never raises; ``None`` / empty -> ``[]``.
    """
    if not ideas:
        return []
    out: list[Tradeability] = []
    for idea in ideas:
        try:
            out.append(assess_tradeability(idea, stock_data, ideas))
        except Exception:  # pragma: no cover - assess_tradeability is guarded
            logger.debug(
                "tradeability.screen_ideas: assess failed for {}",
                getattr(idea, "ticker", "?"),
            )
            out.append(
                Tradeability(
                    ticker=str(getattr(idea, "ticker", "") or ""),
                    verdict="unknown",
                    reason="Tradeability could not be assessed for this idea.",
                )
            )
    return out
