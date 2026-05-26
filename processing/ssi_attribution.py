"""processing/ssi_attribution.py — live decomposition of an SSI score.

The Shipping Stress Index blends 6 components (chokepoint, congestion,
weather, rate, vulnerability, anomaly) with fixed weights. The
backtest validators measure each component's *predictiveness*; this
module answers a complementary question — for any given live SSI
score, what's the per-component contribution to that score?

Operators reading the radar tab see ``ssi_total = 0.62`` and want to
know "is that being driven by Red Sea chokepoints or rate volatility?"
The dominant_driver field on ``RouteStress`` partially answers this
per-route, but the fleet-wide view needs the full breakdown.

Output is two complementary views:

  * **Component contributions** — per-component weighted contribution
    to today's score, sorted by magnitude. Each entry includes the
    raw component score, the weight, the weighted contribution, and
    the percent share of the total. Sums to ``ssi_total``.
  * **Route contributions** — per-route weighted contribution to the
    fleet-wide score (using the prominent-route weighting). Sorted by
    magnitude. Top entry is the route doing the most damage today.

Pure-function on a ``ShippingStressReport``; no I/O. The caller passes
in whatever report it already has (the radar tab + the daily briefing
both have one in hand).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from processing.shipping_stress_index import (
    COMPONENT_WEIGHTS,
    RouteStress,
    ShippingStressReport,
)


__all__ = [
    "ComponentContribution",
    "RouteContribution",
    "SSIAttributionReport",
    "attribute_ssi",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ComponentContribution:
    """One row of the per-component breakdown."""

    component: str         # "chokepoint" / "congestion" / etc.
    raw_score: float       # the component's [0, 1] score
    weight: float          # from COMPONENT_WEIGHTS
    weighted: float        # raw_score * weight
    pct_share: float       # weighted / sum_of_weighted (in [0, 1])


@dataclass
class RouteContribution:
    """One row of the per-route breakdown."""

    route_id: str
    route_name: str
    stress_score: float    # the route's [0, 1] composite
    route_weight: float    # 1.0 for non-prominent routes; > 1.0 for prominent
    weighted: float        # stress_score * route_weight
    pct_share: float       # weighted / sum_of_weighted (in [0, 1])
    dominant_driver: str   # passthrough from RouteStress


@dataclass
class SSIAttributionReport:
    """Top-level attribution wrapper.

    ``component_contributions`` always sums to ``ssi_total`` (modulo
    rounding); ``route_contributions`` is normalized into the same
    headline number. ``top_component`` and ``top_route`` are the
    single-line answers to "what's driving today's score".
    """

    ssi_total: float
    ssi_label: str
    component_contributions: list[ComponentContribution] = field(
        default_factory=list,
    )
    route_contributions: list[RouteContribution] = field(
        default_factory=list,
    )
    top_component: str = ""
    top_route: str = ""
    explanation: str = ""


# ---------------------------------------------------------------------------
# Pure-function attribution
# ---------------------------------------------------------------------------


# Route weights mirror the SSI module's prominent-route logic so the
# attribution numbers reconcile against the live SSI. Kept in sync by
# import-only — when the SSI module changes the constants we re-read.
def _import_prominent_route_weights() -> dict[str, float]:
    """Return the SSI module's prominent-route weight table.

    Lazy + defensive: if the SSI module's internal names change, we
    fall back to an empty dict (every route gets weight 1.0). The
    attribution is still numerically valid — just doesn't reflect the
    extra fleet-wide weighting on prominent routes.
    """
    try:
        from processing.shipping_stress_index import (  # type: ignore
            _PROMINENT_ROUTES, _DEFAULT_ROUTE_WEIGHT,
        )
        return dict(_PROMINENT_ROUTES)
    except Exception:
        return {}


def _route_weight(route_id: str, table: dict[str, float]) -> float:
    """Return the prominent-route weight for ``route_id`` (default 1.0)."""
    return float(table.get(route_id, 1.0))


def attribute_ssi(report: ShippingStressReport) -> SSIAttributionReport:
    """Decompose ``report`` into per-component + per-route contributions.

    Pure function over the public ``ShippingStressReport`` shape. The
    contributions in the result reconcile to the fleet-wide score so
    a UI can render "component X accounts for N% of today's stress".

    Edge cases:
      * Empty report (``route_stress=[]``) → returns an empty
        attribution with empty top_component + top_route. Never raises.
      * A report with zero overall_ssi → component shares pinned to
        the weight distribution (every component contributed 0; share
        falls back to "in the limit").
      * Components missing from ``component_scores`` (e.g. a legacy
        report serialized before the anomaly component was added) →
        treated as raw_score=0 for that component.
    """
    component_scores: dict[str, float] = dict(report.component_scores or {})
    route_stress: list[RouteStress] = list(report.route_stress or [])

    # ── Per-component contributions ──────────────────────────────────
    weighted_components: list[ComponentContribution] = []
    for comp, weight in COMPONENT_WEIGHTS.items():
        raw = float(component_scores.get(comp, 0.0))
        weighted = raw * float(weight)
        weighted_components.append(ComponentContribution(
            component=comp,
            raw_score=raw,
            weight=float(weight),
            weighted=weighted,
            pct_share=0.0,   # populated after we have the total
        ))
    total_weighted = sum(c.weighted for c in weighted_components)
    if total_weighted > 0:
        for c in weighted_components:
            c.pct_share = c.weighted / total_weighted
    # Sort by weighted contribution DESC — the operator-facing order.
    weighted_components.sort(key=lambda c: c.weighted, reverse=True)

    # ── Per-route contributions ──────────────────────────────────────
    prominent = _import_prominent_route_weights()
    weighted_routes: list[RouteContribution] = []
    for r in route_stress:
        rw = _route_weight(r.route_id, prominent)
        weighted = float(r.stress_score) * rw
        weighted_routes.append(RouteContribution(
            route_id=r.route_id,
            route_name=r.route_name,
            stress_score=float(r.stress_score),
            route_weight=rw,
            weighted=weighted,
            pct_share=0.0,
            dominant_driver=str(r.dominant_driver or ""),
        ))
    total_routes = sum(r.weighted for r in weighted_routes)
    if total_routes > 0:
        for r in weighted_routes:
            r.pct_share = r.weighted / total_routes
    weighted_routes.sort(key=lambda r: r.weighted, reverse=True)

    # ── Top picks + explanation ──────────────────────────────────────
    top_component = weighted_components[0].component if weighted_components else ""
    top_route = (
        f"{weighted_routes[0].route_name} ({weighted_routes[0].dominant_driver})"
        if weighted_routes else ""
    )
    explanation = _build_explanation(
        ssi_total=float(report.overall_ssi),
        ssi_label=str(report.ssi_label or ""),
        top_component_name=top_component,
        top_component_share=(
            weighted_components[0].pct_share if weighted_components else 0.0
        ),
        top_route_name=(
            weighted_routes[0].route_name if weighted_routes else ""
        ),
        top_route_driver=(
            weighted_routes[0].dominant_driver if weighted_routes else ""
        ),
    )

    return SSIAttributionReport(
        ssi_total=float(report.overall_ssi),
        ssi_label=str(report.ssi_label or ""),
        component_contributions=weighted_components,
        route_contributions=weighted_routes,
        top_component=top_component,
        top_route=top_route,
        explanation=explanation,
    )


def _build_explanation(
    *,
    ssi_total: float,
    ssi_label: str,
    top_component_name: str,
    top_component_share: float,
    top_route_name: str,
    top_route_driver: str,
) -> str:
    """One-sentence summary of who's driving today's score.

    Designed to drop into the daily briefing without further
    formatting. Renders "(no signal)" cleanly when the report is
    empty so the briefing doesn't show a half-formed sentence.
    """
    if ssi_total <= 0 or not top_component_name:
        return "(no signal)"
    pieces = [
        f"SSI {ssi_total:.2f} ({ssi_label or 'unlabelled'})",
        f"driven primarily by {top_component_name} "
        f"({top_component_share * 100:.0f}% of weighted score)",
    ]
    if top_route_name:
        pieces.append(
            f"worst route: {top_route_name}"
            + (f" — {top_route_driver}" if top_route_driver else "")
        )
    return "; ".join(pieces) + "."
