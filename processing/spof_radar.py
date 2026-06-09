"""spof_radar.py — multi-dimension single-point-of-failure (SPOF) radar (rec R030).

Concentration alerting on the platform was historically PORT-only
(``company_concentration_alerts.compute_concentration_alerts``) and per-TICKER:
a book can read "diversified" across ports and across names yet still be 80 %
dependent on the Suez Canal, on Asia-East cargo origins, or on a single
carrier. This module projects ONE book's per-name weights onto FOUR risk axes
and scores Herfindahl concentration on each, so a hidden single-point-of-failure
surfaces no matter which dimension it hides in.

The four axes
-------------
* **carrier**     — the book's per-NAME weight vector itself. Each tracked
                    ticker *is* a carrier / operator, so HHI over the name
                    weights is the single-operator SPOF (one company blows up).
* **commodity**   — Σ_name weight × ``exposure_matrix.company_commodity_weights``
                    → HHI over the HS-category mix. One-cargo dependence.
* **origin**      — each name's commodity mix routed onto the registry lanes that
                    carry it (``exposure_matrix.routes_for_commodity``), then
                    rolled up to each lane's ``origin_region``. One-origin
                    dependence (e.g. 90 % Asia-East sourced).
* **chokepoint**  — the same lane projection pushed through the chokepoint
                    registry (``company_concentration_alerts.chokepoint_shares``).
                    Chokepoints on a lane are SERIAL — closing ANY ONE blocks the
                    whole route — so the chokepoint score is the MAX single-
                    chokepoint exposure (the fraction of the book one closure
                    disrupts), NOT an HHI over a 1/K split. A chokepoint-free book
                    scores 0; a 90 %-one-chokepoint book scores ~0.9.

Metric per axis
---------------
* carrier / commodity / origin are PARTITIONS (shares sum to ~1) → Herfindahl
  HHI via the reused ``company_supply_risk._hhi``. Range 1/n (diversified) → 1.0
  (single bucket). A 90 %-one-bucket book scores ~0.81.
* chokepoint is SERIAL/overlapping (shares can sum to > 1) → MAX single-key
  exposure, NOT HHI (HHI is meaningless on a sum-can-exceed-1 vector).

Relationship to R040
--------------------
R040 (``company_concentration_alerts.compute_axis_concentration_alerts``) scores
the commodity / route / chokepoint axes for ONE TICKER from the registry. R030
REUSES exactly that machinery — ``chokepoint_shares``, ``_route_to_chokepoints``,
``routes_for_commodity``, ``_axis_concentration`` — but operates on the USER'S
BOOK (weighted across names) and adds the two axes R040 lacks: **carrier**
(single-name SPOF) and **origin** (cargo-origin-region SPOF). The HHI helper is
the single ``company_supply_risk._hhi`` — never reimplemented.

Fire / critical thresholds (documented, published — not fitted)
---------------------------------------------------------------
Mirrors the port-concentration ladder so operators read one scale everywhere:
* ``score >= 0.45``  → FIRE (HIGH)      — "Concentrated" band, a real SPOF.
* ``score >= 0.85``  → CRITICAL         — "Single-Point Risk" band.
* below 0.45         → no alert.

Honest provenance: every share is computed from the REAL exposure map
(``COMPANY_COMMODITY_EXPOSURE``) + the REAL chokepoint / route registries + the
user's own book weights. 0.0 means "no concentration measured", never a
fabricated risk; an empty / unpriced book yields all-zeros and fires nothing.

Pure, deterministic, never raises. No Streamlit, no I/O, no hot fetch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Reuse the single HHI helper — do NOT reimplement (constraint R030).
from processing.company_supply_risk import _hhi

# Reuse R040's axis machinery (serial-chokepoint max, registry inversion,
# partition-vs-serial metric selector) — extend, never duplicate.
from processing.company_concentration_alerts import (
    DEFAULT_CRITICAL_THRESHOLD_HHI,
    DEFAULT_FIRE_THRESHOLD_HHI,
    _axis_concentration,
    _normalize_shares,
    chokepoint_shares,
    concentration_band,
)

__all__ = [
    "SPOF_AXES",
    "SPOF_FIRE_THRESHOLD",
    "SPOF_CRITICAL_THRESHOLD",
    "AxisSpof",
    "SpofRadar",
    "compute_spof_radar",
    "spof_alerts",
]


# The four risk axes, in display order. carrier/commodity/origin are partitions
# (HHI); chokepoint is serial (max single-key exposure).
SPOF_AXES: tuple[str, ...] = ("chokepoint", "origin", "carrier", "commodity")

# Documented thresholds — mirror the port-concentration ladder (R040 defaults).
SPOF_FIRE_THRESHOLD: float = DEFAULT_FIRE_THRESHOLD_HHI       # 0.45
SPOF_CRITICAL_THRESHOLD: float = DEFAULT_CRITICAL_THRESHOLD_HHI  # 0.85


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisSpof:
    """One axis's concentration read for a book."""

    axis: str                               # one of SPOF_AXES
    score: float                            # [0, 1] — HHI (partition) or max
    #                                         single-key exposure (chokepoint)
    band: str                               # concentration_band(score)
    dominant_node: str                      # the most-exposed key on this axis
    dominant_share: float                   # that key's share [0, 1]
    n_buckets: int                          # # of distinct keys on this axis
    fired: bool                             # score >= SPOF_FIRE_THRESHOLD
    critical: bool                          # score >= SPOF_CRITICAL_THRESHOLD
    top_keys: list = field(default_factory=list)   # (key, share) desc
    metric: str = "HHI"                     # "HHI" | "max single-key exposure"


