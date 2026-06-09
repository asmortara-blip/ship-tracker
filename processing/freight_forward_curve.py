"""R042 — MODELED freight-forward (FFA) term structure.

This module builds a **MODELED** forward freight curve from REAL inputs. It is
NOT a live FFA print from the Baltic Exchange and must never be presented as
one. It is a cross-check / fair-value curve: a deterministic, documented map
from observable spot + momentum + the fleet orderbook onto a per-tenor forward
level, so the desk can compare a modeled fair-value curve against (when it
lands) a real FFA quote and trade the gap.

Why a modeled curve is honest *and* tradeable
----------------------------------------------
A forward freight curve has economic structure even without a live quote:

  * **Mean reversion of spot momentum.** Freight spot is strongly
    mean-reverting. After a sharp rally (high momentum), forwards sit *below*
    spot (the market does not expect the spike to hold) → **BACKWARDATION**,
    and rolling down the curve earns a negative carry for a long-spot holder
    — i.e. a *fade* signal on the front. After weakness (low momentum),
    forwards sit *above* spot → **CONTANGO**.

  * **Orderbook supply.** The fleet orderbook is contracted future supply with
    a known delivery schedule. Heavy near-term deliveries add capacity ahead →
    downward pressure on the *back* of the curve → more contango-ish / steeper
    discount in the deferred tenors. This is a real, observable supply signal
    (Clarksons/Alphaliner orderbook), independent of the momentum leg.

Both legs are conservative, bounded, and deterministic. The output carries a
``DataSource`` stamped MODELED so every consumer renders the honest provenance.

Heuristics (documented, conservative, deterministic)
----------------------------------------------------
Inputs:
  * ``spot``        — REAL current spot level (e.g. BDI points, or a $/day TC
                      rate). Anchors the front of the curve.
  * ``momentum``    — REAL momentum score in ``[0, 1]`` (0.5 = neutral), the
                      same convention as ``routes.rate_estimator.compute_rate_momentum``
                      and ``data.fred_feed.compute_bdi_score``. >0.5 = recent
                      rally; <0.5 = recent weakness.
  * ``orderbook_deliveries`` — a forward-looking supply-pressure scalar in
                      ``[0, 1]`` (0 = no incoming supply, 1 = heavy near-term
                      deliveries). Callers derive this from the orderbook (see
                      ``orderbook_pressure_from_fleet``). Heavy incoming supply
                      caps how high spot can hold → spot is the *expensive*
                      point and forwards sit ABOVE it → steeper **contango**.

Per-tenor forward level (for tenor ``t`` months, longest tenor ``T``):

    momentum_tilt   = (momentum - 0.5) * 2          # ∈ [-1, +1]; +rally, -weak
    supply_tilt     = orderbook_deliveries          # ∈ [ 0, +1]; supply ahead

    # Fraction of the way out the curve, 0 at front → 1 at the longest tenor.
    depth           = t / T                          # ∈ (0, 1]

    # Momentum leg: a rally pulls forwards DOWN (backwardation); weakness pushes
    # them UP (contango). Capped at ±_MAX_MOMENTUM_TILT of spot at the long end.
    momentum_adj    = -momentum_tilt * _MAX_MOMENTUM_TILT * depth

    # Supply leg: a heavy orderbook means contracted future capacity ahead. With
    # supply landing, a tight spot is not expected to hold — the deferred tenors
    # price the looser balance and the curve slopes UP off spot (contango). A
    # one-sided UPWARD push that bites the deferred tenors hardest. Capped at
    # _MAX_SUPPLY_DRAG of spot.
    supply_adj      = +supply_tilt * _MAX_SUPPLY_DRAG * depth

    forward(t)      = spot * (1 + momentum_adj + supply_adj)

So:
  * Strong rally (momentum→1) + light orderbook → forwards < spot →
    **BACKWARDATION** (a long-spot holder rolling down the curve loses → fade).
  * Weak spot (momentum→0) + heavy near-term deliveries → forwards > spot,
    steepening into the back → **CONTANGO** (momentum + supply reinforce).
  * Neutral momentum (0.5) + no orderbook → flat curve at spot.

Note the two legs can OPPOSE: a rally (momentum pulls down) into a heavy
orderbook (supply pushes up) can net to a near-FLAT curve — an honest 'the
spike will be met by deliveries' read. The shape is labelled FLAT in that
band rather than forcing a direction.

Carry metrics:
  * ``basis``       = spot − front_forward (nearest tenor). >0 = backwardation.
  * ``roll_yield``  = annualized % carry from rolling the front tenor down (or
                      up) the curve toward spot, sign-consistent with the shape:
                          roll_yield = (spot / front_forward - 1) / front_tenor_yr
                      A **positive** roll yield with backwardation = a long
                      forward rolls UP toward a higher spot (carry to a long
                      forward) — but for a long *spot* holder this same
                      backwardation is the *fade* signal (spot expected to fall
                      to the forward). The label disambiguates.

Everything is pure (no streamlit/plotly), deterministic, and degenerate-safe:
empty / non-finite / non-positive spot, or no tenors → a FLAT curve at spot
(or at 0.0 with a clear label) with NO crash.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from data.quality import DataSource


# ── Tunable, documented caps (fractions of spot) ──────────────────────────────
# Conservative: at the long end, a maxed-out rally bends the curve at most
# 8% below spot; a maxed-out orderbook lifts the deferred tenors at most 6%
# above spot. These keep the modeled curve sober — it is a cross-check, not a
# punt.
_MAX_MOMENTUM_TILT: float = 0.08   # ±8% of spot at the longest tenor
_MAX_SUPPLY_DRAG: float = 0.06     # up to +6% of spot at the longest tenor

# Backwardation/contango is only LABELLED when the basis clears this fraction of
# spot, so a numerically-flat curve does not get a misleading directional label.
_FLAT_BAND: float = 0.002          # 0.2% of spot

_DEFAULT_TENORS: tuple[int, ...] = (1, 3, 6, 12)   # months

# Provenance — always MODELED. This is a fair-value term structure, not a quote.
MODELED_CURVE_SOURCE = DataSource(
    name="Modeled FFA term structure (spot momentum + fleet orderbook)",
    kind="modeled",
    quality="modeled",
    notes=(
        "MODELED forward curve — NOT a live FFA print. Front anchored on real "
        "spot; shape from real momentum (mean-reversion → backwardation/contango) "
        "and the real fleet orderbook delivery schedule (incoming supply → back-end "
        "discount). Caps ±8% momentum / −6% supply at the 12m tenor. Cross-check "
        "only; replace with a live Baltic FFA feed when it lands."
    ),
)


@dataclass(frozen=True)
class ForwardCurve:
    """A MODELED freight-forward term structure.

    Attributes
    ----------
    spot:
        The real spot level the front of the curve is anchored on.
    tenors_months:
        The tenors (months) the curve is sampled at, ascending.
    forwards:
        Per-tenor modeled forward level, aligned with ``tenors_months``.
    shape:
        "BACKWARDATION" | "CONTANGO" | "FLAT" — the term-structure label.
    basis:
        spot − front_forward. >0 = backwardation (spot rich vs nearest forward).
    roll_yield:
        Annualized % carry from rolling the front tenor toward spot. Signed so
        it is positive under backwardation (forward rolls up toward spot) and
        negative under contango. ``0.0`` for a flat/degenerate curve.
    momentum:
        The momentum score used ([0,1], 0.5 neutral) — echoed for transparency.
    orderbook_pressure:
        The supply-pressure scalar used ([0,1]) — echoed for transparency.
    fade_signal:
        True when the shape is BACKWARDATION and the rally is sharp enough that
        a long-SPOT holder should fade (spot expected to revert down to the
        forwards). This is the tradeable read on the front.
    source:
        A MODELED ``DataSource`` — render this so the curve is never mistaken
        for a live FFA quote.
    degenerate:
        True when inputs were empty/non-finite and the curve fell back to flat.
    """
    spot: float
    tenors_months: tuple[int, ...]
    forwards: tuple[float, ...]
    shape: str
    basis: float
    roll_yield: float
    momentum: float
    orderbook_pressure: float
    fade_signal: bool
    source: DataSource = MODELED_CURVE_SOURCE
    degenerate: bool = False

    def as_points(self) -> list[tuple[int, float]]:
        """Return ``[(tenor_months, forward_level), ...]`` for plotting."""
        return list(zip(self.tenors_months, self.forwards))


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def orderbook_pressure_from_fleet(
    deliveries_next_12m_teu_m: float,
    total_teu_capacity_m: float,
) -> float:
    """Map an orderbook delivery schedule to a supply-pressure scalar in [0, 1].

    Heuristic: the 12-month delivery ratio (incoming TEU / deployed TEU) scaled
    so that a ~12.5% one-year delivery wave saturates to full pressure (1.0).
    That threshold is deliberately conservative — modern orderbooks running
    ~10-11% net supply growth (see ``processing.fleet_tracker.FLEET_2025``)
    already register as heavy back-end pressure. Returns 0.0 on bad inputs.

        pressure = clamp( deliveries_12m / capacity / 0.125 , 0, 1 )

    Pure; never raises.
    """
    if not (_finite(deliveries_next_12m_teu_m) and _finite(total_teu_capacity_m)):
        return 0.0
    cap = float(total_teu_capacity_m)
    if cap <= 0:
        return 0.0
    delivery_ratio = max(0.0, float(deliveries_next_12m_teu_m)) / cap
    return _clamp01(delivery_ratio / 0.125)


def build_forward_curve(
    spot: float,
    momentum: float,
    orderbook_deliveries: float,
    *,
    tenors: tuple[int, ...] = _DEFAULT_TENORS,
) -> ForwardCurve:
    """Build a MODELED freight-forward term structure from real inputs.

    Parameters
    ----------
    spot:
        REAL current spot level (BDI points, or a $/day TC rate). Anchors the
        front of the curve. Non-finite/non-positive → a degenerate flat curve.
    momentum:
        REAL momentum score in ``[0, 1]`` (0.5 neutral; >0.5 rally, <0.5 weak).
        Same convention as ``routes.rate_estimator.compute_rate_momentum`` /
        ``data.fred_feed.compute_bdi_score``. Clamped to [0, 1]; non-finite →
        treated as neutral (0.5).
    orderbook_deliveries:
        Supply-pressure scalar in ``[0, 1]`` (0 none → 1 heavy near-term
        deliveries). Derive from the fleet orderbook via
        ``orderbook_pressure_from_fleet``. Clamped to [0, 1]; non-finite → 0.0.
    tenors:
        Tenors in months, ascending. Empty/None → the default (1, 3, 6, 12).

    Returns
    -------
    ForwardCurve
        Per-tenor levels + shape label + basis + (annualized) roll yield +
        fade signal + a MODELED provenance stamp. See the module docstring for
        the full momentum→shape and orderbook→back-end heuristics. Never raises.
    """
    # ── Degenerate-safe normalization ────────────────────────────────────────
    tenor_list = [int(t) for t in (tenors or ()) if _finite(t) and float(t) > 0]
    tenor_list = sorted(set(tenor_list))
    if not tenor_list:
        tenor_list = list(_DEFAULT_TENORS)

    mom = _clamp01(momentum) if _finite(momentum) else 0.5
    supply = _clamp01(orderbook_deliveries) if _finite(orderbook_deliveries) else 0.0

    # Bad spot → an honest, crash-free flat curve at spot (or 0.0).
    if not _finite(spot) or float(spot) <= 0:
        s = float(spot) if (_finite(spot) and float(spot) > 0) else 0.0
        flat = tuple(s for _ in tenor_list)
        return ForwardCurve(
            spot=s,
            tenors_months=tuple(tenor_list),
            forwards=flat,
            shape="FLAT",
            basis=0.0,
            roll_yield=0.0,
            momentum=mom,
            orderbook_pressure=supply,
            fade_signal=False,
            source=MODELED_CURVE_SOURCE,
            degenerate=True,
        )

    s = float(spot)
    longest = float(tenor_list[-1])

    # momentum_tilt ∈ [-1, +1]: +rally pulls forwards DOWN; -weakness pushes UP.
    momentum_tilt = (mom - 0.5) * 2.0
    supply_tilt = supply  # one-sided UPWARD push (contango from incoming supply)

    forwards: list[float] = []
    for t in tenor_list:
        depth = float(t) / longest  # 0..1, deepest at the long end
        # rally → forwards DOWN (backwardation); orderbook supply → forwards UP
        # (contango). Both bite the deferred tenors hardest (× depth).
        momentum_adj = -momentum_tilt * _MAX_MOMENTUM_TILT * depth
        supply_adj = +supply_tilt * _MAX_SUPPLY_DRAG * depth
        fwd = s * (1.0 + momentum_adj + supply_adj)
        forwards.append(max(0.0, fwd))

    front_fwd = forwards[0]
    front_tenor_yr = tenor_list[0] / 12.0

    basis = s - front_fwd  # >0 = backwardation (spot rich vs nearest forward)

    # Shape label only when the basis clears the flat band (avoid mislabeling a
    # numerically-flat curve).
    flat_threshold = _FLAT_BAND * s
    if basis > flat_threshold:
        shape = "BACKWARDATION"
    elif basis < -flat_threshold:
        shape = "CONTANGO"
    else:
        shape = "FLAT"

    # Annualized roll yield: carry from the front forward rolling toward spot.
    # Positive under backwardation (forward < spot → rolls up), negative under
    # contango. Guarded against a zero/degenerate front forward.
    if front_fwd > 0 and front_tenor_yr > 0 and shape != "FLAT":
        roll_yield = (s / front_fwd - 1.0) / front_tenor_yr
    else:
        roll_yield = 0.0

    # Fade signal: sharp rally + backwardation → a long-SPOT holder should fade
    # (spot is expected to revert down toward the modeled forwards). Gate on a
    # clear rally so we don't cry fade on a marginal backwardation.
    fade_signal = shape == "BACKWARDATION" and mom >= 0.65

    return ForwardCurve(
        spot=s,
        tenors_months=tuple(tenor_list),
        forwards=tuple(forwards),
        shape=shape,
        basis=basis,
        roll_yield=roll_yield,
        momentum=mom,
        orderbook_pressure=supply,
        fade_signal=fade_signal,
        source=MODELED_CURVE_SOURCE,
        degenerate=False,
    )


__all__ = [
    "ForwardCurve",
    "MODELED_CURVE_SOURCE",
    "build_forward_curve",
    "orderbook_pressure_from_fleet",
]
