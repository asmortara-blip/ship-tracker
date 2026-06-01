"""disruption_cascade.py — the core cascade scorer + ``EquityIdea`` generation.

This is the **"Conclude" stage** of the Disruption Alpha chain. Everything
upstream has measured *what is physically breaking* (the Shipping Stress Index)
and *which commodities and companies are exposed* (the exposure matrix). This
module walks that chain to its conclusion: for every tracked shipping ticker it
builds a ranked, **fully traceable** candidate equity idea.

The chain, per ticker
----------------------
For each company in ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE`` the scorer
walks one hop per (commodity, route) pair:

* the company has an *exposure weight* to an HS cargo category;
* that category implies a set of trade routes
  (``exposure_matrix.routes_for_commodity``);
* each route carries a measured ``RouteStress.stress_score`` from the SSI;
* the route's *cargo share* of that commodity scales the link — the **actual**
  per-route commodity share from ``cargo_analyzer.get_route_cargo_mix``, so a
  lane genuinely carrying more of the commodity contributes more. When real mix
  data cannot be obtained the link falls back to a uniform ``1 / N`` split
  across the commodity's routes.

One :class:`CascadeLink` is recorded per hop with
``contribution = route_stress * cargo_share * exposure_weight`` — a single
number, fully decomposable into its three visible factors. The company's raw
cascade magnitude is the sum of those contributions.

Direction — explicitly, not learned
------------------------------------
A disruption is **not** uniformly bullish or bearish. Tight-capacity /
rate-driven stress lifts container-liner pricing power (bullish); fuel-cost
(USO) driven stress compresses carrier margins (bearish); dry-bulk names track
metals/agriculture rather than finished goods. That judgement lives in
:data:`_DIRECTION_RULES` — an **explicit, documented, auditable** rule table
keyed by ``(company sector, dominant stress driver)``. There is no model to
train and no hidden weighting: every sign in the output can be traced to one
named row of that table.

Conviction — a transparent weighted sum
---------------------------------------
``conviction_score`` (always in ``[0, 1]``) is a plain weighted sum of four
named terms — cascade magnitude, signal-agreement count, commodity-ETF
confirmation, and route vulnerability — and **every term is surfaced verbatim**
in the idea's ``supporting_signals`` list.

The four term *weights* are **not** one fixed set. :data:`_CONVICTION_WEIGHT_SETS`
holds a small table of hand-authored, per-driver weight sets: a chokepoint-driven
idea up-weights cascade magnitude (the physical reroute is the signal), a
rate-dislocation idea up-weights signal agreement (a rate move wants
independent confirmation). Every set sums to 1.0, every set is published, and
the *name* of the set actually used is stated in ``supporting_signals`` — the
score stays fully reproducible.

The route-vulnerability term is additionally weighted by **stress persistence**:
fast-reverting stress (weather) is discounted, slow-reverting structural stress
(a chokepoint closure) is not. The per-driver persistence factor lives in
:data:`_DRIVER_PERSISTENCE` and is documented and explicit — not learned.

The optional ``insights`` argument (``engine.scorer`` output) is cross-referenced
purely for *corroboration*: a matching ``Insight.stocks_potentially_affected``
adds a supporting signal but is never required and never changes the direction.

Pure processing module — **no Streamlit imports, no ``st.`` calls.** Every
public function tolerates an empty or degenerate ``stress_report`` /
``exposure_matrix`` and returns neutral defaults rather than raising, matching
the codebase convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from processing import cargo_analyzer
from processing.commodity_shipping import analyze_commodity_signals
from processing.company_profiler import COMPANY_PROFILES
from processing.exposure_matrix import (
    COMPANY_COMMODITY_EXPOSURE,
    COMMODITY_ETF_MAP,
    company_commodity_weights,
    routes_for_commodity,
)

# ---------------------------------------------------------------------------
# Sector classification
# ---------------------------------------------------------------------------
# Companies fall into one of two cascade archetypes. The free-text
# ``COMPANY_PROFILES[t]["sector"]`` field is not directly usable as a key
# (values such as "Container/Dry Bulk" or "Container/Jones Act" mix terms), so
# the authoritative split is the explicit ticker sets below — the same sets
# ``exposure_matrix`` uses, kept consistent on purpose.
#
#   * "container" — liners and container lessors. Their economics turn on
#     vessel-capacity tightness: when finished-goods lanes are stressed by
#     congestion or rate dislocation, box pricing power rises.
#   * "dry bulk"  — Capesize/Panamax bulkers. Their economics turn on raw-
#     materials (metals, agriculture) demand and the bulk freight cycle.

_CONTAINER_TICKERS: frozenset[str] = frozenset({"ZIM", "MATX", "DAC", "CMRE"})
_DRY_BULK_TICKERS: frozenset[str] = frozenset({"SBLK", "GOGL", "DSX"})


def _company_sector(ticker: str) -> str:
    """Return the cascade sector archetype for *ticker*.

    One of ``"container"`` or ``"dry bulk"``. The explicit ticker sets are
    authoritative; anything outside both falls back to a substring read of the
    profile's free-text ``sector`` field, defaulting to ``"container"``.
    """
    if ticker in _DRY_BULK_TICKERS:
        return "dry bulk"
    if ticker in _CONTAINER_TICKERS:
        return "container"
    sector_text = str(COMPANY_PROFILES.get(ticker, {}).get("sector", "")).lower()
    return "dry bulk" if "dry bulk" in sector_text else "container"


# ---------------------------------------------------------------------------
# Stress-driver normalisation
# ---------------------------------------------------------------------------
# ``RouteStress.dominant_driver`` is a human-readable label produced by
# shipping_stress_index (e.g. "Chokepoint disruption", "Freight-rate
# dislocation"). _DIRECTION_RULES is keyed by a compact, stable driver key, so
# the label is normalised here. Any unrecognised label maps to "congestion" —
# a generic operational-disruption bucket.

_DRIVER_KEYS: dict[str, str] = {
    "chokepoint disruption":     "chokepoint",
    "port congestion":           "congestion",
    "weather risk":              "weather",
    "freight-rate dislocation":  "rate",
    "structural vulnerability":  "vulnerability",
}
_DEFAULT_DRIVER_KEY = "congestion"


def _driver_key(dominant_driver: str) -> str:
    """Normalise a human-readable dominant-driver label to a rule-table key."""
    return _DRIVER_KEYS.get(str(dominant_driver).strip().lower(), _DEFAULT_DRIVER_KEY)


# ---------------------------------------------------------------------------
# _DIRECTION_RULES — the explicit, auditable direction table
# ---------------------------------------------------------------------------
# This table is the crux of the "transparent, not a black box" requirement.
# It is keyed by ``(company sector, dominant stress driver)`` and yields a
# ``(sign, rationale)`` pair:
#
#   sign      — +1 bullish, -1 bearish, 0 neutral. Multiplied onto the
#               company's cascade magnitude to give a signed cascade score.
#   rationale — a one-line plain-language justification, surfaced verbatim in
#               the EquityIdea thesis so every sign is self-documenting.
#
# The economic logic, stated once:
#
#   * Container liners/lessors. Stress that *tightens effective capacity* is
#     PRICE-POSITIVE for box freight: chokepoint reroutes (Suez/Panama) absorb
#     tonne-miles and strand capacity; port congestion idles ships; a freight-
#     rate dislocation on a stressed lane is itself the bullish signal. Weather
#     is mildly bullish (short, localised capacity loss). Pure structural
#     *vulnerability* with no acute trigger is read as a latent risk, not a
#     catalyst -> neutral.
#   * Dry-bulk carriers. They do not earn box freight, so container-capacity
#     tightness is not their signal; their cascade runs through the metals /
#     agriculture commodities (handled by exposure weighting). Chokepoint and
#     congestion stress still lengthen bulk voyages and tighten the bulk
#     market -> bullish but at a lower magnitude than for liners. Rate
#     dislocation on a bulk-relevant lane is bullish. Weather is mildly
#     bullish; vulnerability alone is neutral.
#
# A separate fuel-cost overlay (see _FUEL_COST_RULE / _apply_fuel_overlay)
# handles the USO bunker-cost channel, which is BEARISH for both sectors
# regardless of the dominant physical driver. It is kept out of this table
# because it is an exposure-side signal (USO ETF move), not a route-stress
# driver.

# (sector, driver_key) -> (sign, rationale)
_DIRECTION_RULES: dict[tuple[str, str], tuple[int, str]] = {
    # ---- Container liners / lessors --------------------------------------
    ("container", "chokepoint"): (
        +1,
        "Chokepoint reroutes strand container capacity and absorb tonne-miles, "
        "tightening box supply — price-positive for liners and lessors.",
    ),
    ("container", "congestion"): (
        +1,
        "Port congestion idles container tonnage and lengthens turn times, "
        "tightening effective capacity — supportive of box freight rates.",
    ),
    ("container", "rate"): (
        +1,
        "A freight-rate dislocation on a stressed container lane is itself the "
        "bullish signal — scarcity pricing is flowing through to carriers.",
    ),
    ("container", "weather"): (
        +1,
        "Weather disruption causes short, localised container-capacity loss — "
        "mildly supportive of freight rates.",
    ),
    ("container", "vulnerability"): (
        0,
        "Stress is dominated by the lane's structural fragility with no acute "
        "trigger — a latent risk rather than a directional catalyst.",
    ),
    # ---- Dry-bulk carriers ----------------------------------------------
    ("dry bulk", "chokepoint"): (
        +1,
        "Chokepoint reroutes lengthen dry-bulk voyages and absorb bulker "
        "tonnage, tightening the bulk market — supportive of bulk rates.",
    ),
    ("dry bulk", "congestion"): (
        +1,
        "Port congestion idles bulk tonnage and extends voyage times, "
        "tightening bulker availability — supportive of bulk rates.",
    ),
    ("dry bulk", "rate"): (
        +1,
        "A freight-rate dislocation on a bulk-relevant lane signals scarcity "
        "pricing in the dry-bulk market.",
    ),
    ("dry bulk", "weather"): (
        +1,
        "Weather disruption causes short, localised bulker-capacity loss — "
        "mildly supportive of bulk rates.",
    ),
    ("dry bulk", "vulnerability"): (
        0,
        "Stress is dominated by the lane's structural fragility with no acute "
        "trigger — a latent risk rather than a directional catalyst.",
    ),
}

# Fuel-cost overlay. The USO ETF (bunker-fuel proxy) is tracked by
# exposure_matrix as a *cost* signal, not a cargo-demand signal. A rising USO
# is bearish for carrier margins for BOTH sectors; this rule documents that
# channel explicitly. ``_apply_fuel_overlay`` consults it when the USO ETF is
# present in stock_data.
_FUEL_COST_RULE: tuple[int, str] = (
    -1,
    "Bunker fuel (USO) has risen over the past 30 days — roughly 20-30% of "
    "shipping operating cost — a margin headwind for carriers regardless of "
    "the dominant disruption driver.",
)

# Magnitude scaling applied to a dry-bulk company's *physical-driver* cascade
# (chokepoint / congestion / weather / rate). Dry-bulk economics run primarily
# through commodity demand, so the direct route-stress channel is dampened
# relative to container liners. Documented and explicit — not learned.
_DRY_BULK_DRIVER_DAMPEN: float = 0.65

# 30-day USO move (fractional) beyond which the fuel-cost overlay engages.
# Matches the 3% dead-band used elsewhere (commodity_shipping, exposure_matrix).
_FUEL_OVERLAY_THRESHOLD: float = 0.03


# ---------------------------------------------------------------------------
# Conviction model — transparent weighted sum, per-driver weight sets
# ---------------------------------------------------------------------------
# conviction_score = sum of four named, normalised-to-[0,1] terms, each scaled
# by a published weight. The weights in any one set sum to 1.0 so the score
# itself lands in [0, 1] without further clamping (a defensive clamp is still
# applied). Every term — and the *name* of the weight set used — is emitted as
# a human-readable string in supporting_signals so the number is fully
# decomposable.
#
# Why per-driver weight sets, not one fixed set
# ----------------------------------------------
# The four terms do not deserve equal trust across every disruption type:
#
#   * A *chokepoint*-driven idea is a physical-capacity story. The summed
#     cascade magnitude is the load-bearing evidence, so the "cascade" weight
#     is lifted and the softer corroboration terms are trimmed.
#   * A *rate*-dislocation idea is a price signal that can be noisy on its own
#     — a rate move genuinely wants independent confirmation, so "agreement"
#     and "etf_confirm" are lifted at the cascade term's expense.
#   * A *congestion* idea is an operational story sitting between the two —
#     the balanced "default" set is used.
#   * A *weather* idea is short-lived; its cascade magnitude overstates a
#     transient event, so cascade is trimmed and corroboration leaned on.
#   * A *vulnerability*-driven idea has no acute trigger — structural fragility
#     is the whole story, so the "vulnerability" weight is lifted.
#
# Each set is hand-authored and asserted to sum to 1.0 below. There is no
# fitting and no hidden weighting — selection is a plain dictionary lookup on
# the normalised driver key, and the chosen key is surfaced in the output.
#
# Term keys (stable):
#   cascade       — how large the summed cascade contribution is
#   agreement     — how many independent signals point the same way
#   etf_confirm   — commodity-ETF move confirms the direction
#   vulnerability — persistence-weighted structural fragility of the routes
_CONVICTION_WEIGHT_SETS: dict[str, dict[str, float]] = {
    # Balanced set — the historical weights; used for congestion and as the
    # fallback whenever a driver has no dedicated set.
    "default": {
        "cascade":        0.42,
        "agreement":      0.22,
        "etf_confirm":    0.20,
        "vulnerability":  0.16,
    },
    # Chokepoint reroute — a physical-capacity story; trust the cascade most.
    "chokepoint": {
        "cascade":        0.52,
        "agreement":      0.18,
        "etf_confirm":    0.16,
        "vulnerability":  0.14,
    },
    # Freight-rate dislocation — a price signal; demand independent agreement.
    "rate": {
        "cascade":        0.30,
        "agreement":      0.30,
        "etf_confirm":    0.28,
        "vulnerability":  0.12,
    },
    # Weather — short-lived; the cascade magnitude overstates a transient hit,
    # so lean on corroboration instead.
    "weather": {
        "cascade":        0.34,
        "agreement":      0.26,
        "etf_confirm":    0.24,
        "vulnerability":  0.16,
    },
    # Structural vulnerability — no acute trigger; fragility is the whole story.
    "vulnerability": {
        "cascade":        0.34,
        "agreement":      0.20,
        "etf_confirm":    0.16,
        "vulnerability":  0.30,
    },
}
# Every published weight set must sum to 1.0 — the conviction-score [0, 1]
# bound depends on it. ValueError (not assert) so the invariant survives ``-O``.
for _set_name, _set in _CONVICTION_WEIGHT_SETS.items():
    if abs(sum(_set.values()) - 1.0) >= 1e-9:
        raise ValueError(
            f"disruption_cascade _CONVICTION_WEIGHT_SETS['{_set_name}'] "
            f"must sum to 1.0"
        )

# Driver key -> conviction weight-set name. Any driver without an explicit
# mapping (including the "" empty-cascade case) uses the "default" set.
_DRIVER_WEIGHT_SET: dict[str, str] = {
    "chokepoint":    "chokepoint",
    "congestion":    "default",
    "weather":       "weather",
    "rate":          "rate",
    "vulnerability": "vulnerability",
}

# Backward-compatible alias. Earlier code (and external callers / tests) read
# the single fixed weight set as ``_CONVICTION_WEIGHTS``; that name now refers
# to the balanced "default" set so the historical import keeps working.
_CONVICTION_WEIGHTS: dict[str, float] = _CONVICTION_WEIGHT_SETS["default"]


def _conviction_weights_for(driver_key: str) -> tuple[str, dict[str, float]]:
    """Return ``(set_name, weights)`` — the conviction weight set for *driver_key*.

    A plain dictionary lookup: the driver key selects a named set from
    :data:`_CONVICTION_WEIGHT_SETS`, defaulting to the balanced ``"default"``
    set for any unmapped or empty driver. Transparent by construction — the
    returned ``set_name`` is surfaced verbatim in ``supporting_signals``.
    """
    set_name = _DRIVER_WEIGHT_SET.get(driver_key, "default")
    return set_name, _CONVICTION_WEIGHT_SETS[set_name]


# ---------------------------------------------------------------------------
# Stress-persistence factors — mean-reversion awareness for the vuln term
# ---------------------------------------------------------------------------
# The route-vulnerability conviction term measures structural lane fragility.
# But fragility only *earns conviction* in proportion to how long the stress
# is likely to last: a fragile lane hit by a thunderstorm reverts within days,
# whereas the same lane behind a closed chokepoint stays stressed for months.
#
# _DRIVER_PERSISTENCE is a hand-authored multiplier in (0, 1] applied to the
# vulnerability term, keyed by the dominant driver:
#
#   * chokepoint    — a closure / reroute is slow to resolve; near-full credit.
#   * vulnerability — structural fragility is by definition persistent.
#   * congestion    — port backlogs clear over weeks; moderate persistence.
#   * rate          — a freight-rate dislocation mean-reverts as capacity
#                     responds; modest persistence.
#   * weather       — fast-reverting by nature; the smallest credit.
#
# This is documented and explicit — a published constant, not a fitted decay.
# It only ever *discounts* the vulnerability term (all factors <= 1.0), so it
# can never inflate conviction.
_DRIVER_PERSISTENCE: dict[str, float] = {
    "chokepoint":    0.95,
    "vulnerability": 1.00,
    "congestion":    0.70,
    "rate":          0.55,
    "weather":       0.30,
}
# Persistence factor for an unmapped or empty driver key — mid-range, neither
# rewarding nor heavily penalising an unclassified driver.
_DEFAULT_PERSISTENCE: float = 0.60

# Cascade magnitude that maps to a full (1.0) cascade term. The raw cascade
# magnitude is ``route_stress * cargo_share * exposure_weight`` summed over
# every hop, where ``cargo_share`` is now the *actual* per-route commodity
# share (cargo_analyzer.get_route_cargo_mix), typically 0.07-0.50 — materially
# larger than the old uniform exposure_weight/N fraction. Empirically (a
# uniform-stress sweep across every route, mirrored in the module tests) the
# magnitude scales near-linearly with system stress at roughly
# ``2.1 * mean_route_stress``: a genuinely high, fleet-wide ~0.6 stress level
# sums to ~1.2-1.5. This scale is therefore set to 1.2 so a genuinely severe
# cascade reaches a near-full term while ordinary stress lands mid-range and
# stays discriminating. Documented and explicit — calibrated to the cargo-mix
# magnitude scale, not learned.
_CASCADE_FULL_SCALE: float = 1.2

# Number of agreeing supporting signals that maps to a full agreement term.
_AGREEMENT_FULL_COUNT: int = 4

# Conviction-band thresholds — (lower-bound-inclusive, label). Checked high to
# low; the first band whose bound the score meets wins.
_CONVICTION_BANDS: list[tuple[float, str]] = [
    (0.66, "High"),
    (0.42, "Moderate"),
    (0.22, "Low"),
    (0.0,  "Watch"),
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CascadeLink:
    """One traceable hop in a company's disruption cascade.

    A link answers: *this route, carrying this commodity, with this much
    company exposure, contributes this much to the idea.* Every field is a
    visible factor of ``contribution`` — nothing is hidden.
    """

    route_id: str            # registry route_id of the stressed lane
    route_stress: float      # [0, 1] the lane's SSI stress_score
    hs_category: str         # HS cargo category carried on the lane
    cargo_share: float       # [0, 1] share of that commodity on the route
    commodity_signal: str    # "Bullish" | "Bearish" | "Neutral" ETF read
    contribution: float      # route_stress * cargo_share * exposure_weight


@dataclass
class EquityIdea:
    """A ranked, fully traceable candidate equity idea for one shipping ticker.

    Framed as Bullish / Bearish / Neutral with an explicit rationale — never a
    Buy/Sell call and never a price target. Every element of the conviction
    score is reproduced in ``supporting_signals``; the full reasoning path is
    reproduced hop-by-hop in ``cascade_chain``.
    """

    ticker: str
    company_name: str
    direction: str                       # "Bullish" | "Bearish" | "Neutral"
    conviction_score: float              # [0, 1] transparent weighted sum
    conviction_label: str                # "High" | "Moderate" | "Low" | "Watch"
    thesis: str                          # plain-language idea summary
    cascade_chain: list[CascadeLink] = field(default_factory=list)
    driving_routes: list[str] = field(default_factory=list)        # route_ids
    driving_commodities: list[str] = field(default_factory=list)   # hs_categories
    supporting_signals: list[str] = field(default_factory=list)    # decomposed terms
    risk_flags: list[str] = field(default_factory=list)            # caveats
    price: float = 0.0                   # latest close (0.0 if unavailable)
    change_30d: float = 0.0              # 30-day % move (0.0 if unavailable)
    generated_at: str = ""               # ISO 8601 UTC generation time
    conviction_weight_set: str = "default"  # named _CONVICTION_WEIGHT_SETS key used
    dominant_driver_key: str = ""        # normalised driver key (see _driver_key)


# ---------------------------------------------------------------------------
# Internal numeric helper
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into ``[lo, hi]``."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Stress-report indexing
# ---------------------------------------------------------------------------

def _route_stress_index(stress_report) -> dict[str, object]:
    """Index a ``ShippingStressReport`` by route_id -> ``RouteStress``.

    Tolerates ``None``, a report with no ``route_stress`` attribute, or an
    empty list — all yield an empty index so callers degrade to neutral.
    """
    if stress_report is None:
        return {}
    route_stress = getattr(stress_report, "route_stress", None)
    if not route_stress:
        return {}
    index: dict[str, object] = {}
    for rs in route_stress:
        rid = getattr(rs, "route_id", None)
        if rid:
            index[rid] = rs
    return index


def _exposure_by_category(exposure_matrix) -> dict[str, object]:
    """Index an exposure matrix by hs_category -> ``CommodityExposure``.

    Tolerates ``None`` and an empty list — both yield an empty index.
    """
    if not exposure_matrix:
        return {}
    index: dict[str, object] = {}
    for ce in exposure_matrix:
        cat = getattr(ce, "hs_category", None)
        if cat:
            index[cat] = ce
    return index


# ---------------------------------------------------------------------------
# Stock-data helpers
# ---------------------------------------------------------------------------

def _price_and_change(ticker: str, stock_data: dict) -> tuple[float, float]:
    """Return ``(latest_close, 30d_pct_change)`` for *ticker* from stock_data.

    ``stock_data`` maps ticker -> DataFrame[date, symbol, open, high, low,
    close, volume]. Returns ``(0.0, 0.0)`` when the ticker is absent, the
    frame is empty/malformed, or there is too little history — never raises.
    """
    if not stock_data:
        return 0.0, 0.0
    df = stock_data.get(ticker)
    if df is None:
        return 0.0, 0.0
    try:
        if getattr(df, "empty", True) or "close" not in df.columns:
            return 0.0, 0.0
        close = df["close"].dropna()
        if len(close) < 2:
            return 0.0, 0.0
        current = float(close.iloc[-1])
        prior = float(close.iloc[-31]) if len(close) >= 31 else float(close.iloc[0])
        if prior == 0:
            return current, 0.0
        return current, (current - prior) / abs(prior)
    except Exception:  # pragma: no cover - defensive
        return 0.0, 0.0


def _fuel_cost_move(stock_data: dict) -> float:
    """Return the USO (bunker-fuel proxy) 30-day fractional move.

    ``0.0`` when USO is absent from ``stock_data`` — the fuel-cost overlay then
    simply does not engage.
    """
    _price, change = _price_and_change("USO", stock_data or {})
    return change


# ---------------------------------------------------------------------------
# Per-route cargo shares — real mix with a uniform fallback
# ---------------------------------------------------------------------------

def _route_cargo_shares(
    hs_category: str,
    route_ids: list[str],
    exposure_weight: float,
) -> tuple[dict[str, float], bool]:
    """Return ``({route_id: cargo_share}, used_real_mix)`` for one commodity.

    The cargo share scales how much a given lane's stress feeds the cascade.
    The **real** share is ``cargo_analyzer.get_route_cargo_mix(route_id, {})``'s
    weight for ``hs_category`` — the genuine commodity composition of that lane.
    A route carrying more of the commodity therefore contributes proportionally
    more to the company's cascade.

    The uniform ``exposure_weight / len(route_ids)`` split is used **only** as a
    fallback, and only when the real mix cannot be obtained at all (every route
    returns an unusable mix, e.g. ``cargo_analyzer`` raised or yielded no data).
    A route whose real mix simply does not list the commodity keeps a genuine
    ``0.0`` share — it really is not carrying that cargo — and is not a reason
    to discard the real-mix path for the other routes.

    Parameters
    ----------
    hs_category:
        The HS cargo category being walked.
    route_ids:
        Registry route_ids that carry the commodity (from
        :func:`routes_for_commodity`); assumed non-empty by the caller.
    exposure_weight:
        The company's weight on the commodity — used only to size the uniform
        fallback split so the fallback matches the prior behaviour exactly.

    Returns
    -------
    tuple[dict[str, float], bool]
        Per-route cargo share in ``[0, 1]`` and a flag that is ``True`` when
        the real per-route mix was used, ``False`` when the uniform fallback
        was taken. The flag is surfaced in the idea's signals/risk flags.
    """
    real_shares: dict[str, float] = {}
    mix_available = False
    for route_id in route_ids:
        try:
            mix = cargo_analyzer.get_route_cargo_mix(route_id, {})
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "disruption_cascade: get_route_cargo_mix failed for {}",
                route_id,
            )
            continue
        if not mix:
            continue
        # A usable mix was returned for at least one route — the real-mix
        # path is viable. A commodity simply absent from the mix is a true
        # 0.0 share, not a failure.
        mix_available = True
        share = float(mix.get(hs_category, 0.0))
        real_shares[route_id] = _clamp(share)

    if mix_available:
        return real_shares, True

    # No route yielded a usable mix — fall back to the uniform 1/N split,
    # sized by the exposure weight exactly as the pre-real-mix code did.
    uniform = exposure_weight / len(route_ids)
    return {rid: uniform for rid in route_ids}, False


# ---------------------------------------------------------------------------
# Cascade walk — per company
# ---------------------------------------------------------------------------

def _company_cascade(
    ticker: str,
    stress_report,
    exposure_matrix,
    stock_data: dict,
) -> tuple[float, list[CascadeLink], str]:
    """Walk one company's disruption cascade.

    For each HS category in the company's ``COMPANY_COMMODITY_EXPOSURE`` vector
    this finds the routes that carry that commodity
    (:func:`routes_for_commodity`), pulls each route's
    ``RouteStress.stress_score`` from the stress report, and records a
    :class:`CascadeLink` per (commodity, route) hop with::

        contribution = route_stress * cargo_share * exposure_weight

    where ``exposure_weight`` is the company's weight on the commodity and
    ``cargo_share`` is that commodity's **actual** share of the route's cargo
    mix, taken from :func:`cargo_analyzer.get_route_cargo_mix` — a route
    genuinely carrying more of the commodity therefore contributes more. When
    the real per-route mix cannot be obtained for a commodity the hop falls
    back to a uniform ``1 / N`` split across that commodity's routes (the prior
    behaviour). The summed contribution is the company's raw cascade
    *magnitude* (always >= 0); the **sign** is resolved separately by
    :data:`_DIRECTION_RULES` via :func:`_resolve_direction`.

    Parameters
    ----------
    ticker:
        Shipping company ticker.
    stress_report:
        A ``ShippingStressReport`` (or ``None`` — yields a zero cascade).
    exposure_matrix:
        ``list[CommodityExposure]`` from ``exposure_matrix.build_exposure_matrix``
        (or ``None``/empty — commodity signal degrades to "Neutral").
    stock_data:
        Ticker -> price DataFrame mapping. Unused directly here but kept in the
        signature for parity with the rest of the pipeline.

    Returns
    -------
    tuple[float, list[CascadeLink], str, bool]
        ``(cascade_magnitude, cascade_links, dominant_driver_key,
        used_real_cargo_mix)`` — the summed contribution, every traceable hop
        sorted by contribution descending, the normalised driver key of the
        single highest-contribution link (``""`` when the cascade is empty),
        and a flag that is ``True`` when at least one commodity used the real
        per-route cargo mix and ``False`` when every commodity fell back to the
        uniform split.
    """
    route_index = _route_stress_index(stress_report)
    exposure_index = _exposure_by_category(exposure_matrix)
    weights = company_commodity_weights(ticker)  # always non-empty

    links: list[CascadeLink] = []
    # True once any commodity resolves to a real per-route cargo mix; False
    # only if every commodity fell back to the uniform 1/N split.
    used_real_cargo_mix = False

    # Walk every commodity the company is exposed to.
    for hs_category, exposure_weight in weights.items():
        exposure_weight = max(0.0, float(exposure_weight))
        if exposure_weight <= 0.0:
            continue

        # Commodity-demand ETF read for this category (corroboration only).
        commodity_exposure = exposure_index.get(hs_category)
        commodity_signal = (
            getattr(commodity_exposure, "direction", "Neutral")
            if commodity_exposure is not None
            else "Neutral"
        )

        # Routes that carry this commodity as a dominant cargo.
        route_ids = routes_for_commodity(hs_category)
        if not route_ids:
            continue

        # Per-route cargo share — the *actual* commodity share of each lane's
        # cargo mix (from cargo_analyzer), so a route genuinely carrying more
        # of this commodity contributes proportionally more. Falls back to the
        # uniform exposure_weight / len(route_ids) split only when no real mix
        # is available at all.
        cargo_shares, used_real_mix = _route_cargo_shares(
            hs_category, route_ids, exposure_weight
        )
        if used_real_mix:
            used_real_cargo_mix = True

        for route_id in route_ids:
            route_stress_obj = route_index.get(route_id)
            if route_stress_obj is None:
                continue
            route_stress = _clamp(
                float(getattr(route_stress_obj, "stress_score", 0.0))
            )
            if route_stress <= 0.0:
                continue

            cargo_share = _clamp(cargo_shares.get(route_id, 0.0))
            if cargo_share <= 0.0:
                # The lane carries none of this commodity — no traceable link.
                continue

            contribution = route_stress * cargo_share * exposure_weight
            if contribution <= 0.0:
                continue

            links.append(
                CascadeLink(
                    route_id=route_id,
                    route_stress=round(route_stress, 4),
                    hs_category=hs_category,
                    cargo_share=round(cargo_share, 4),
                    commodity_signal=commodity_signal,
                    contribution=round(contribution, 6),
                )
            )

    # Sort hops worst-first so the chain reads catalyst-first.
    links.sort(key=lambda lk: lk.contribution, reverse=True)

    cascade_magnitude = round(sum(lk.contribution for lk in links), 6)

    # The dominant driver is taken from the single highest-contribution link's
    # route. That route's RouteStress carries the dominant_driver label.
    dominant_driver_key = ""
    if links:
        top_route = route_index.get(links[0].route_id)
        if top_route is not None:
            dominant_driver_key = _driver_key(
                getattr(top_route, "dominant_driver", "")
            )

    # An empty cascade never exercised the real-mix path; report False so the
    # flag is not misleading.
    if not links:
        used_real_cargo_mix = False

    return cascade_magnitude, links, dominant_driver_key, used_real_cargo_mix


# ---------------------------------------------------------------------------
# Direction resolution — explicit rule lookup
# ---------------------------------------------------------------------------

def _sign_to_direction(sign: int) -> str:
    """Map a +1/-1/0 sign to its direction word."""
    if sign > 0:
        return "Bullish"
    if sign < 0:
        return "Bearish"
    return "Neutral"


def _resolve_direction(
    ticker: str,
    driver_key: str,
    cascade_magnitude: float,
    fuel_move: float,
) -> tuple[str, str, int, str, str | None]:
    """Resolve a company's idea direction from :data:`_DIRECTION_RULES`.

    Two directions are produced and both are returned:

    * the **cascade direction** — the pure sign from :data:`_DIRECTION_RULES`
      for the company's ``(sector, driver_key)``, reflecting the physical
      route-stress cascade alone. This is the direction the commodity-ETF and
      signal-agreement corroboration is measured against, so a separate cost
      overlay cannot silently erase otherwise-valid corroboration;
    * the **published direction** — the cascade direction *after* the bearish
      fuel-cost overlay. A rising USO is a carrier-margin headwind, so the
      overlay can drag a bullish cascade to Neutral or push a neutral cascade
      bearish. It never strengthens a bullish call.

    Parameters
    ----------
    ticker:
        Shipping company ticker (determines the sector archetype).
    driver_key:
        Normalised dominant stress-driver key (see :func:`_driver_key`).
    cascade_magnitude:
        The company's summed cascade contribution. A zero magnitude forces a
        Neutral idea regardless of the rule — there is no cascade to act on.
    fuel_move:
        USO 30-day fractional move. When it exceeds
        :data:`_FUEL_OVERLAY_THRESHOLD` the bearish fuel-cost overlay engages
        (see :data:`_FUEL_COST_RULE`).

    Returns
    -------
    tuple[str, str, int, str, str | None]
        ``(direction, cascade_direction, sign, rationale, fuel_note)`` where
        ``direction`` is the published Bullish/Bearish/Neutral verdict,
        ``cascade_direction`` is the pre-overlay verdict, ``sign`` is the
        published +1/-1/0 sign, ``rationale`` is the rule's plain-language
        justification, and ``fuel_note`` is the fuel-overlay rationale string
        when the overlay engaged, else ``None``.
    """
    sector = _company_sector(ticker)

    # No cascade -> nothing to act on. Still report a fuel note if relevant so
    # the idea explains why it is flat.
    if cascade_magnitude <= 0.0:
        no_cascade = (
            "No stressed route carries this company's cargo mix — no "
            "directional cascade signal."
        )
        if fuel_move > _FUEL_OVERLAY_THRESHOLD:
            _fsign, fnote = _FUEL_COST_RULE
            return "Neutral", "Neutral", 0, no_cascade, fnote
        return "Neutral", "Neutral", 0, no_cascade, None

    rule = _DIRECTION_RULES.get((sector, driver_key))
    if rule is None:
        # Unknown (sector, driver) pair — degrade to Neutral, never guess.
        logger.debug(
            "disruption_cascade: no _DIRECTION_RULES entry for ({}, {})",
            sector, driver_key,
        )
        unknown = (
            f"No direction rule defined for a {sector} carrier under "
            f"{driver_key}-driven stress — treated as Neutral."
        )
        return "Neutral", "Neutral", 0, unknown, None

    cascade_sign, rationale = rule
    cascade_direction = _sign_to_direction(cascade_sign)

    # Fuel-cost overlay. A rising USO is a margin headwind for both sectors.
    # It adjusts the *published* sign only — the cascade direction above is
    # left intact so corroboration is measured against the physical cascade.
    sign = cascade_sign
    fuel_note: str | None = None
    if fuel_move > _FUEL_OVERLAY_THRESHOLD:
        fuel_sign, fuel_rationale = _FUEL_COST_RULE
        fuel_note = fuel_rationale
        # The overlay can drag a bullish idea to neutral, or push a neutral
        # idea bearish — it never strengthens a bullish call.
        if sign > 0:
            sign = 0
        elif sign == 0:
            sign = fuel_sign

    return (
        _sign_to_direction(sign),
        cascade_direction,
        sign,
        rationale,
        fuel_note,
    )


# ---------------------------------------------------------------------------
# Conviction scoring — transparent weighted sum
# ---------------------------------------------------------------------------

def _conviction_band(score: float) -> str:
    """Map a conviction score in ``[0, 1]`` to its label band."""
    for bound, label in _CONVICTION_BANDS:
        if score >= bound:
            return label
    return _CONVICTION_BANDS[-1][1]


def _signal_agreement(
    cascade_direction: str,
    links: list[CascadeLink],
    commodity_corroborates: bool,
    insight_corroborates: bool,
) -> tuple[int, list[str]]:
    """Count independent signals agreeing with the *cascade* direction.

    Agreement is judged against the pre-overlay cascade direction so that a
    rising-fuel cost overlay (which is folded into the *published* direction)
    does not silently erase corroboration that genuinely exists.

    Each agreeing signal also yields a human-readable note for
    ``supporting_signals``. The signals counted:

    * the cascade itself (a non-empty chain always agrees with its own
      direction — counted once);
    * a commodity-ETF read pointing the same way;
    * a corroborating ``engine.scorer`` insight naming the ticker.

    Returns ``(count, notes)``.
    """
    count = 0
    notes: list[str] = []

    if cascade_direction != "Neutral" and links:
        count += 1
        notes.append(
            f"Route-stress cascade: {len(links)} stressed-lane "
            f"hop(s) point {cascade_direction.lower()}."
        )
    if cascade_direction != "Neutral" and commodity_corroborates:
        count += 1
        notes.append(
            f"Commodity-ETF signal agrees: a driving commodity's ETF move "
            f"corroborates the {cascade_direction.lower()} cascade."
        )
    if insight_corroborates:
        count += 1
        notes.append(
            "Decision-engine insight independently flags this ticker as "
            "potentially affected (corroborating, not required)."
        )
    return count, notes


def _score_one_idea(
    ticker: str,
    stress_report,
    exposure_index: dict[str, object],
    stock_data: dict,
    fuel_move: float,
    insight_tickers: frozenset[str],
) -> EquityIdea:
    """Build a single :class:`EquityIdea` for *ticker*.

    Orchestrates the cascade walk, direction resolution and transparent
    conviction sum, then assembles the idea. Always returns a valid idea — a
    company with no stressed-route exposure yields a Neutral, low-conviction
    Watch idea rather than being dropped.
    """
    profile = COMPANY_PROFILES.get(ticker, {})
    company_name = str(profile.get("name", ticker))
    price, change_30d = _price_and_change(ticker, stock_data)

    # ---- 1. Walk the cascade --------------------------------------------
    cascade_magnitude, links, driver_key, used_real_cargo_mix = _company_cascade(
        ticker, stress_report, exposure_matrix=list(exposure_index.values()),
        stock_data=stock_data,
    )

    # Per-driver conviction weight set — a documented, published set chosen by
    # a plain lookup on the dominant driver (chokepoint ideas up-weight cascade
    # magnitude, rate ideas up-weight signal agreement, etc.). The set name is
    # surfaced in supporting_signals so the weighting stays fully transparent.
    weight_set_name, conviction_weights = _conviction_weights_for(driver_key)

    # Stress-persistence factor for the dominant driver — discounts the
    # vulnerability term for fast-reverting stress (weather) and leaves it
    # near-intact for slow-reverting structural stress (a chokepoint closure).
    persistence = _DRIVER_PERSISTENCE.get(driver_key, _DEFAULT_PERSISTENCE)

    # ---- 2. Resolve direction from the explicit rule table --------------
    # ``direction`` is the published verdict (after the fuel-cost overlay);
    # ``cascade_direction`` is the pre-overlay physical-cascade verdict, used
    # below for corroboration so the fuel overlay cannot erase valid ETF /
    # insight agreement.
    (
        direction,
        cascade_direction,
        _sign,
        rule_rationale,
        fuel_note,
    ) = _resolve_direction(ticker, driver_key, cascade_magnitude, fuel_move)

    # Driving routes / commodities — de-duplicated, order-preserving.
    driving_routes: list[str] = []
    driving_commodities: list[str] = []
    for lk in links:
        if lk.route_id not in driving_routes:
            driving_routes.append(lk.route_id)
        if lk.hs_category not in driving_commodities:
            driving_commodities.append(lk.hs_category)

    # ---- 3. Conviction — transparent weighted sum -----------------------
    # Term A: cascade magnitude, normalised against a full-scale constant.
    cascade_term = _clamp(cascade_magnitude / _CASCADE_FULL_SCALE)

    # Term D: route vulnerability — mean structural fragility of the driving
    # routes, then weighted by stress persistence. A fragile lane only earns
    # conviction in proportion to how long its stress is likely to last:
    # weather reverts in days, a chokepoint closure persists for months. The
    # raw mean fragility (``vulnerability_fragility``) and the persistence-
    # weighted term that actually enters the sum (``vulnerability_term``) are
    # both surfaced so the discount is auditable.
    route_index = _route_stress_index(stress_report)
    vuln_values = [
        _clamp(float(getattr(route_index.get(rid), "vulnerability", 0.0)))
        for rid in driving_routes
        if route_index.get(rid) is not None
    ]
    vulnerability_fragility = (
        sum(vuln_values) / len(vuln_values) if vuln_values else 0.0
    )
    vulnerability_term = _clamp(vulnerability_fragility * persistence)

    # Term C: commodity-ETF confirmation — does a driving commodity's ETF move
    # agree with the *cascade* direction? Corroboration, surfaced explicitly.
    # Judged against cascade_direction (not the fuel-adjusted published one) so
    # a separate cost overlay cannot zero out genuine demand-side confirmation.
    commodity_corroborates = False
    for hs_category in driving_commodities:
        ce = exposure_index.get(hs_category)
        if ce is None:
            continue
        ce_direction = getattr(ce, "direction", "Neutral")
        if cascade_direction != "Neutral" and ce_direction == cascade_direction:
            commodity_corroborates = True
            break
    etf_confirm_term = 1.0 if commodity_corroborates else 0.0

    # Term B: signal agreement — count of independent agreeing signals.
    insight_corroborates = ticker in insight_tickers
    agreement_count, agreement_notes = _signal_agreement(
        cascade_direction, links, commodity_corroborates, insight_corroborates
    )
    agreement_term = _clamp(agreement_count / _AGREEMENT_FULL_COUNT)

    # Weighted sum -> conviction in [0, 1]. ``conviction_weights`` is the
    # per-driver set selected above; its four weights sum to 1.0 (asserted at
    # import) so the score stays in [0, 1] before the defensive clamp.
    conviction_score = _clamp(
        conviction_weights["cascade"] * cascade_term
        + conviction_weights["agreement"] * agreement_term
        + conviction_weights["etf_confirm"] * etf_confirm_term
        + conviction_weights["vulnerability"] * vulnerability_term
    )
    # A Neutral idea has no actionable conviction — cap it low so Neutral ideas
    # never outrank a genuine directional call.
    if direction == "Neutral":
        conviction_score = min(conviction_score, 0.21)
    conviction_score = round(conviction_score, 4)
    conviction_label = _conviction_band(conviction_score)

    # ---- 4. supporting_signals — every conviction term, surfaced --------
    # The conviction score is fully decomposable from this list: the weight
    # set in use is named, each term's weight and value are printed, and the
    # cargo-share source and persistence discount are stated explicitly.
    supporting_signals: list[str] = []
    supporting_signals.append(
        f"Conviction weight set: '{weight_set_name}' "
        f"(cascade {conviction_weights['cascade']:.0%}, "
        f"agreement {conviction_weights['agreement']:.0%}, "
        f"etf-confirm {conviction_weights['etf_confirm']:.0%}, "
        f"vulnerability {conviction_weights['vulnerability']:.0%}; "
        f"sums to 1.0) — selected for "
        f"{driver_key or 'no'}-driven stress."
    )
    cargo_mix_src = (
        "actual per-route cargo mix" if used_real_cargo_mix
        else "uniform 1/N cargo-share fallback"
    )
    supporting_signals.append(
        f"Cascade magnitude {cascade_magnitude:.3f} across "
        f"{len(links)} route-commodity hop(s), each = route stress x "
        f"cargo share x exposure weight using the {cargo_mix_src} "
        f"(weight {conviction_weights['cascade']:.0%}, "
        f"term {cascade_term:.2f})."
    )
    supporting_signals.extend(agreement_notes)
    supporting_signals.append(
        f"Signal-agreement count {agreement_count} "
        f"(weight {conviction_weights['agreement']:.0%}, "
        f"term {agreement_term:.2f})."
    )
    if etf_confirm_term > 0.0:
        supporting_signals.append(
            f"Commodity-ETF confirmation present "
            f"(weight {conviction_weights['etf_confirm']:.0%}, "
            f"term {etf_confirm_term:.2f})."
        )
    else:
        supporting_signals.append(
            f"No commodity-ETF confirmation "
            f"(weight {conviction_weights['etf_confirm']:.0%}, term 0.00)."
        )
    supporting_signals.append(
        f"Driving-route vulnerability {vulnerability_term:.2f} "
        f"= mean fragility {vulnerability_fragility:.2f} x persistence "
        f"{persistence:.2f} ({driver_key or 'unclassified'} stress "
        f"{'reverts slowly' if persistence >= 0.7 else 'mean-reverts'}) "
        f"(weight {conviction_weights['vulnerability']:.0%})."
    )

    # ---- 5. risk_flags --------------------------------------------------
    risk_flags: list[str] = []
    if not links:
        risk_flags.append(
            "No stressed route currently carries this company's cargo mix — "
            "signal is dormant."
        )
    if price <= 0.0:
        risk_flags.append(
            "Price history unavailable for this ticker — momentum context "
            "could not be cross-checked."
        )
    if fuel_note is not None:
        risk_flags.append(fuel_note)
    profile_risk = str(profile.get("risk_factor", "")).strip()
    if profile_risk:
        risk_flags.append(f"Company-specific risk: {profile_risk}")
    if cascade_direction != "Neutral" and not commodity_corroborates:
        risk_flags.append(
            "Commodity-demand ETFs do not yet confirm the cascade direction — "
            "signal rests on route stress alone."
        )
    if links and not used_real_cargo_mix:
        risk_flags.append(
            "Per-route cargo shares fell back to a uniform 1/N split — real "
            "cargo-mix data was unavailable, so per-lane contributions are "
            "approximate."
        )

    # ---- 6. thesis ------------------------------------------------------
    sector = _company_sector(ticker)
    if direction == "Neutral":
        if cascade_direction != "Neutral" and fuel_note is not None:
            # A directional cascade was neutralised by the fuel-cost overlay.
            thesis = (
                f"{company_name} ({ticker}) — Neutral. The route-stress "
                f"cascade reads {cascade_direction.lower()} ({rule_rationale}), "
                f"but a rising bunker-fuel cost offsets it to a net-neutral "
                f"view. Modeled idea, not investment advice."
            )
        else:
            thesis = (
                f"{company_name} ({ticker}) — Neutral. {rule_rationale} "
                f"Modeled idea, not investment advice."
            )
    else:
        lead_route = driving_routes[0] if driving_routes else "key lanes"
        lead_commodity = driving_commodities[0] if driving_commodities else "core cargo"
        thesis = (
            f"{company_name} ({ticker}) — {direction} ({conviction_label} "
            f"conviction). As a {sector} carrier, its cascade runs through "
            f"{lead_commodity} on {lead_route} and {max(0, len(links) - 1)} "
            f"other stressed hop(s). {rule_rationale} Modeled idea framed as a "
            f"direction with rationale — not investment advice, not a price "
            f"target."
        )

    return EquityIdea(
        ticker=ticker,
        company_name=company_name,
        direction=direction,
        conviction_score=conviction_score,
        conviction_label=conviction_label,
        thesis=thesis,
        cascade_chain=links,
        driving_routes=driving_routes,
        driving_commodities=driving_commodities,
        supporting_signals=supporting_signals,
        risk_flags=risk_flags,
        price=round(price, 4),
        change_30d=round(change_30d, 4),
        generated_at=datetime.now(timezone.utc).isoformat(),
        conviction_weight_set=weight_set_name,
        dominant_driver_key=driver_key,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_equity_ideas(
    stress_report,
    exposure_matrix,
    stock_data: dict,
    insights=None,
) -> list[EquityIdea]:
    """Score one :class:`EquityIdea` per tracked shipping ticker, ranked.

    For every company in ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE`` this
    walks the cascade (:func:`_company_cascade`), resolves a direction from the
    explicit :data:`_DIRECTION_RULES` table, and computes a transparent
    weighted-sum conviction score. Ideas are returned sorted by
    ``conviction_score`` descending.

    Parameters
    ----------
    stress_report:
        A ``ShippingStressReport`` from
        ``processing.shipping_stress_index.compute_shipping_stress``. May be
        ``None`` or carry an empty ``route_stress`` list — every idea then
        degrades to Neutral / Watch rather than the function raising.
    exposure_matrix:
        ``list[CommodityExposure]`` from
        ``processing.exposure_matrix.build_exposure_matrix``. May be ``None``
        or empty — commodity signals then degrade to "Neutral" and the
        ETF-confirmation conviction term contributes 0.
    stock_data:
        Mapping ``ticker -> DataFrame[date, symbol, open, high, low, close,
        volume]``. May be empty or missing tickers — price/30-day context then
        defaults to 0.0 and a risk flag is raised. USO, when present, drives
        the bearish fuel-cost overlay.
    insights:
        Optional ``list[engine.insight.Insight]`` from
        ``engine.scorer.InsightScorer``. Used **only** for corroboration: a
        ticker appearing in any ``Insight.stocks_potentially_affected`` gains a
        supporting signal and a small conviction lift. Never required and never
        changes a direction. ``None`` disables the cross-reference.

    Returns
    -------
    list[EquityIdea]
        One idea per tracked shipping ticker, sorted by ``conviction_score``
        descending. Non-empty whenever
        ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE`` is non-empty (it is
        derived at import and always populated), even when every input is
        degenerate.
    """
    stock_data = stock_data or {}

    # Index the exposure matrix once for all companies.
    exposure_index = _exposure_by_category(exposure_matrix)

    # USO 30-day move powers the bearish fuel-cost overlay (0.0 if absent).
    fuel_move = _fuel_cost_move(stock_data)

    # Cross-reference engine insights — corroboration only. Collect every
    # ticker any insight flags as potentially affected.
    insight_tickers: set[str] = set()
    if insights:
        for ins in insights:
            try:
                for tk in getattr(ins, "stocks_potentially_affected", []) or []:
                    if tk:
                        insight_tickers.add(str(tk))
            except Exception:  # pragma: no cover - defensive
                logger.debug("disruption_cascade: skipped a malformed insight")
    insight_tickers_frozen = frozenset(insight_tickers)

    # Tracked shipping tickers — the COMPANY_COMMODITY_EXPOSURE keys (derived
    # from COMPANY_PROFILES at import). Sorted for deterministic tie-breaking.
    ideas: list[EquityIdea] = []
    for ticker in sorted(COMPANY_COMMODITY_EXPOSURE.keys()):
        try:
            idea = _score_one_idea(
                ticker,
                stress_report,
                exposure_index,
                stock_data,
                fuel_move,
                insight_tickers_frozen,
            )
        except Exception:  # pragma: no cover - defensive
            # A single bad ticker must not sink the whole list — emit a safe
            # Neutral placeholder so the ticker still appears.
            logger.exception("disruption_cascade: scoring failed for {}", ticker)
            idea = EquityIdea(
                ticker=ticker,
                company_name=str(
                    COMPANY_PROFILES.get(ticker, {}).get("name", ticker)
                ),
                direction="Neutral",
                conviction_score=0.0,
                conviction_label="Watch",
                thesis=(
                    f"{ticker} — Neutral. Idea could not be scored from the "
                    f"current inputs. Modeled idea, not investment advice."
                ),
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
        ideas.append(idea)

    # Rank by conviction descending; ticker as the deterministic tie-breaker.
    ideas.sort(key=lambda idea: (-idea.conviction_score, idea.ticker))

    logger.debug(
        "score_equity_ideas: {} ideas; top={} ({}, conviction {:.3f})",
        len(ideas),
        ideas[0].ticker if ideas else "none",
        ideas[0].direction if ideas else "n/a",
        ideas[0].conviction_score if ideas else 0.0,
    )
    return ideas


def cascade_summary(ideas: list[EquityIdea]) -> dict:
    """Summarise a list of :class:`EquityIdea` into headline counts.

    Parameters
    ----------
    ideas:
        The output of :func:`score_equity_ideas` (may be empty).

    Returns
    -------
    dict with keys:
        ``total`` (int), ``bullish_count`` (int), ``bearish_count`` (int),
        ``neutral_count`` (int), ``net_signal`` ("Bullish"|"Bearish"|"Neutral"),
        ``top_idea`` (the highest-conviction :class:`EquityIdea`, or ``None``),
        ``top_ticker`` (str), ``avg_conviction`` (float),
        ``high_conviction_count`` (int — ideas with a "High" conviction label).
    """
    total = len(ideas)
    if total == 0:
        return {
            "total": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "net_signal": "Neutral",
            "top_idea": None,
            "top_ticker": "",
            "avg_conviction": 0.0,
            "high_conviction_count": 0,
        }

    bullish = sum(1 for i in ideas if i.direction == "Bullish")
    bearish = sum(1 for i in ideas if i.direction == "Bearish")
    neutral = sum(1 for i in ideas if i.direction == "Neutral")

    if bullish > bearish:
        net_signal = "Bullish"
    elif bearish > bullish:
        net_signal = "Bearish"
    else:
        net_signal = "Neutral"

    # ideas is already conviction-sorted by score_equity_ideas, but recompute
    # the top defensively in case a caller passes an unsorted list.
    top_idea = max(ideas, key=lambda i: i.conviction_score)
    avg_conviction = round(
        sum(i.conviction_score for i in ideas) / total, 4
    )
    high_conviction = sum(1 for i in ideas if i.conviction_label == "High")

    return {
        "total": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "net_signal": net_signal,
        "top_idea": top_idea,
        "top_ticker": top_idea.ticker,
        "avg_conviction": avg_conviction,
        "high_conviction_count": high_conviction,
    }