@dataclass(frozen=True)
class SpofRadar:
    """The book's SPOF read across every axis + the worst-axis summary."""

    axes: list                              # list[AxisSpof], in SPOF_AXES order
    worst_axis: str                         # axis with the highest score ("" if none)
    worst_score: float                      # that axis's score
    n_breached: int                         # # of axes at/above the fire threshold
    is_spof: bool                           # any axis fired
    summary: str = ""


# ---------------------------------------------------------------------------
# Per-axis share projections — book name-weights → axis buckets
# ---------------------------------------------------------------------------


def _book_commodity_shares(weights: dict, *, exposure: dict) -> dict:
    """Book-weighted HS-category share vector (a partition summing to ~1).

    Σ_name book_weight × company_commodity_weights(name). ``exposure`` maps
    ticker → {hs_category: weight} (the live ``COMPANY_COMMODITY_EXPOSURE``);
    unknown tickers contribute nothing (honest — no fabricated mix). Empty book
    → ``{}``.
    """
    agg: dict[str, float] = {}
    for ticker, wt in (weights or {}).items():
        w = float(wt)
        if w <= 0.0:
            continue
        vec = exposure.get(ticker) or {}
        for cat, v in vec.items():
            agg[cat] = agg.get(cat, 0.0) + w * float(v)
    return _normalize_shares(agg)


def _book_route_shares(commodity_shares: dict) -> dict:
    """Distribute each commodity share evenly across the lanes that carry it.

    Returns route_id → share (renormalized to 1 over the routes touched). Mirrors
    R040's ``_route_shares`` but takes the BOOK's commodity vector. A commodity
    that maps to no registry lane drops out (honest — no phantom route).
    """
    from processing.exposure_matrix import routes_for_commodity

    route_share: dict[str, float] = {}
    for hs, w in (commodity_shares or {}).items():
        routes = routes_for_commodity(hs)
        if not routes:
            continue
        per = float(w) / len(routes)
        for rid in routes:
            route_share[str(rid)] = route_share.get(str(rid), 0.0) + per
    return _normalize_shares(route_share)


def _book_origin_shares(route_share: dict) -> dict:
    """Roll route shares up to each lane's ``origin_region`` (a partition).

    The cargo-origin axis: which source REGION the book's flow originates in. The
    ``ShippingRoute`` registry carries ``origin_region`` (e.g. "Asia East",
    "Europe") — an honest cargo-origin granularity, not a fabricated country
    code. A route the registry doesn't know is skipped. Empty input → ``{}``.
    """
    from routes.route_registry import ROUTES_BY_ID

    origin_share: dict[str, float] = {}
    for rid, w in (route_share or {}).items():
        route = ROUTES_BY_ID.get(str(rid))
        if route is None:
            continue
        region = str(getattr(route, "origin_region", "") or "")
        if not region:
            continue
        origin_share[region] = origin_share.get(region, 0.0) + float(w)
    return _normalize_shares(origin_share)


def _axis_shares(axis: str, weights: dict, *, exposure: dict, chokepoints) -> dict:
    """Project the book's name-weights onto one axis's bucket→share map.

    * carrier    — the (normalized) name-weight vector itself.
    * commodity  — book-weighted HS-category mix.
    * origin     — commodity routed onto lanes, rolled up to origin_region.
    * chokepoint — commodity routed onto lanes, pushed through the chokepoint
                   registry (serial / overlapping; ``chokepoint_shares``).

    All projections share the commodity → route base so the four axes stay
    mutually consistent. ``chokepoints`` (the route→chokepoint inversion) is
    threaded through so callers can inject a registry for hermetic tests.
    """
    name_w = _normalize_shares(weights)
    if axis == "carrier":
        return name_w
    comm = _book_commodity_shares(weights, exposure=exposure)
    if axis == "commodity":
        return comm
    route_share = _book_route_shares(comm)
    if axis == "origin":
        return _book_origin_shares(route_share)
    if axis == "chokepoint":
        # Serial / overlapping — NOT renormalized (sum can exceed 1 by design).
        return chokepoint_shares(route_share, route_to_cp=chokepoints)
    return {}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def _route_to_chokepoints_safe() -> dict:
    """The route→[chokepoint] inversion, never raising (defensive default)."""
    try:
        from processing.company_concentration_alerts import _route_to_chokepoints
        return _route_to_chokepoints()
    except Exception:  # pragma: no cover - defensive
        return {}


def compute_spof_radar(
    book_weights: dict,
    *,
    exposure: dict | None = None,
    chokepoints: dict | None = None,
    fire_threshold: float = SPOF_FIRE_THRESHOLD,
    critical_threshold: float = SPOF_CRITICAL_THRESHOLD,
    top_in_body: int = 3,
) -> SpofRadar:
    """Score a book's single-point-of-failure concentration on every axis.

    Parameters
    ----------
    book_weights:
        ``{ticker: weight}`` — the user's book (e.g.
        ``processing.book_exposure.book_weights``). Weights are renormalized per
        axis, so they need not sum to exactly 1. An empty / all-zero book yields
        an all-zeros radar that fires nothing.
    exposure:
        ticker → {hs_category: weight} map (the commodity axis). Defaults to the
        live ``exposure_matrix.COMPANY_COMMODITY_EXPOSURE``. Injectable for
        hermetic tests.
    chokepoints:
        route_id → [chokepoint key] inversion. Defaults to the live registry
        (``company_concentration_alerts._route_to_chokepoints``). Injectable.
    fire_threshold / critical_threshold:
        Documented HHI thresholds (0.45 / 0.85 by default). ``score >= fire`` →
        ``fired``; ``score >= critical`` → ``critical``.
    top_in_body:
        How many (key, share) pairs to carry per axis for the alert body.

    Returns
    -------
    SpofRadar
        One ``AxisSpof`` per axis (in ``SPOF_AXES`` order) + a worst-axis
        summary. Pure, deterministic, never raises.
    """
    if exposure is None:
        try:
            from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE
            exposure = COMPANY_COMMODITY_EXPOSURE
        except Exception:  # pragma: no cover - defensive
            exposure = {}
    if chokepoints is None:
        chokepoints = _route_to_chokepoints_safe()

    axes_out: list[AxisSpof] = []
    for axis in SPOF_AXES:
        try:
            shares = _axis_shares(
                axis, book_weights or {}, exposure=exposure, chokepoints=chokepoints
            )
        except Exception:  # pragma: no cover - defensive; never raise
            shares = {}
        score = _axis_concentration(axis, shares) if shares else 0.0
        # Defensive clamp — _hhi/_axis_concentration are already in [0,1] but a
        # malformed injected share map should never leak an out-of-range score.
        score = max(0.0, min(1.0, float(score)))
        top = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)[
            : max(1, int(top_in_body))
        ] if shares else []
        dominant_node = top[0][0] if top else ""
        dominant_share = round(float(top[0][1]), 6) if top else 0.0
        metric = "max single-key exposure" if axis == "chokepoint" else "HHI"
        axes_out.append(AxisSpof(
            axis=axis,
            score=round(score, 6),
            band=concentration_band(score),
            dominant_node=str(dominant_node),
            dominant_share=dominant_share,
            n_buckets=len(shares),
            fired=score >= fire_threshold,
            critical=score >= critical_threshold,
            top_keys=[(str(k), round(float(s), 6)) for k, s in top],
            metric=metric,
        ))

    fired = [a for a in axes_out if a.fired]
    worst = max(axes_out, key=lambda a: a.score) if axes_out else None
    worst_axis = worst.axis if worst and worst.score > 0.0 else ""
    worst_score = worst.score if worst else 0.0
    if fired:
        worst_fired = max(fired, key=lambda a: a.score)
        summary = (
            f"SPOF on {len(fired)} axis(es); worst: {worst_fired.axis} "
            f"{worst_fired.score:.2f} ({worst_fired.band}, {worst_fired.metric}) "
            f"— dominant {worst_fired.dominant_node} "
            f"{worst_fired.dominant_share * 100:.0f}%."
        )
    else:
        summary = "No single-point-of-failure on any axis (all below fire threshold)."

    return SpofRadar(
        axes=axes_out,
        worst_axis=worst_axis,
        worst_score=worst_score,
        n_breached=len(fired),
        is_spof=bool(fired),
        summary=summary,
    )


def spof_alerts(radar: SpofRadar, *, fire_threshold: float = SPOF_FIRE_THRESHOLD):
    """One ``SPOF_DIMENSION`` ShippingAlert per breached axis (best-effort).

    Wraps ``compute_spof_radar``'s output into the platform's alert shape — the
    axis goes in ``route_id`` (reusing a dedup-keyed field so the SAME axis
    breach fires once per window, not repeatedly), the dominant node + share in
    the body. Severity follows the per-axis ``critical`` flag. Returns ``[]``
    when nothing breached. Never raises — a formatting hiccup on one axis is
    skipped, not propagated.
    """
    try:
        from engine.alert_engine_v2 import _make
    except Exception:  # pragma: no cover - defensive
        return []

    if radar is None:
        return []

    out: list = []
    for a in getattr(radar, "axes", []) or []:
        try:
            if not a.fired or a.score < fire_threshold:
                continue
            severity = "CRITICAL" if a.critical else "HIGH"
            top_str = ", ".join(
                f"{k} {s * 100:.0f}%" for k, s in (a.top_keys or [])
            )
            out.append(_make(
                alert_type="SPOF_DIMENSION",
                severity=severity,
                title=(
                    f"Book SPOF — {a.axis} concentration "
                    f"{a.score:.2f} ({a.band})"
                ),
                body=(
                    f"The book is concentrated on the {a.axis} axis: "
                    f"{a.metric} {a.score:.2f} ({a.band}) across {a.n_buckets} "
                    f"bucket(s). Dominant: {a.dominant_node} "
                    f"({a.dominant_share * 100:.0f}%). Top: {top_str}. "
                    "A single-point-of-failure disruption on this axis would hit "
                    "most of the book even if it looks port-diversified."
                ),
                route_id=a.axis,
                value=a.score,
                threshold=fire_threshold,
                change_pct=(a.score - fire_threshold) * 100.0,
            ))
        except Exception:  # pragma: no cover - defensive; skip the bad axis
            continue
    return out
